"""Herramienta MCP ``create_upload_ticket``: pide un ticket al servicio de subida."""

from __future__ import annotations

import posixpath
import shlex
from typing import Any

import httpx

from ..config import Settings
from ..errors import (
    ConfigError,
    CPanelConnectionError,
    CPanelTimeoutError,
    CpanelMCPError,
    FileOperationError,
    FileTypeNotAllowedError,
    ValidationError,
)
from ..file_ops import (
    _check_writable_path,
    _parent_and_name,
    display_path,
    normalize_public_path,
    validate_expected_sha256,
)
from .validate import UploadRejection, check_filename

TIMEOUT_SECONDS = 30.0


class UploadTicketTool:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http = http

    async def create_upload_ticket(
        self,
        path: str,
        max_bytes: int | None = None,
        overwrite: bool = False,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        base = (self._settings.upload_service_url or "").rstrip("/")
        secret = self._settings.upload_admin_secret
        if not base or not secret:
            raise ConfigError(
                "create_upload_ticket no está configurada: define "
                "UPLOAD_SERVICE_URL y UPLOAD_ADMIN_SECRET (ver docs/UPLOAD_SERVICE.md)."
            )

        normalized = normalize_public_path(path, allow_root=False)
        if type(overwrite) is not bool:
            raise ValidationError("overwrite debe ser true o false.")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ValidationError("max_bytes debe ser un entero positivo.")
        expected = validate_expected_sha256(expected_sha256)

        _, name = _parent_and_name(normalized)
        _check_writable_path(normalized, name)
        try:
            check_filename(name)
        except UploadRejection as exc:
            raise FileTypeNotAllowedError(exc.message) from None

        payload: dict[str, Any] = {
            "account": self._settings.user,
            "path": normalized,
            "overwrite": overwrite,
        }
        if max_bytes is not None:
            payload["max_bytes"] = max_bytes
        if expected is not None:
            payload["expected_sha256"] = expected

        data = await self._post(f"{base}/admin/tickets", payload, secret)
        url = data.get("upload_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise FileOperationError(
                "El servicio de subida devolvió una respuesta inesperada."
            )

        local_name = posixpath.basename(normalized)
        return {
            "upload_url": url,
            "method": "PUT",
            "expires_at": data.get("expires_at"),
            "path": display_path(normalized),
            "max_bytes": data.get("max_bytes"),
            "overwrite": overwrite,
            "curl_example": f"curl -sS -T {shlex.quote(local_name)} {shlex.quote(url)}",
            "instructions": (
                "Ejecuta el curl desde un entorno con el archivo local (el "
                "nombre del archivo local es indiferente; el destino lo fija el "
                "ticket). El ticket es de un solo uso y vence a los 5 minutos. "
                "La respuesta JSON incluye path, bytes, sha256 y mime."
            ),
        }

    async def _post(
        self, url: str, payload: dict[str, Any], secret: str
    ) -> dict[str, Any]:
        owns_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {secret}"},
            )
        except httpx.TimeoutException:
            raise CPanelTimeoutError(
                "El servicio de subida no respondió a tiempo."
            ) from None
        except httpx.HTTPError:
            raise CPanelConnectionError(
                "No se pudo conectar con el servicio de subida. Revisa "
                "UPLOAD_SERVICE_URL y que la app Python esté en marcha."
            ) from None
        finally:
            if owns_client:
                await client.aclose()

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 201:
            message = body.get("message") if isinstance(body, dict) else None
            raise CpanelMCPError(
                message
                or f"El servicio de subida respondió {response.status_code}.",
                code=(body.get("error") if isinstance(body, dict) else None)
                or "upload_service_error",
            )
        if not isinstance(body, dict):
            raise FileOperationError(
                "El servicio de subida devolvió una respuesta inesperada."
            )
        return body
