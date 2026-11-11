"""Validación de nombres y contenido de archivos subidos por ticket.

La verificación de contenido usa firmas (magic bytes) en Python puro para no
depender de libmagic en el hosting compartido.
"""

from __future__ import annotations

from ..file_ops import _BLOCKED_EXTENSIONS, _is_sensitive_name


class UploadRejection(Exception):
    """Error con estado HTTP y mensaje apto para el cliente."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


ALLOWED_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
OPTIONAL_EXTENSIONS: dict[str, str] = {".zip": "application/zip"}

# Segmentos que no pueden aparecer en ningún punto del nombre (foto.php.jpg).
BLOCKED_SEGMENTS = frozenset(
    {ext.lstrip(".") for ext in _BLOCKED_EXTENSIONS}
    | {"asp", "aspx", "jsp", "shtml", "htaccess", "htpasswd", "phps", "pht"}
)

HEAD_BYTES = 16


def allowed_extensions(allow_zip: bool) -> dict[str, str]:
    allowed = dict(ALLOWED_EXTENSIONS)
    if allow_zip:
        allowed.update(OPTIONAL_EXTENSIONS)
    return allowed


def check_filename(name: str, *, allow_zip: bool = False) -> str:
    """Valida el nombre y devuelve el MIME esperado según la extensión."""

    lower = name.lower()
    if _is_sensitive_name(name):
        raise UploadRejection(
            415,
            "type_not_allowed",
            f"'{name}' es un nombre de archivo sensible o de configuración y "
            "no se puede subir. Usa otro nombre.",
        )

    segments = lower.split(".")
    blocked = [seg for seg in segments[1:-1] if seg in BLOCKED_SEGMENTS]
    if len(segments) > 1 and segments[-1] in BLOCKED_SEGMENTS:
        blocked.append(segments[-1])
    if blocked:
        raise UploadRejection(
            415,
            "type_not_allowed",
            f"'{name}' contiene la extensión ejecutable o de configuración "
            f"'.{blocked[0]}' (también se rechazan dobles extensiones como "
            "foto.php.jpg). Renombra el archivo con una sola extensión de imagen.",
        )

    suffix = "." + segments[-1] if len(segments) > 1 else ""
    if suffix == ".svg":
        raise UploadRejection(
            415,
            "type_not_allowed",
            "SVG está desactivado porque puede contener scripts. Convierte el "
            "logo a PNG o WebP.",
        )
    allowed = allowed_extensions(allow_zip)
    if suffix not in allowed:
        raise UploadRejection(
            415,
            "type_not_allowed",
            f"La extensión '{suffix or '(ninguna)'}' no está permitida. "
            f"Permitidas: {', '.join(sorted(allowed))}.",
        )
    return allowed[suffix]


def sniff_mime(head: bytes) -> str | None:
    """Detecta el tipo real a partir de los primeros bytes."""

    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if (
        head.startswith(b"\x00\x00\x01\x00")
        and len(head) >= 6
        and head[4:6] != b"\x00\x00"
    ):
        return "image/x-icon"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"wOFF"):
        return "font/woff"
    if head.startswith(b"wOF2"):
        return "font/woff2"
    if head.startswith((b"\x00\x01\x00\x00", b"true")):
        return "font/ttf"
    if head.startswith(b"OTTO"):
        return "font/otf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "application/zip"
    return None


def check_magic(expected_mime: str, head: bytes) -> str:
    """Comprueba que el contenido real coincida con la extensión."""

    actual = sniff_mime(head)
    if actual != expected_mime:
        detected = actual or "desconocido"
        raise UploadRejection(
            415,
            "content_mismatch",
            f"El contenido real del archivo ({detected}) no coincide con su "
            f"extensión (se esperaba {expected_mime}). Sube un archivo auténtico "
            "o corrige la extensión.",
        )
    return actual
