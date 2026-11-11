"""Operaciones de archivos confinadas a /public_html.

Todas las rutas que llegan del modelo se normalizan antes de contactar cPanel.
Se rechazan rutas absolutas fuera de ``public_html``, componentes ``.``/``..``,
caracteres de control y enlaces simbólicos en cualquier componente existente.

Las escrituras nunca sobrescriben directamente: si el destino existe, primero
se mueve a la papelera de cPanel y después se escribe el nuevo contenido.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from typing import Any

from .config import Settings
from .cpanel import CPanelClient
from .errors import (
    FileAlreadyExistsError,
    FileOperationError,
    FileTooLargeError,
    FileTypeNotAllowedError,
    RemoteFileNotFoundError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
)

PUBLIC_ROOT = "public_html"
_MAX_PATH_LENGTH = 1024
_MAX_COMPONENT_LENGTH = 255
_MAX_DEPTH = 20

# El listado y la descarga solo deben devolver texto: get_file_content no es
# seguro para binarios en un contexto JSON.
_TEXT_EXTENSIONS = {
    ".css",
    ".csv",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".map",
    ".md",
    ".mjs",
    ".php",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_TEXT_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/xml",
    "application/x-httpd-php",
    "application/xhtml+xml",
    "image/svg+xml",
}

# Archivos que pueden contener credenciales. No se descargan ni se escriben
# desde el chat para evitar filtrarlas al historial de la conversación.
_SENSITIVE_NAMES = {
    ".env",
    ".htaccess",
    ".htpasswd",
    ".netrc",
    ".user.ini",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "php.ini",
    "wp-config.php",
}
_SENSITIVE_PREFIXES = (".env.",)
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SENSITIVE_DIRECTORIES = {".git", ".hg", ".ssh", ".svn"}

# Extensiones que ejecutan código en el servidor o cambian su configuración.
# Se bloquean tanto en create_file como en upload_file.
_BLOCKED_EXTENSIONS = {
    ".bash",
    ".cgi",
    ".dll",
    ".exe",
    ".htaccess",
    ".phar",
    ".php",
    ".php3",
    ".php4",
    ".php5",
    ".php7",
    ".php8",
    ".phtml",
    ".pl",
    ".py",
    ".rb",
    ".sh",
    ".so",
}


def display_path(path: str) -> str:
    """Devuelve la ruta con barra inicial, tal como la espera el usuario."""

    return "/" + path.lstrip("/")


def normalize_public_path(value: object, *, allow_root: bool = True) -> str:
    """Normaliza una ruta y la confina a ``public_html``.

    Acepta ``public_html/x``, ``/public_html/x`` y rutas relativas como
    ``sitio.example.com/index.html`` (interpretadas desde ``public_html``).
    """

    if not isinstance(value, str):
        raise UnsafePathError("La ruta debe ser texto.")

    raw = value.strip()
    if not raw:
        raise UnsafePathError("La ruta no puede estar vacía.")
    if "\x00" in raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise UnsafePathError("La ruta contiene caracteres de control.")
    if "\\" in raw:
        raise UnsafePathError("Usa barras normales (/) en la ruta.")
    if raw.startswith("~"):
        raise UnsafePathError("No se admite la expansión de ~ en la ruta.")

    raw = raw.rstrip("/")
    if not raw:
        raise UnsafePathError("La ruta no puede ser la raíz del sistema.")

    if raw == PUBLIC_ROOT:
        relative = ""
    elif raw.startswith(PUBLIC_ROOT + "/"):
        relative = raw[len(PUBLIC_ROOT) + 1 :]
    elif raw == "/" + PUBLIC_ROOT:
        relative = ""
    elif raw.startswith("/" + PUBLIC_ROOT + "/"):
        relative = raw[len(PUBLIC_ROOT) + 2 :]
    elif raw.startswith("/"):
        raise UnsafePathError(
            "La ruta debe estar dentro de /public_html. Por ejemplo: "
            "/public_html/sitio.example.com/index.html."
        )
    else:
        # Ruta relativa a public_html.
        relative = raw

    if not relative:
        if not allow_root:
            raise UnsafePathError(
                "Esta acción no se puede aplicar a /public_html completo."
            )
        return PUBLIC_ROOT

    parts = relative.split("/")
    if len(parts) > _MAX_DEPTH:
        raise UnsafePathError(
            f"La ruta no puede superar {_MAX_DEPTH} niveles de profundidad."
        )
    for part in parts:
        if part in {"", ".", ".."}:
            raise UnsafePathError(
                "La ruta no puede contener componentes vacíos, '.' ni '..'."
            )
        if part != part.strip():
            raise UnsafePathError(
                "Los nombres de archivo no pueden empezar ni terminar con espacios."
            )
        if len(part) > _MAX_COMPONENT_LENGTH:
            raise UnsafePathError(
                f"Cada componente de la ruta debe tener {_MAX_COMPONENT_LENGTH} "
                "caracteres o menos."
            )
        if any(char in part for char in ",;"):
            raise UnsafePathError(
                "Los nombres de archivo no pueden contener ',' ni ';'."
            )

    normalized = PUBLIC_ROOT + "/" + "/".join(parts)
    if len(normalized) > _MAX_PATH_LENGTH:
        raise UnsafePathError("La ruta es demasiado larga.")
    return normalized


def _parent_and_name(path: str) -> tuple[str, str]:
    parent, _, name = path.rpartition("/")
    if not parent or not name:
        raise UnsafePathError("La ruta debe incluir un nombre de archivo.")
    return parent, name


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        # cPanel devuelve size="" para archivos vacíos.
        if not text:
            return 0
        if text.isdigit():
            return int(text)
    return None


def _is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    if lower in _SENSITIVE_NAMES:
        return True
    if any(lower.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
        return True
    return any(lower.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _is_text_file(name: str, mimetype: str | None) -> bool:
    lower = name.lower()
    if lower.endswith(tuple(_TEXT_EXTENSIONS)):
        return True
    if not mimetype:
        return False
    mime = mimetype.lower().split(";", 1)[0].strip()
    return mime.startswith("text/") or mime in _TEXT_MIME_TYPES


def _check_writable_path(path: str, name: str) -> None:
    components = path.split("/")[1:]
    if any(part.lower() in _SENSITIVE_DIRECTORIES for part in components):
        raise FileTypeNotAllowedError(
            "Por seguridad no se escribe dentro de directorios de control de "
            "versiones o de claves."
        )

    lower = name.lower()
    if _is_sensitive_name(name):
        raise FileTypeNotAllowedError(
            f"Por seguridad no se puede escribir el archivo '{name}' desde MCP."
        )
    suffix = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    if lower in _BLOCKED_EXTENSIONS or suffix in _BLOCKED_EXTENSIONS:
        raise FileTypeNotAllowedError(
            f"La extensión de '{name}' puede ejecutar código o cambiar la "
            "configuración del servidor. Usa archivos estáticos o súbelo "
            "manualmente desde cPanel."
        )


def _validate_overwrite(value: object) -> bool:
    if type(value) is not bool:
        raise ValidationError("overwrite debe ser true o false.")
    return value


def _validate_show_hidden(value: object) -> bool:
    if type(value) is not bool:
        raise ValidationError("show_hidden debe ser true o false.")
    return value


async def inspect_remote_path(
    client: CPanelClient,
    path: str,
) -> dict[str, Any] | None:
    """Devuelve la información de una ruta o None si no existe."""

    try:
        info = await client.get_file_information(path)
    except RemoteFileNotFoundError:
        return None
    if info.get("exists") in {0, "0", False}:
        return None
    return info


async def ensure_safe_remote_path(
    client: CPanelClient,
    path: str,
    *,
    must_exist: bool,
    expect_dir: bool | None = None,
) -> dict[str, Any] | None:
    """Recorre los componentes y rechaza symlinks o tipos inesperados.

    Si ``must_exist`` es False se detiene en el primer componente que no
    existe y devuelve None. Los componentes anteriores sí se validan, de modo
    que ninguna escritura atraviesa un enlace simbólico.
    """

    parts = path.split("/")
    current: list[str] = []
    info: dict[str, Any] | None = None

    for index, part in enumerate(parts):
        current.append(part)
        current_path = "/".join(current)
        info = await inspect_remote_path(client, current_path)
        is_last = index == len(parts) - 1

        if info is None:
            if must_exist:
                raise RemoteFileNotFoundError(
                    f"La ruta '{display_path(current_path)}' no existe."
                )
            return None

        entry_type = info.get("type")
        if entry_type == "symlink":
            raise SymlinkError(
                f"'{display_path(current_path)}' es un enlace simbólico. "
                "Por seguridad, MCP no sigue ni modifica enlaces simbólicos."
            )
        if entry_type not in {"dir", "file"}:
            raise FileOperationError(
                f"No se pudo determinar el tipo de "
                f"'{display_path(current_path)}'. Por seguridad no se "
                "opera sobre elementos especiales."
            )
        if not is_last and entry_type != "dir":
            raise FileOperationError(
                f"'{display_path(current_path)}' no es un directorio."
            )
        if is_last and expect_dir is True and entry_type != "dir":
            raise FileOperationError(
                f"'{display_path(current_path)}' no es un directorio."
            )
        if is_last and expect_dir is False and entry_type == "dir":
            raise FileOperationError(
                f"'{display_path(current_path)}' es un directorio."
            )

    return info


class FileOperations:
    """Acciones de archivos con validación de rutas y respaldo previo."""

    def __init__(self, client: CPanelClient, settings: Settings) -> None:
        self.client = client
        self.max_file_bytes = settings.max_file_bytes
        self.local_upload_dirs = settings.local_upload_dirs

    async def _inspect(self, path: str) -> dict[str, Any] | None:
        return await inspect_remote_path(self.client, path)

    async def _ensure_safe_path(
        self,
        path: str,
        *,
        must_exist: bool,
        expect_dir: bool | None = None,
    ) -> dict[str, Any] | None:
        return await ensure_safe_remote_path(
            self.client,
            path,
            must_exist=must_exist,
            expect_dir=expect_dir,
        )

    async def _prepare_write(
        self,
        path: str,
    ) -> tuple[str, str, dict[str, Any] | None]:
        parent, name = _parent_and_name(path)
        await self._ensure_safe_path(parent, must_exist=True, expect_dir=True)
        existing = await self._inspect(path)
        if existing is not None:
            entry_type = existing.get("type")
            if entry_type == "symlink":
                raise SymlinkError(
                    f"'{display_path(path)}' es un enlace simbólico y no se "
                    "puede sobrescribir."
                )
            if entry_type == "dir":
                raise FileOperationError(
                    f"'{display_path(path)}' es un directorio, no un archivo."
                )
        return parent, name, existing

    async def _backup_existing(
        self,
        path: str,
        existing: dict[str, Any] | None,
    ) -> bool:
        if existing is None:
            return False
        await self.client.trash_file(path)
        return True

    async def _verify_file(self, path: str, expected_size: int) -> bool:
        info = await self._inspect(path)
        if info is None or info.get("type") != "file":
            return False
        return _as_int(info.get("size")) == expected_size

    async def list_files(
        self,
        path: str = PUBLIC_ROOT,
        show_hidden: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_public_path(path)
        show_hidden = _validate_show_hidden(show_hidden)
        await self._ensure_safe_path(normalized, must_exist=True, expect_dir=True)

        entries = await self.client.list_files(
            normalized,
            show_hidden=show_hidden,
        )
        result: list[dict[str, Any]] = []
        for entry in entries:
            name = entry.get("file")
            if not isinstance(name, str) or not name:
                continue
            entry_type = entry.get("type")
            result.append(
                {
                    "name": name,
                    "path": f"{normalized.rstrip('/')}/{name}",
                    "type": entry_type,
                    "size": _as_int(entry.get("size")),
                    "mtime": _as_int(entry.get("mtime")),
                    "mimetype": entry.get("mimetype"),
                    "readable": entry.get("read") in {1, "1", True},
                    "writable": entry.get("write") in {1, "1", True},
                    "symlink": entry_type == "symlink",
                }
            )

        result.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
        return {
            "path": normalized,
            "path_display": display_path(normalized),
            "count": len(result),
            "entries": result,
        }

    async def download_file(self, path: str) -> dict[str, Any]:
        normalized = normalize_public_path(path, allow_root=False)
        parent, name = _parent_and_name(normalized)

        if _is_sensitive_name(name):
            raise FileTypeNotAllowedError(
                f"Por seguridad no se puede descargar '{name}' desde MCP: "
                "puede contener credenciales."
            )
        components = normalized.split("/")[1:]
        if any(part.lower() in _SENSITIVE_DIRECTORIES for part in components):
            raise FileTypeNotAllowedError(
                "Por seguridad no se descargan archivos de directorios de "
                "control de versiones o de claves."
            )

        info = await self._ensure_safe_path(
            normalized,
            must_exist=True,
            expect_dir=False,
        )
        assert info is not None

        size = _as_int(info.get("size"))
        if size is not None and size > self.max_file_bytes:
            raise FileTooLargeError(
                f"'{display_path(normalized)}' ocupa {size} bytes y el límite "
                f"es {self.max_file_bytes}. Ajusta MCP_FILE_MAX_BYTES o "
                "descárgalo manualmente desde cPanel."
            )

        mimetype = info.get("mimetype")
        if not _is_text_file(name, mimetype if isinstance(mimetype, str) else None):
            raise FileTypeNotAllowedError(
                f"'{name}' no parece un archivo de texto "
                f"(tipo: {mimetype or 'desconocido'}). La descarga por MCP "
                "solo admite HTML, CSS, JS, JSON, texto y formatos similares."
            )

        data = await self.client.get_file_content(parent, name)
        content = data.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise FileOperationError(
                "cPanel devolvió el contenido en un formato inesperado."
            )
        if "\x00" in content:
            raise FileTypeNotAllowedError(
                f"'{name}' contiene datos binarios y no se puede descargar "
                "como texto."
            )

        encoded_size = len(content.encode("utf-8"))
        if encoded_size > self.max_file_bytes:
            raise FileTooLargeError(
                f"El contenido de '{name}' supera el límite de "
                f"{self.max_file_bytes} bytes."
            )

        return {
            "path": normalized,
            "path_display": display_path(normalized),
            "filename": name,
            "size": encoded_size,
            "mimetype": mimetype,
            "encoding": data.get("to_charset") or "UTF-8",
            "content": content,
            "verified": True,
        }

    async def create_file(
        self,
        path: str,
        content: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_public_path(path, allow_root=False)
        overwrite = _validate_overwrite(overwrite)
        if not isinstance(content, str):
            raise ValidationError("El contenido del archivo debe ser texto.")

        _, name = _parent_and_name(normalized)
        _check_writable_path(normalized, name)

        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise FileTooLargeError(
                f"El contenido supera el límite de {self.max_file_bytes} bytes."
            )

        parent, _, existing = await self._prepare_write(normalized)
        if existing is not None and not overwrite:
            raise FileAlreadyExistsError(
                f"'{display_path(normalized)}' ya existe. Usa overwrite=true "
                "para reemplazarlo; el original se moverá a la papelera."
            )

        backed_up = await self._backup_existing(normalized, existing)
        try:
            # Se usa multipart en lugar de save_file_content para no enviar el
            # contenido en la query string: cPanel limita la longitud de la URL.
            await self.client.upload_file(
                parent,
                name,
                encoded,
                overwrite=False,
            )
        except Exception as exc:
            if backed_up:
                raise FileOperationError(
                    f"No se pudo escribir '{display_path(normalized)}'. El "
                    "archivo anterior quedó en la papelera de cPanel y puede "
                    "restaurarse desde allí."
                ) from exc
            raise

        verified = await self._verify_file(normalized, len(encoded))
        result: dict[str, Any] = {
            "path": normalized,
            "path_display": display_path(normalized),
            "created": existing is None,
            "overwritten": backed_up,
            "backed_up_to_trash": backed_up,
            "bytes": len(encoded),
            "verified": verified,
            "message": "Archivo creado en cPanel.",
        }
        if not verified:
            result["warning"] = (
                "cPanel aceptó la escritura, pero la verificación inmediata no "
                "coincide con el tamaño esperado. Revisa el archivo en cPanel."
            )
        return result

    async def upload_file(
        self,
        path: str,
        content: str | None = None,
        content_base64: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_public_path(path, allow_root=False)
        overwrite = _validate_overwrite(overwrite)

        if (content is None) == (content_base64 is None):
            raise ValidationError(
                "Indica exactamente uno de content (texto) o content_base64 "
                "(binario codificado en base64)."
            )

        if content is not None:
            if not isinstance(content, str):
                raise ValidationError("content debe ser texto.")
            payload = content.encode("utf-8")
        else:
            if not isinstance(content_base64, str) or not content_base64.strip():
                raise ValidationError(
                    "content_base64 debe ser una cadena base64 no vacía."
                )
            try:
                compact = "".join(content_base64.split())
                payload = base64.b64decode(compact, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValidationError(
                    "content_base64 no es base64 válido."
                ) from exc

        if len(payload) > self.max_file_bytes:
            raise FileTooLargeError(
                f"El archivo ocupa {len(payload)} bytes y el límite es "
                f"{self.max_file_bytes}. Ajusta MCP_FILE_MAX_BYTES o súbelo "
                "manualmente desde cPanel."
            )

        _, name = _parent_and_name(normalized)
        _check_writable_path(normalized, name)

        parent, _, existing = await self._prepare_write(normalized)
        if existing is not None and not overwrite:
            raise FileAlreadyExistsError(
                f"'{display_path(normalized)}' ya existe. Usa overwrite=true "
                "para reemplazarlo; el original se moverá a la papelera."
            )

        backed_up = await self._backup_existing(normalized, existing)
        try:
            await self.client.upload_file(
                parent,
                name,
                payload,
                overwrite=False,
            )
        except Exception as exc:
            if backed_up:
                raise FileOperationError(
                    f"No se pudo subir '{display_path(normalized)}'. El "
                    "archivo anterior quedó en la papelera de cPanel y puede "
                    "restaurarse desde allí."
                ) from exc
            raise

        verified = await self._verify_file(normalized, len(payload))
        result: dict[str, Any] = {
            "path": normalized,
            "path_display": display_path(normalized),
            "uploaded": True,
            "created": existing is None,
            "overwritten": backed_up,
            "backed_up_to_trash": backed_up,
            "bytes": len(payload),
            "verified": verified,
            "message": "Archivo subido a cPanel.",
        }
        if not verified:
            result["warning"] = (
                "cPanel aceptó la subida, pero la verificación inmediata no "
                "coincide con el tamaño esperado. Revisa el archivo en cPanel."
            )
        return result

    async def list_local_files(self) -> dict[str, Any]:
        """Lista la bandeja local (primera carpeta de MCP_LOCAL_UPLOAD_DIRS)."""

        from .binary_types import ALLOWED_BINARY_EXTENSIONS
        from .local_files import list_inbox

        if not self.local_upload_dirs:
            raise UnsafePathError(
                "No hay carpetas locales permitidas. Define MCP_LOCAL_UPLOAD_DIRS."
            )
        inbox = self.local_upload_dirs[0]
        if not inbox.is_dir():
            return {
                "folder": str(inbox),
                "exists": False,
                "count": 0,
                "files": [],
                "message": (
                    f"La carpeta {inbox} no existe. Créala y pide al usuario "
                    "que copie ahí los archivos que quiere subir."
                ),
            }
        files = await asyncio.to_thread(
            list_inbox, inbox, tuple(ALLOWED_BINARY_EXTENSIONS)
        )
        return {
            "folder": str(inbox),
            "exists": True,
            "count": len(files),
            "files": files,
            "message": (
                "Para subir uno, llama a upload_local_file con local_path igual "
                "al nombre que aparece en 'name'."
            ),
        }

    async def upload_local_file(
        self,
        local_path: str,
        path: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Sube un archivo binario leído del disco local, sin pasar por el modelo."""

        # Importación diferida: estos módulos importan constantes de este.
        from .binary_types import expected_mime_for, verify_content
        from .local_files import read_limited, resolve_local_file

        normalized = normalize_public_path(path, allow_root=False)
        overwrite = _validate_overwrite(overwrite)
        _, name = _parent_and_name(normalized)
        _check_writable_path(normalized, name)
        expected_mime = expected_mime_for(name)

        source = resolve_local_file(local_path, self.local_upload_dirs)
        payload = await asyncio.to_thread(
            read_limited, source, self.max_file_bytes
        )
        if not payload:
            raise ValidationError("El archivo local está vacío.")
        mime = verify_content(expected_mime, payload)
        digest = hashlib.sha256(payload).hexdigest()

        parent, _, existing = await self._prepare_write(normalized)
        if existing is not None and not overwrite:
            raise FileAlreadyExistsError(
                f"'{display_path(normalized)}' ya existe. Usa overwrite=true "
                "para reemplazarlo; el original se moverá a la papelera."
            )

        backed_up = await self._backup_existing(normalized, existing)
        try:
            await self.client.upload_file(parent, name, payload, overwrite=False)
        except Exception as exc:
            if backed_up:
                raise FileOperationError(
                    f"No se pudo subir '{display_path(normalized)}'. El "
                    "archivo anterior quedó en la papelera de cPanel y puede "
                    "restaurarse desde allí."
                ) from exc
            raise

        verified = await self._verify_file(normalized, len(payload))
        result: dict[str, Any] = {
            "path": normalized,
            "path_display": display_path(normalized),
            "uploaded": True,
            "created": existing is None,
            "overwritten": backed_up,
            "backed_up_to_trash": backed_up,
            "source_filename": source.name,
            "bytes": len(payload),
            "sha256": digest,
            "mime": mime,
            "verified": verified,
            "message": "Archivo local subido a cPanel.",
        }
        if not verified:
            result["warning"] = (
                "cPanel aceptó la subida, pero la verificación inmediata no "
                "coincide con el tamaño esperado. Revisa el archivo en cPanel."
            )
        return result

    async def delete_file(self, path: str, confirm: str) -> dict[str, Any]:
        normalized = normalize_public_path(path, allow_root=False)

        if not isinstance(confirm, str) or not confirm.strip():
            raise ValidationError(
                "Falta la confirmación. Escribe la ruta exacta en confirm."
            )
        confirmed = normalize_public_path(confirm, allow_root=False)
        if confirmed != normalized:
            raise ValidationError(
                "La confirmación no coincide con la ruta. Para borrar "
                f"'{display_path(normalized)}' escribe exactamente "
                f"'{display_path(normalized)}' en confirm."
            )

        info = await self._ensure_safe_path(
            normalized,
            must_exist=True,
        )
        assert info is not None

        data = await self.client.trash_file(normalized)
        remaining = await self._inspect(normalized)
        verified = remaining is None

        result: dict[str, Any] = {
            "path": normalized,
            "path_display": display_path(normalized),
            "type": info.get("type"),
            "trashed": True,
            "backed_up_to_trash": True,
            "trash_leaf": data.get("trash_leaf"),
            "verified": verified,
            "message": (
                "Elemento movido a la papelera de cPanel. Se puede restaurar "
                "desde Archivos → Papelera."
            ),
        }
        if not verified:
            result["warning"] = (
                "cPanel aceptó el movimiento a la papelera, pero la ruta aún "
                "aparece en la verificación. Revisa cPanel antes de reutilizar "
                "el nombre."
            )
        return result
