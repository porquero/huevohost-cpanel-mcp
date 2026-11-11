from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp.audit import AuditLogger, sanitize
from cpanel_mcp.config import Settings
from cpanel_mcp.server import build_server

from .conftest import write_actions
from .test_operations import FakeClient


def test_audit_writes_one_json_line(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(path, secrets=("TOKEN-SECRETO-123",))
    audit.log(
        action="create_email_account",
        args={
            "user": "juan",
            "domain": "example.com",
            "quota_mb": 500,
            "password": "NO-DEBE-SALIR",
            "token": "TOKEN-SECRETO-123",
        },
        outcome="success",
        duration_ms=12,
        dry_run=True,
        verified=False,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "create_email_account"
    assert entry["args"]["password"] == "[REDACTED]"
    assert entry["args"]["token"] == "[REDACTED]"
    assert "NO-DEBE-SALIR" not in lines[0]
    assert "TOKEN-SECRETO-123" not in lines[0]
    assert entry["dry_run"] is True


def test_sanitize_redacts_nested_values() -> None:
    payload = {
        "outer": [
            {"Authorization": "cpanel user:TOKEN-SECRETO-123"},
            "TOKEN-SECRETO-123",
        ]
    }
    cleaned = sanitize(payload, ("TOKEN-SECRETO-123",))
    assert "TOKEN-SECRETO-123" not in json.dumps(cleaned)


def test_sanitize_redacts_file_content_arguments() -> None:
    payload = {
        "path": "public_html/site/index.html",
        "content": "<h1>contenido privado</h1>",
        "content_base64": "Y29udGVuaWRv",
    }
    cleaned = sanitize(payload)
    text = json.dumps(cleaned)
    assert "contenido privado" not in text
    assert "Y29udGVuaWRv" not in text
    assert cleaned["path"] == "public_html/site/index.html"
    assert cleaned["content"] == "[REDACTED]"
    assert cleaned["content_base64"] == "[REDACTED]"


async def test_successful_tool_is_audited_without_password(
    settings: Settings, tmp_path
) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {
                "name": "create_email_account",
                "level": "escritura_reversible",
                "enabled": True,
            }
        ],
    )
    server = build_server(
        settings,
        client=FakeClient(),  # type: ignore[arg-type]
        actions_path=actions,
    )
    _, structured = await server.call_tool(
        "create_email_account",
        {
            "user": "juan",
            "domain": "example.com",
            "quota_mb": 500,
            "execute": True,
        },
    )
    password = structured["result"]["password"]
    audit_text = settings.audit_path.read_text(encoding="utf-8")
    entry = json.loads(audit_text.splitlines()[0])

    assert entry["outcome"] == "success"
    assert entry["dry_run"] is False
    assert entry["verified"] is True
    assert entry["args"]["user"] == "juan"
    assert password not in audit_text
    assert settings.token not in audit_text


async def test_failed_tool_is_audited(settings: Settings, tmp_path) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {
                "name": "create_email_account",
                "level": "escritura_reversible",
                "enabled": True,
            }
        ],
    )
    server = build_server(
        settings,
        client=FakeClient(),  # type: ignore[arg-type]
        actions_path=actions,
    )
    with pytest.raises(ToolError):
        await server.call_tool(
            "create_email_account",
            {
                "user": "juan",
                "domain": "dominio-invalido",
                "quota_mb": 500,
                "execute": True,
            },
        )

    entry = json.loads(settings.audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["outcome"] == "error"
    assert entry["error_code"] == "validation_error"
    assert "TOKEN-SECRETO-123" not in json.dumps(entry)
