"""Lectura segura de archivos locales para ``upload_local_file``.

Solo se leen archivos regulares dentro de carpetas permitidas por el usuario
(``MCP_LOCAL_UPLOAD_DIRS``). No se siguen enlaces simbólicos ni componentes
``..``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Sequence

from .errors import (
    FileOperationError,
    FileTooLargeError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def resolve_local_file(value: object, allowed_dirs: Sequence[Path]) -> Path:
    """Valida la ruta local y devuelve el archivo regular dentro de la allowlist."""

    if not isinstance(value, str):
        raise ValidationError("local_path debe ser texto.")
    raw = value.strip()
    if not raw:
        raise ValidationError("local_path no puede estar vacío.")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise UnsafePathError("local_path contiene caracteres de control.")

    candidate = Path(os.path.expanduser(raw))
    if not candidate.is_absolute():
        raise UnsafePathError(
            "local_path debe ser una ruta absoluta (por ejemplo "
            "C:\\Users\\tu_usuario\\Downloads\\logo.png)."
        )
    if ".." in candidate.parts:
        raise UnsafePathError("local_path no puede contener '..'.")

    if not allowed_dirs:
        raise UnsafePathError(
            "No hay carpetas locales permitidas. Define MCP_LOCAL_UPLOAD_DIRS."
        )

    normalized = _norm(candidate)
    root: Path | None = None
    for allowed in allowed_dirs:
        base = _norm(allowed)
        if normalized == base or normalized.startswith(base.rstrip(os.sep) + os.sep):
            root = allowed
            break
    if root is None:
        shown = ", ".join(str(item) for item in allowed_dirs)
        raise UnsafePathError(
            "Por seguridad solo se pueden subir archivos de estas carpetas "
            f"locales: {shown}. Copia el archivo a una de ellas o ajusta "
            "MCP_LOCAL_UPLOAD_DIRS."
        )

    relative = Path(os.path.normpath(str(candidate))).relative_to(
        Path(os.path.normpath(str(root)))
    )
    current = Path(os.path.normpath(str(root)))
    mode = 0
    for part in relative.parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            raise FileOperationError(
                f"El archivo local '{candidate.name}' no existe en esa ruta."
            ) from None
        except OSError as exc:
            raise FileOperationError(
                f"No se pudo acceder al archivo local: {exc.strerror or exc}."
            ) from None
        if stat.S_ISLNK(mode):
            raise SymlinkError(
                f"'{part}' es un enlace simbólico; no se siguen enlaces al "
                "subir archivos locales."
            )
    if not relative.parts or not stat.S_ISREG(mode):
        raise FileOperationError(
            "local_path debe apuntar a un archivo regular, no a una carpeta."
        )
    return current


def read_limited(path: Path, limit: int) -> bytes:
    """Lee el archivo completo rechazando los que superen ``limit`` bytes."""

    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise FileOperationError(
            f"No se pudo leer el archivo local: {exc.strerror or exc}."
        ) from None
    if len(data) > limit:
        raise FileTooLargeError(
            f"El archivo local supera el límite de {limit} bytes. Ajusta "
            "MCP_FILE_MAX_BYTES (máximo 10485760) o reduce el archivo."
        )
    return data
