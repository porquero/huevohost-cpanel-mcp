from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from cpanel_mcp.config import Settings
from cpanel_mcp.logging_setup import (
    SecretRedactionFilter,
    configure_logging,
    redact_text,
)


def test_redact_text_removes_known_secret_and_named_values() -> None:
    text = "Authorization: cpanel user:TOKEN-SECRETO-123 password=MiClave"
    cleaned = redact_text(text, ("TOKEN-SECRETO-123",))
    assert "TOKEN-SECRETO-123" not in cleaned
    assert "MiClave" not in cleaned
    assert "[REDACTED]" in cleaned


def test_logging_filter_redacts_token() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter(("TOKEN-SECRETO-123",)))
    logger = logging.getLogger("test.secrets")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("token=%s", "TOKEN-SECRETO-123")
    output = stream.getvalue()
    assert "TOKEN-SECRETO-123" not in output
    assert "[REDACTED]" in output


def test_configure_logging_goes_to_stderr(monkeypatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    configure_logging(("TOKEN-SECRETO-123",), "INFO")
    logging.getLogger("cpanel_mcp.test").info(
        "token=%s password=MiClave", "TOKEN-SECRETO-123"
    )
    output = stream.getvalue()
    assert "TOKEN-SECRETO-123" not in output
    assert "MiClave" not in output
    assert "[REDACTED]" in output
    logging.getLogger().handlers.clear()


def test_gitignore_covers_secrets_and_logs() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".venv/", ".env", "audit.jsonl", "*.log", "__pycache__/"):
        assert pattern in content


def test_settings_repr_hides_token() -> None:
    settings = Settings(host="cpanel.test", user="tester", token="SECRETO")
    assert "SECRETO" not in repr(settings)
