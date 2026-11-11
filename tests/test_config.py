from __future__ import annotations

from pathlib import Path

import pytest

from cpanel_mcp.config import load_settings
from cpanel_mcp.errors import ConfigError


def base_env() -> dict[str, str]:
    return {
        "CPANEL_HOST": "cpanel.example.com",
        "CPANEL_USER": "huevo",
        "CPANEL_TOKEN": "TOKEN-SECRETO-123",
    }


def test_load_settings_defaults(tmp_path: Path) -> None:
    env = base_env()
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    settings = load_settings(env)
    assert settings.host == "cpanel.example.com"
    assert settings.port == 2083
    assert settings.verify_ssl is True
    assert settings.base_url == "https://cpanel.example.com:2083"
    assert "TOKEN-SECRETO-123" not in repr(settings)


def test_host_normalization(tmp_path: Path) -> None:
    env = base_env()
    env["CPANEL_HOST"] = "https://CPANEL.Example.com/"
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    assert load_settings(env).host == "cpanel.example.com"


def test_verify_ssl_false(tmp_path: Path) -> None:
    env = base_env()
    env["CPANEL_VERIFY_SSL"] = "false"
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    assert load_settings(env).verify_ssl is False


@pytest.mark.parametrize("name", ["CPANEL_HOST", "CPANEL_USER", "CPANEL_TOKEN"])
def test_missing_required(name: str) -> None:
    env = base_env()
    env.pop(name)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env)
    assert name in str(exc_info.value)


def test_host_with_port_is_accepted(tmp_path: Path) -> None:
    env = base_env()
    env["CPANEL_HOST"] = "https://cpanel.example.com:2084/"
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    settings = load_settings(env)
    assert settings.host == "cpanel.example.com"
    assert settings.port == 2084


def test_cpanel_port_overrides_host_port(tmp_path: Path) -> None:
    env = base_env()
    env["CPANEL_HOST"] = "cpanel.example.com:2084"
    env["CPANEL_PORT"] = "2085"
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    assert load_settings(env).port == 2085


def test_invalid_port() -> None:
    env = base_env()
    env["CPANEL_PORT"] = "70000"
    with pytest.raises(ConfigError):
        load_settings(env)


def test_token_with_whitespace_rejected() -> None:
    env = base_env()
    env["CPANEL_TOKEN"] = "abc def"
    with pytest.raises(ConfigError):
        load_settings(env)


def test_max_file_bytes_default_and_custom(tmp_path: Path) -> None:
    env = base_env()
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    assert load_settings(env).max_file_bytes == 1_048_576

    env["MCP_FILE_MAX_BYTES"] = "2048"
    assert load_settings(env).max_file_bytes == 2048


@pytest.mark.parametrize("raw", ["0", "1023", "10485761", "muchos"])
def test_invalid_max_file_bytes(tmp_path: Path, raw: str) -> None:
    env = base_env()
    env["AUDIT_LOG_PATH"] = str(tmp_path / "audit.jsonl")
    env["ALLOWED_ACTIONS_PATH"] = str(tmp_path / "allowed_actions.json")
    env["MCP_FILE_MAX_BYTES"] = raw
    with pytest.raises(ConfigError):
        load_settings(env)
