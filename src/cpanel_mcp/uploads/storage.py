"""Escritura segura en el filesystem de la cuenta (dentro de public_html)."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .validate import UploadRejection

TMP_DIRNAME = ".mcp-upload-tmp"


class AccountFiles:
    """Operaciones de archivos de una cuenta, sin seguir enlaces simbólicos."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)

    def parent_dir(self, normalized: str) -> tuple[Path, str]:
        """Valida cada componente existente y devuelve (directorio, nombre).

        ``normalized`` viene de ``normalize_public_path``: ``public_html/a/b.png``.
        """

        parts = normalized.split("/")
        current = self.home
        for part in parts[:-1]:
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                raise UploadRejection(
                    404,
                    "directory_not_found",
                    f"El directorio '/{'/'.join(parts[:-1])}' no existe. Créalo "
                    "primero (por ejemplo con create_subdomain).",
                ) from None
            if stat.S_ISLNK(mode):
                raise UploadRejection(
                    403,
                    "symlink_rejected",
                    f"'{part}' es un enlace simbólico; no se sigue ni se escribe "
                    "a través de enlaces.",
                )
            if not stat.S_ISDIR(mode):
                raise UploadRejection(
                    400, "not_a_directory", f"'{part}' no es un directorio."
                )
        return current, parts[-1]

    def inspect(self, dest: Path) -> str | None:
        """None si no existe; 'file' o 'other' (directorio, symlink...)."""

        try:
            mode = os.lstat(dest).st_mode
        except FileNotFoundError:
            return None
        return "file" if stat.S_ISREG(mode) else "other"

    def new_temp(self) -> Path:
        tmp_dir = self.home / TMP_DIRNAME
        tmp_dir.mkdir(mode=0o700, exist_ok=True)
        return tmp_dir / f"{secrets.token_hex(16)}.part"

    def publish(self, tmp: Path, dest: Path) -> None:
        """Publica sin pisar: ``link`` falla si el destino ya existe."""

        os.chmod(tmp, 0o644)
        try:
            os.link(tmp, dest)
        except FileExistsError:
            raise UploadRejection(
                409,
                "file_exists",
                "El archivo apareció mientras se subía. Crea un ticket nuevo.",
            ) from None
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
