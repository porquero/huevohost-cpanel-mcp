"""Tipos binarios permitidos para ``upload_local_file`` y verificación por firma.

La verificación usa magic bytes en Python puro (sin libmagic): el contenido real
debe coincidir con la extensión del destino.
"""

from __future__ import annotations

from .errors import FileTypeNotAllowedError
from .file_ops import _BLOCKED_EXTENSIONS

ALLOWED_BINARY_EXTENSIONS: dict[str, str] = {
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

# Segmentos prohibidos en cualquier posición del nombre (foto.php.jpg).
_BLOCKED_SEGMENTS = frozenset(
    {ext.lstrip(".") for ext in _BLOCKED_EXTENSIONS}
    | {"asp", "aspx", "jsp", "shtml", "htaccess", "htpasswd", "phps", "pht"}
)

HEAD_BYTES = 16
PHP_MIME = "application/x-httpd-php"


def expected_mime_for(name: str, *, allow_php: bool = False) -> str:
    """Valida el nombre de destino y devuelve el MIME esperado.

    Con ``allow_php=True`` (nombre ya validado por ``_check_writable_path``)
    se espera un archivo PHP.
    """

    if allow_php and name.lower().endswith(".php"):
        return PHP_MIME

    segments = name.lower().split(".")
    blocked = [seg for seg in segments[1:] if seg in _BLOCKED_SEGMENTS]
    if blocked:
        raise FileTypeNotAllowedError(
            f"'{name}' contiene la extensión ejecutable o de configuración "
            f".{blocked[0]} (se rechazan también dobles extensiones como "
            "foto.php.jpg). Usa un nombre con una sola extensión de imagen."
        )
    suffix = "." + segments[-1] if len(segments) > 1 else ""
    if suffix == ".svg":
        raise FileTypeNotAllowedError(
            "SVG no se sube desde MCP porque puede contener scripts. "
            "Conviértelo a PNG o WebP."
        )
    mime = ALLOWED_BINARY_EXTENSIONS.get(suffix)
    if mime is None:
        raise FileTypeNotAllowedError(
            f"La extensión '{suffix or '(ninguna)'}' no está permitida para "
            f"subidas locales. Permitidas: "
            f"{', '.join(sorted(ALLOWED_BINARY_EXTENSIONS))}."
        )
    return mime


def sniff_mime(head: bytes) -> str | None:
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
    return None


def verify_content(expected_mime: str, data: bytes) -> str:
    """Comprueba que el contenido real coincida con la extensión de destino."""

    if expected_mime == PHP_MIME:
        if b"\x00" in data or b"<?" not in data:
            raise FileTypeNotAllowedError(
                "El archivo no parece un script PHP (debe ser texto y contener "
                "una etiqueta '<?php'). No se subió."
            )
        return PHP_MIME

    actual = sniff_mime(data[:HEAD_BYTES])
    if actual != expected_mime:
        raise FileTypeNotAllowedError(
            f"El contenido real del archivo ({actual or 'desconocido'}) no "
            f"coincide con la extensión de destino (se esperaba "
            f"{expected_mime}). Corrige la extensión o usa un archivo válido."
        )
    return actual
