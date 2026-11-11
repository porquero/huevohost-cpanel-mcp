from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from cpanel_mcp.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="cpanel.test",
        user="tester",
        token="TOKEN-SECRETO-123",
        port=2083,
        verify_ssl=True,
        audit_path=tmp_path / "audit.jsonl",
        allowed_actions_path=Path(__file__).resolve().parents[1]
        / "allowed_actions.json",
        log_level="ERROR",
    )


def uapi_payload(
    data: object,
    *,
    status: int = 1,
    errors: list[str] | None = None,
    messages: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    return {
        "apiversion": 3,
        "func": "test",
        "module": "Test",
        "result": {
            "data": data,
            "status": status,
            "errors": errors,
            "messages": messages,
            "warnings": warnings,
            "metadata": {},
        },
    }


def make_http_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def write_actions(path: Path, actions: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"version": 1, "actions": actions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
