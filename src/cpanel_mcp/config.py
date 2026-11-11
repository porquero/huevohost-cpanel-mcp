"""Configuración por variables de entorno.

Las credenciales solo se leen de CPANEL_HOST, CPANEL_USER y CPANEL_TOKEN.
Nunca se escriben en logs, auditoría ni respuestas.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")


def _default_audit_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Huevohost" / "cpanel-mcp" / "audit.jsonl"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Huevohost"
            / "cpanel-mcp"
            / "audit.jsonl"
        )
    return Path.home() / ".local" / "state" / "huevohost-cpanel-mcp" / "audit.jsonl"


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Falta la variable de entorno {name}. "
            "Configúrala antes de arrancar el servidor."
        )
    return value


def _parse_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on", "si", "sí"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} debe ser true o false.")


def _parse_port(env: Mapping[str, str], default: int) -> int:
    raw = (env.get("CPANEL_PORT") or "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError("CPANEL_PORT debe ser un número entero.") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("CPANEL_PORT debe estar entre 1 y 65535.")
    return port


def _parse_max_file_bytes(env: Mapping[str, str]) -> int:
    raw = (env.get("MCP_FILE_MAX_BYTES") or "").strip()
    if not raw:
        return 1_048_576
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError("MCP_FILE_MAX_BYTES debe ser un número entero.") from exc
    if not 1024 <= value <= 10_485_760:
        raise ConfigError(
            "MCP_FILE_MAX_BYTES debe estar entre 1024 y 10485760 bytes."
        )
    return value


def _default_local_upload_dirs() -> tuple[Path, ...]:
    return (Path.home() / "Downloads",)


def _parse_local_upload_dirs(env: Mapping[str, str]) -> tuple[Path, ...]:
    raw = (env.get("MCP_LOCAL_UPLOAD_DIRS") or "").strip()
    if not raw:
        return _default_local_upload_dirs()
    dirs: list[Path] = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        path = Path(item).expanduser()
        if not path.is_absolute():
            raise ConfigError(
                "MCP_LOCAL_UPLOAD_DIRS debe contener rutas absolutas separadas "
                f"por '{os.pathsep}'."
            )
        dirs.append(path)
    if not dirs:
        raise ConfigError("MCP_LOCAL_UPLOAD_DIRS no contiene ninguna carpeta.")
    return tuple(dirs)


def _normalize_host(raw: str) -> tuple[str, int | None]:
    host = raw.strip()
    if host.lower().startswith("https://"):
        host = host[8:]
    elif host.lower().startswith("http://"):
        host = host[7:]
    host = host.rstrip("/")

    if not host:
        raise ConfigError("CPANEL_HOST no puede estar vacío.")
    if any(char in host for char in "/?#"):
        raise ConfigError(
            "CPANEL_HOST debe contener solo el hostname o la IP, sin ruta ni esquema."
        )

    port_hint: int | None = None
    if ":" in host:
        hostname, port_text = host.rsplit(":", 1)
        if not port_text.isdigit():
            raise ConfigError(
                "El puerto incluido en CPANEL_HOST no es válido. "
                "Usa por ejemplo cpanel.example.com:2083 o CPANEL_PORT."
            )
        port_hint = int(port_text)
        if not 1 <= port_hint <= 65535:
            raise ConfigError("El puerto de CPANEL_HOST debe estar entre 1 y 65535.")
        host = hostname

    if not _HOST_PATTERN.fullmatch(host):
        raise ConfigError(
            "CPANEL_HOST no tiene un formato válido. "
            "Usa un hostname o una IP, por ejemplo cpanel.example.com."
        )
    return host.lower(), port_hint


@dataclass(frozen=True)
class Settings:
    host: str
    user: str
    token: str = field(repr=False)
    port: int = 2083
    verify_ssl: bool = True
    audit_path: Path = field(default_factory=_default_audit_path)
    allowed_actions_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / "allowed_actions.json"
    )
    log_level: str = "WARNING"
    max_file_bytes: int = 1_048_576
    local_upload_dirs: tuple[Path, ...] = field(
        default_factory=_default_local_upload_dirs
    )

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env

    host, host_port = _normalize_host(_required(source, "CPANEL_HOST"))
    user = _required(source, "CPANEL_USER")
    token = _required(source, "CPANEL_TOKEN")

    if any(char.isspace() for char in token):
        raise ConfigError("CPANEL_TOKEN contiene espacios o saltos de línea.")
    if any(char.isspace() for char in user):
        raise ConfigError("CPANEL_USER contiene espacios o saltos de línea.")

    log_level = (source.get("CPANEL_MCP_LOG_LEVEL") or "WARNING").strip().upper()
    if log_level not in _ALLOWED_LOG_LEVELS:
        raise ConfigError(
            "CPANEL_MCP_LOG_LEVEL debe ser DEBUG, INFO, WARNING, ERROR o CRITICAL."
        )

    audit_raw = (source.get("AUDIT_LOG_PATH") or "").strip()
    audit_path = (
        Path(audit_raw).expanduser().resolve()
        if audit_raw
        else _default_audit_path()
    )

    actions_raw = (source.get("ALLOWED_ACTIONS_PATH") or "").strip()
    allowed_actions_path = (
        Path(actions_raw).expanduser().resolve()
        if actions_raw
        else PROJECT_ROOT / "allowed_actions.json"
    )

    return Settings(
        host=host,
        user=user,
        token=token,
        port=_parse_port(source, host_port or 2083),
        verify_ssl=_parse_bool(source, "CPANEL_VERIFY_SSL", True),
        audit_path=audit_path,
        allowed_actions_path=allowed_actions_path,
        log_level=log_level,
        max_file_bytes=_parse_max_file_bytes(source),
        local_upload_dirs=_parse_local_upload_dirs(source),
    )
