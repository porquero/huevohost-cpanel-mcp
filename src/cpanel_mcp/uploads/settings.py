"""Configuración del servicio de subida (variables de entorno)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..config import _normalize_host, _parse_bool, _parse_port
from ..errors import ConfigError

MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class AccountConfig:
    user: str
    home: Path
    token: str = field(repr=False)


@dataclass(frozen=True)
class UploadSettings:
    public_base_url: str
    admin_secret: str = field(repr=False)
    accounts: dict[str, AccountConfig] = field(repr=False)
    db_path: Path = Path("upload-state.db")
    audit_path: Path = Path("upload-audit.jsonl")
    cpanel_host: str = "localhost"
    cpanel_port: int = 2083
    verify_ssl: bool = True
    max_bytes_cap: int = 5 * 1024 * 1024
    ticket_ttl_seconds: int = 300
    allow_zip: bool = False
    require_https: bool = True
    # (límite, ventana en segundos)
    limit_tickets_per_account: tuple[int, int] = (20, 60)
    limit_tickets_per_ip: tuple[int, int] = (60, 60)
    limit_uploads_per_ip: tuple[int, int] = (60, 60)
    limit_bad_attempts_per_ip: tuple[int, int] = (10, 600)

    def secrets(self) -> tuple[str, ...]:
        return (
            self.admin_secret,
            *(account.token for account in self.accounts.values()),
        )


def _int_env(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número entero.") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} debe estar entre {lo} y {hi}.")
    return value


def load_upload_settings(env: Mapping[str, str] | None = None) -> UploadSettings:
    source = os.environ if env is None else env

    secret = (source.get("UPLOAD_ADMIN_SECRET") or "").strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise ConfigError(
            f"UPLOAD_ADMIN_SECRET es obligatorio y debe tener al menos "
            f"{MIN_SECRET_LENGTH} caracteres."
        )

    base_url = (source.get("UPLOAD_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base_url.startswith("https://") or len(base_url) <= len("https://"):
        raise ConfigError(
            "UPLOAD_PUBLIC_BASE_URL es obligatoria y debe empezar con https://, "
            "por ejemplo https://upload.acopios.cl."
        )

    host, host_port = _normalize_host(source.get("CPANEL_HOST") or "localhost")
    default_user = (source.get("CPANEL_USER") or "").strip()

    raw_accounts = (source.get("UPLOAD_ACCOUNTS") or "").strip()
    pairs: list[tuple[str, Path]] = []
    if raw_accounts:
        for item in raw_accounts.split(","):
            user, sep, home = item.strip().partition("=")
            if not sep or not user.strip() or not home.strip():
                raise ConfigError(
                    "UPLOAD_ACCOUNTS debe tener el formato usuario=/home/usuario"
                    "[,usuario2=/home/usuario2]."
                )
            pairs.append((user.strip(), Path(home.strip())))
    elif default_user:
        pairs.append((default_user, Path.home()))
    if not pairs:
        raise ConfigError("Define UPLOAD_ACCOUNTS o CPANEL_USER.")

    accounts: dict[str, AccountConfig] = {}
    for user, home in pairs:
        key = "CPANEL_TOKEN_" + "".join(
            char if char.isalnum() else "_" for char in user.upper()
        )
        token = (source.get(key) or "").strip()
        if not token and user == default_user:
            token = (source.get("CPANEL_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                f"Falta el token de cPanel de la cuenta '{user}' "
                f"({key} o CPANEL_TOKEN)."
            )
        accounts[user] = AccountConfig(user=user, home=home, token=token)

    state_dir = Path.home() / ".huevohost-upload"
    return UploadSettings(
        public_base_url=base_url,
        admin_secret=secret,
        accounts=accounts,
        db_path=Path(source.get("UPLOAD_DB_PATH") or state_dir / "state.db"),
        audit_path=Path(source.get("UPLOAD_AUDIT_PATH") or state_dir / "audit.jsonl"),
        cpanel_host=host,
        cpanel_port=_parse_port(source, host_port or 2083),
        verify_ssl=_parse_bool(source, "CPANEL_VERIFY_SSL", True),
        max_bytes_cap=_int_env(
            source, "UPLOAD_MAX_BYTES_CAP", 5 * 1024 * 1024, 1024, 25 * 1024 * 1024
        ),
        ticket_ttl_seconds=_int_env(source, "UPLOAD_TICKET_TTL", 300, 30, 900),
        allow_zip=_parse_bool(source, "UPLOAD_ALLOW_ZIP", False),
    )
