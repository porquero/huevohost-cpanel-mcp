from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpanel_mcp.actions_config import load_actions
from cpanel_mcp.errors import ConfigError


def test_default_actions_file_has_high_impact_disabled() -> None:
    path = Path(__file__).resolve().parents[1] / "allowed_actions.json"
    actions = {action.name: action for action in load_actions(path)}
    assert actions["list_domains"].enabled is True
    assert actions["list_email_accounts"].enabled is True
    assert actions["create_email_account"].enabled is True
    assert actions["create_subdomain"].enabled is True
    assert actions["create_subdomain"].level == "escritura_reversible"
    assert actions["list_files"].enabled is True
    assert actions["download_file"].enabled is True
    assert actions["create_file"].enabled is True
    assert actions["upload_file"].enabled is True
    assert actions["delete_file"].enabled is True
    assert actions["delete_file"].level == "alto_impacto"
    assert actions["publish_landing_page"].enabled is False
    assert actions["publish_landing_page"].level == "alto_impacto"


def test_invalid_level_fails(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            {
                "actions": [
                    {"name": "list_domains", "level": "total", "enabled": True}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_actions(path)


def test_duplicate_name_fails(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            {
                "actions": [
                    {"name": "list_domains", "level": "lectura", "enabled": True},
                    {"name": "list_domains", "level": "lectura", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_actions(path)


def test_enabled_must_be_boolean(tmp_path: Path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            {
                "actions": [
                    {"name": "list_domains", "level": "lectura", "enabled": "true"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_actions(path)


def test_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_actions(tmp_path / "no-existe.json")
