"""Aplicación WSGI de subida por ticket.

Rutas:

* ``POST /admin/tickets``  (Bearer UPLOAD_ADMIN_SECRET): crea un ticket.
* ``PUT|POST /upload/<token>``: sube un único archivo ligado al ticket.

Solo biblioteca estándar: corre bajo Passenger ("Setup Python App" de cPanel).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import mmap
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..audit import AuditLogger
from ..config import Settings
from ..cpanel import CPanelClient
from ..errors import CpanelMCPError, FileTypeNotAllowedError, UnsafePathError
from ..file_ops import (
    _check_writable_path,
    _parent_and_name,
    display_path,
    normalize_public_path,
    validate_expected_sha256,
)
from ..logging_setup import redact_text
from .ratelimit import RateLimiter
from .settings import UploadSettings
from .storage import AccountFiles
from .tickets import TicketError, TicketStore
from .validate import HEAD_BYTES, UploadRejection, check_filename, check_magic

logger = logging.getLogger(__name__)

CHUNK = 64 * 1024
MAX_ADMIN_BODY = 8 * 1024
MULTIPART_OVERHEAD = 16 * 1024

Trasher = Callable[[str, str], None]

_STATUS_TEXT = {
    200: "OK",
    201: "Created",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
}
_BOUNDARY = re.compile(r'boundary="?([^";]+)"?', re.IGNORECASE)


class CPanelTrasher:
    """Mueve un archivo existente a la papelera de cPanel (como upload_file)."""

    def __init__(self, settings: UploadSettings) -> None:
        self._settings = settings

    def __call__(self, account: str, path: str) -> None:
        cfg = self._settings.accounts[account]
        client_settings = Settings(
            host=self._settings.cpanel_host,
            user=cfg.user,
            token=cfg.token,
            port=self._settings.cpanel_port,
            verify_ssl=self._settings.verify_ssl,
        )

        async def run() -> None:
            async with CPanelClient(client_settings) as client:
                await client.trash_file(path)

        asyncio.run(run())


class UploadApp:
    def __init__(
        self,
        settings: UploadSettings,
        *,
        store: TicketStore | None = None,
        limiter: RateLimiter | None = None,
        trasher: Trasher | None = None,
        audit: AuditLogger | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.store = store or TicketStore(settings.db_path, clock=clock)
        self.limiter = limiter or RateLimiter(settings.db_path, clock=clock)
        self.trasher = trasher or CPanelTrasher(settings)
        self.audit = audit or AuditLogger(settings.audit_path, secrets=settings.secrets())
        self._clock = clock

    # ------------------------------------------------------------------ WSGI
    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        started = time.perf_counter()
        allow = None
        ip = environ.get("REMOTE_ADDR", "unknown")
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        audit_ctx: dict[str, Any] = {"ip": ip}
        action = "request"
        try:
            if self.settings.require_https and not _is_https(environ):
                raise UploadRejection(
                    403, "https_required", "Usa HTTPS: este servicio no acepta HTTP."
                )
            if path == "/admin/tickets":
                action = "create_ticket"
                if method != "POST":
                    raise _method_not_allowed("POST")
                status, body = self._create_ticket(environ, ip, audit_ctx)
            elif path.startswith("/upload/"):
                action = "upload"
                if method not in {"PUT", "POST"}:
                    raise _method_not_allowed("PUT, POST")
                status, body = self._upload(environ, path[len("/upload/"):], ip, audit_ctx)
            else:
                raise UploadRejection(404, "not_found", "Ruta no encontrada.")
            outcome, code = "success", None
        except UploadRejection as exc:
            status, outcome, code = exc.status, "error", exc.code
            body = {"error": exc.code, "message": exc.message}
            allow = getattr(exc, "allow", None)
        except Exception as exc:  # noqa: BLE001 - última barrera
            allow = None
            logger.error(
                "Error interno en %s: %s",
                action,
                redact_text(str(exc), self.settings.secrets()),
            )
            status, outcome, code = 500, "error", "internal_error"
            body = {
                "error": "internal_error",
                "message": "Error interno del servicio. Reintenta con un ticket nuevo.",
            }

        if action != "request" or outcome == "error":
            try:
                self.audit.log(
                    action=action,
                    args=audit_ctx,
                    outcome=outcome,
                    error_code=code,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    message=body.get("message") if outcome == "error" else None,
                )
            except OSError as exc:
                logger.error("No se pudo escribir la auditoría: %s", exc)

        return self._respond(start_response, status, body, allow=allow)

    # ----------------------------------------------------------- respuestas
    @staticmethod
    def _respond(start_response: Callable, status: int, body: dict[str, Any], *, allow: str | None) -> list[bytes]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if allow:
            headers.append(("Allow", allow))
        if status == 429:
            headers.append(("Retry-After", "60"))
        start_response(f"{status} {_STATUS_TEXT.get(status, 'Error')}", headers)
        return [data]

    # ---------------------------------------------------------- rate limits
    def _guard_bad_attempts(self, ip: str) -> None:
        limit, window = self.settings.limit_bad_attempts_per_ip
        if not self.limiter.check(f"bad:{ip}", limit, window):
            raise UploadRejection(
                429,
                "rate_limited",
                "Demasiados intentos fallidos desde tu IP. Espera unos minutos.",
            )

    def _rate_limit(self, key: str, rule: tuple[int, int], what: str) -> None:
        if not self.limiter.hit(key, *rule):
            raise UploadRejection(
                429,
                "rate_limited",
                f"Demasiadas solicitudes de {what}. Espera un minuto y reintenta.",
            )

    # ------------------------------------------------------------- tickets
    def _create_ticket(self, environ: dict[str, Any], ip: str, ctx: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self._guard_bad_attempts(ip)
        header = environ.get("HTTP_AUTHORIZATION", "")
        provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(
            provided.encode("utf-8"), self.settings.admin_secret.encode("utf-8")
        ):
            self.limiter.record(f"bad:{ip}")
            raise UploadRejection(
                401, "unauthorized", "Falta o es incorrecta la credencial de administración."
            )
        self._rate_limit(f"ticket-ip:{ip}", self.settings.limit_tickets_per_ip, "tickets")

        payload = _read_json(environ)
        account = payload.get("account")
        if not isinstance(account, str) or account not in self.settings.accounts:
            raise UploadRejection(
                403, "account_not_allowed", "La cuenta no está gestionada por este servicio."
            )
        ctx["account"] = account
        self._rate_limit(
            f"ticket-acct:{account}", self.settings.limit_tickets_per_account, "tickets"
        )

        normalized = self._validated_target(payload.get("path"))
        ctx["path"] = normalized

        cap = self.settings.max_bytes_cap
        max_bytes = payload.get("max_bytes", cap)
        if type(max_bytes) is not int or not 1 <= max_bytes <= cap:
            raise UploadRejection(
                400,
                "invalid_max_bytes",
                f"max_bytes debe ser un entero entre 1 y {cap} (tope del servidor).",
            )
        overwrite = payload.get("overwrite", False)
        if type(overwrite) is not bool:
            raise UploadRejection(400, "invalid_overwrite", "overwrite debe ser true o false.")
        try:
            expected = validate_expected_sha256(payload.get("expected_sha256"))
        except CpanelMCPError as exc:
            raise UploadRejection(400, "invalid_sha256", str(exc)) from None

        token, ticket = self.store.create(
            account=account,
            path=normalized,
            max_bytes=max_bytes,
            overwrite=overwrite,
            expected_sha256=expected,
            ttl_seconds=self.settings.ticket_ttl_seconds,
        )
        ctx.update(max_bytes=max_bytes, overwrite=overwrite)
        expires = datetime.fromtimestamp(ticket.expires_at, timezone.utc)
        return 201, {
            "upload_url": f"{self.settings.public_base_url}/upload/{token}",
            "method": "PUT",
            "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "path": display_path(normalized),
            "max_bytes": max_bytes,
            "overwrite": overwrite,
        }

    def _validated_target(self, raw_path: object) -> str:
        try:
            normalized = normalize_public_path(raw_path, allow_root=False)
            _, name = _parent_and_name(normalized)
            _check_writable_path(normalized, name)
        except UnsafePathError as exc:
            raise UploadRejection(400, "unsafe_path", str(exc)) from None
        except FileTypeNotAllowedError as exc:
            raise UploadRejection(415, "type_not_allowed", str(exc)) from None
        check_filename(name, allow_zip=self.settings.allow_zip)
        return normalized

    # -------------------------------------------------------------- subida
    def _upload(self, environ: dict[str, Any], token: str, ip: str, ctx: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self._rate_limit(f"upload-ip:{ip}", self.settings.limit_uploads_per_ip, "subida")
        self._guard_bad_attempts(ip)
        try:
            ticket = self.store.redeem(token)
        except TicketError as exc:
            if exc.kind == "invalid":
                self.limiter.record(f"bad:{ip}")
                raise UploadRejection(
                    404,
                    "ticket_not_found",
                    "Ticket inexistente. Crea uno nuevo con create_upload_ticket.",
                ) from None
            if exc.kind == "used":
                raise UploadRejection(
                    403,
                    "ticket_used",
                    "Este ticket ya se usó (son de un solo uso). Crea uno nuevo.",
                ) from None
            raise UploadRejection(
                403,
                "ticket_expired",
                "El ticket venció (duran 5 minutos). Crea uno nuevo y súbelo enseguida.",
            ) from None

        ctx.update(account=ticket.account, path=ticket.path)
        account = self.settings.accounts.get(ticket.account)
        if account is None:
            raise UploadRejection(
                403, "account_not_allowed", "La cuenta del ticket ya no está gestionada."
            )

        normalized = self._validated_target(ticket.path)
        _, name = _parent_and_name(normalized)
        expected_mime = check_filename(name, allow_zip=self.settings.allow_zip)

        files = AccountFiles(account.home)
        parent, _ = files.parent_dir(normalized)
        dest = parent / name
        state = files.inspect(dest)
        if state == "other":
            raise UploadRejection(
                409, "not_a_file", f"'{display_path(normalized)}' no es un archivo regular."
            )
        if state == "file" and not ticket.overwrite:
            raise UploadRejection(
                409,
                "file_exists",
                f"'{display_path(normalized)}' ya existe. Crea un ticket con "
                "overwrite=true para reemplazarlo (el original va a la papelera).",
            )

        tmp = files.new_temp()
        extracted: Path | None = None
        try:
            size, digest = _stream_body(environ, tmp, ticket.max_bytes)
            if environ.get("REQUEST_METHOD", "").upper() == "POST" and (
                environ.get("CONTENT_TYPE", "").lower().startswith("multipart/form-data")
            ):
                extracted = files.new_temp()
                size, digest = _extract_multipart(
                    tmp, environ.get("CONTENT_TYPE", ""), extracted, ticket.max_bytes
                )
                final = extracted
            else:
                final = tmp
            if size == 0:
                raise UploadRejection(400, "empty_body", "El cuerpo de la subida está vacío.")

            with open(final, "rb") as handle:
                mime = check_magic(expected_mime, handle.read(HEAD_BYTES))

            if ticket.expected_sha256 and digest != ticket.expected_sha256:
                raise UploadRejection(
                    422,
                    "sha256_mismatch",
                    f"El sha256 recibido ({digest}) no coincide con el esperado. El "
                    "archivo llegó alterado; no se guardó nada. Crea un ticket nuevo.",
                )

            if files.inspect(dest) == "file":
                if not ticket.overwrite:
                    raise UploadRejection(
                        409, "file_exists", "El archivo apareció mientras se subía."
                    )
                try:
                    self.trasher(ticket.account, normalized)
                except CpanelMCPError as exc:
                    raise UploadRejection(
                        502,
                        "trash_failed",
                        "No se pudo mover el archivo anterior a la papelera de "
                        f"cPanel; no se escribió nada. Detalle: {exc}",
                    ) from None
            files.publish(final, dest)
        finally:
            for leftover in (tmp, extracted):
                if leftover is not None:
                    try:
                        os.unlink(leftover)
                    except FileNotFoundError:
                        pass

        ctx.update(bytes=size, sha256=digest, overwrite=ticket.overwrite)
        return 201, {
            "path": display_path(normalized),
            "bytes": size,
            "sha256": digest,
            "mime": mime,
        }


# ---------------------------------------------------------------- utilidades
def _is_https(environ: dict[str, Any]) -> bool:
    return (
        environ.get("wsgi.url_scheme") == "https"
        or environ.get("HTTPS", "").lower() in {"on", "1"}
        or environ.get("HTTP_X_FORWARDED_PROTO", "").lower() == "https"
    )


def _method_not_allowed(allow: str) -> UploadRejection:
    exc = UploadRejection(405, "method_not_allowed", f"Método no permitido. Usa {allow}.")
    exc.allow = allow  # type: ignore[attr-defined]
    return exc


def _content_length(environ: dict[str, Any]) -> int | None:
    raw = environ.get("CONTENT_LENGTH", "")
    if raw in ("", None):
        return None
    try:
        value = int(raw)
    except ValueError:
        raise UploadRejection(400, "bad_content_length", "Content-Length inválido.") from None
    if value < 0:
        raise UploadRejection(400, "bad_content_length", "Content-Length inválido.")
    return value


def _read_json(environ: dict[str, Any]) -> dict[str, Any]:
    length = _content_length(environ)
    if length is None or length > MAX_ADMIN_BODY:
        raise UploadRejection(
            400, "bad_body", f"Envía un JSON de hasta {MAX_ADMIN_BODY} bytes con Content-Length."
        )
    raw = environ["wsgi.input"].read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UploadRejection(400, "bad_json", "El cuerpo no es JSON válido.") from None
    if not isinstance(data, dict):
        raise UploadRejection(400, "bad_json", "El cuerpo debe ser un objeto JSON.")
    return data


def _too_large(limit: int) -> UploadRejection:
    return UploadRejection(
        413,
        "payload_too_large",
        f"El archivo supera el máximo de {limit} bytes de este ticket. Reduce o "
        "comprime la imagen, o crea un ticket con un max_bytes mayor (hasta el "
        "tope del servidor).",
    )


def _stream_body(environ: dict[str, Any], dest: Path, limit: int) -> tuple[int, str]:
    """Copia el cuerpo a ``dest`` contando bytes mientras se lee.

    No se confía en Content-Length: el tope se aplica sobre lo realmente leído.
    En multipart se admite un pequeño margen para las cabeceras de las partes.
    """

    multipart = environ.get("REQUEST_METHOD", "").upper() == "POST" and environ.get(
        "CONTENT_TYPE", ""
    ).lower().startswith("multipart/form-data")
    cap = limit + (MULTIPART_OVERHEAD if multipart else 0)
    declared = _content_length(environ)
    if declared is not None and declared > cap:
        raise _too_large(limit)

    stream = environ["wsgi.input"]
    digest = hashlib.sha256()
    total = 0
    with open(dest, "wb") as out:
        while True:
            want = CHUNK
            if declared is not None:
                want = min(want, declared - total)
                if want <= 0:
                    break
            chunk = stream.read(min(want, cap - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise _too_large(limit)
            digest.update(chunk)
            out.write(chunk)
    return total, digest.hexdigest()


def _extract_multipart(src: Path, content_type: str, dest: Path, limit: int) -> tuple[int, str]:
    """Extrae el contenido de un multipart con exactamente una parte."""

    match = _BOUNDARY.search(content_type)
    if not match:
        raise UploadRejection(400, "bad_multipart", "Falta el boundary de multipart/form-data.")
    boundary = match.group(1).encode("ascii", "ignore")
    opening = b"--" + boundary + b"\r\n"
    delimiter = b"\r\n--" + boundary

    with open(src, "rb") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            raise UploadRejection(400, "empty_body", "El cuerpo de la subida está vacío.")
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if not data[: len(opening)] == opening:
                raise UploadRejection(400, "bad_multipart", "Multipart mal formado.")
            headers_end = data.find(b"\r\n\r\n", len(opening))
            if headers_end < 0:
                raise UploadRejection(400, "bad_multipart", "Multipart mal formado.")
            start = headers_end + 4
            end = data.rfind(delimiter)
            if (
                end < start
                or data[end + len(delimiter): end + len(delimiter) + 2] != b"--"
                or data.find(delimiter, start) != end
            ):
                raise UploadRejection(
                    400,
                    "bad_multipart",
                    "Envía un único archivo en multipart/form-data (o usa PUT).",
                )
            if end - start > limit:
                raise _too_large(limit)
            digest = hashlib.sha256()
            with open(dest, "wb") as out:
                position = start
                while position < end:
                    chunk = data[position: min(position + CHUNK, end)]
                    digest.update(chunk)
                    out.write(chunk)
                    position += len(chunk)
            return end - start, digest.hexdigest()


def create_app(settings: UploadSettings | None = None) -> UploadApp:
    """Punto de entrada para Passenger (``passenger_wsgi.py``)."""

    from .settings import load_upload_settings

    return UploadApp(settings or load_upload_settings())
