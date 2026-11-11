"""Configuración de logs: solo stderr y con redacción de secretos."""

from __future__ import annotations

import logging
import re
import sys

_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|authorization|secret)\b\s*[=:]\s*\S+"
)


def redact_text(text: str, secrets: tuple[str, ...] | list[str] = ()) -> str:
    """Sustituye secretos conocidos y valores sensibles con nombre."""

    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return _SENSITIVE_PATTERN.sub(r"\1=[REDACTED]", result)


class SecretRedactionFilter(logging.Filter):
    """Filtro de logging que evita que los secretos lleguen a stderr."""

    def __init__(self, secrets: tuple[str, ...] | list[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = redact_text(record.getMessage(), self._secrets)
        record.msg = message
        record.args = ()
        return True


def configure_logging(
    secrets: tuple[str, ...] | list[str] = (),
    level: str = "WARNING",
) -> None:
    """Configura el logger raíz únicamente sobre stderr.

    stdout queda reservado para el protocolo JSON-RPC de MCP.
    """

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(SecretRedactionFilter(secrets))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))

    # Estas librerías pueden ser muy verbosas y no deben imprimir cabeceras.
    for noisy in ("httpx", "httpcore", "cpanel_api", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
