"""Registro de auditoría append-only en JSON Lines."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .logging_setup import redact_text

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "api_token",
    "content",
    "content_base64",
    "contents",
    "file_content",
}


def sanitize(value: Any, secrets: tuple[str, ...] | list[str] = ()) -> Any:
    """Elimina secretos de estructuras antes de escribirlas en auditoría."""

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _SENSITIVE_KEYS:
                cleaned[key_text] = "[REDACTED]"
            else:
                cleaned[key_text] = sanitize(item, secrets)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value


class AuditLogger:
    """Escribe una línea JSON por invocación, protegida por un lock."""

    def __init__(self, path: Path, secrets: tuple[str, ...] | list[str] = ()) -> None:
        self.path = Path(path)
        self._secrets = tuple(secret for secret in secrets if secret)
        self._lock = threading.Lock()

    def log(
        self,
        *,
        action: str,
        args: Mapping[str, Any],
        outcome: str,
        error_code: str | None = None,
        duration_ms: int | None = None,
        dry_run: bool | None = None,
        verified: bool | None = None,
        message: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "args": sanitize(dict(args), self._secrets),
            "outcome": outcome,
        }
        if error_code is not None:
            entry["error_code"] = error_code
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if dry_run is not None:
            entry["dry_run"] = dry_run
        if verified is not None:
            entry["verified"] = verified
        if message:
            entry["message"] = redact_text(message, self._secrets)

        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
