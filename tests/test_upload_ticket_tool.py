from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp.config import Settings, load_settings
from cpanel_mcp.errors import (
    ConfigError,
    CPanelConnectionError,
    CpanelMCPError,
    FileTypeNotAllowedError,
    UnsafePathError,
    ValidationError,
)
from cpanel_mcp.server import build_server
from cpanel_mcp.uploads.client import UploadTicketTool

from .conftest import make_http_client
from .test_operations import FakeClient

SECRET = "S" * 40
URL = "https://upload.acopios.test/upload/" + "t" * 43


def configured(settings: Settings) -> Settings:
    return dataclasses.replace(
        settings,
        upload_service_url="https://upload.acopios.test",
        upload_admin_secret=SECRET,
    )


def ok_handler(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={
                "upload_url": URL,
                "method": "PUT",
                "expires_at": "2026-01-01T00:05:00Z",
                "max_bytes": 5242880,
            },
        )

    return handler


async def test_creates_ticket_with_curl_example(settings: Settings) -> None:
    captured: list[httpx.Request] = []
    tool = UploadTicketTool(
        configured(settings), http=make_http_client(ok_handler(captured))
    )
    result = await tool.create_upload_ticket("criffoh/foto.jpg", overwrite=True)

    assert result["upload_url"] == URL
    assert result["method"] == "PUT"
    assert result["expires_at"] == "2026-01-01T00:05:00Z"
    assert result["curl_example"] == f"curl -sS -T foto.jpg {URL}"

    request = captured[0]
    assert request.url.path == "/admin/tickets"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    assert body == {
        "account": "tester",
        "path": "public_html/criffoh/foto.jpg",
        "overwrite": True,
    }


async def test_forwards_max_bytes_and_sha256(settings: Settings) -> None:
    captured: list[httpx.Request] = []
    tool = UploadTicketTool(
        configured(settings), http=make_http_client(ok_handler(captured))
    )
    await tool.create_upload_ticket(
        "criffoh/foto.jpg", max_bytes=50_000, expected_sha256="AB" * 32
    )
    body = json.loads(captured[0].content)
    assert body["max_bytes"] == 50_000
    assert body["expected_sha256"] == "ab" * 32


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("../x.jpg", UnsafePathError),
        ("criffoh/foto.php.jpg", FileTypeNotAllowedError),
        ("criffoh/logo.svg", FileTypeNotAllowedError),
        ("criffoh/.htaccess", FileTypeNotAllowedError),
    ],
)
async def test_invalid_paths_never_reach_the_service(
    settings: Settings, path: str, error: type[Exception]
) -> None:
    captured: list[httpx.Request] = []
    tool = UploadTicketTool(
        configured(settings), http=make_http_client(ok_handler(captured))
    )
    with pytest.raises(error):
        await tool.create_upload_ticket(path)
    assert captured == []


async def test_invalid_arguments(settings: Settings) -> None:
    tool = UploadTicketTool(configured(settings), http=make_http_client(ok_handler([])))
    with pytest.raises(ValidationError):
        await tool.create_upload_ticket("a/b.jpg", overwrite="si")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await tool.create_upload_ticket("a/b.jpg", max_bytes=0)
    with pytest.raises(ValidationError):
        await tool.create_upload_ticket("a/b.jpg", expected_sha256="zzz")


async def test_unconfigured_tool_fails_closed(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="UPLOAD_SERVICE_URL"):
        await UploadTicketTool(settings).create_upload_ticket("a/b.jpg")


async def test_service_error_message_is_surfaced(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": "rate_limited", "message": "Demasiados tickets."}
        )

    tool = UploadTicketTool(configured(settings), http=make_http_client(handler))
    with pytest.raises(CpanelMCPError, match="Demasiados tickets") as info:
        await tool.create_upload_ticket("a/b.jpg")
    assert info.value.code == "rate_limited"


async def test_connection_error_is_friendly(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    tool = UploadTicketTool(configured(settings), http=make_http_client(handler))
    with pytest.raises(CPanelConnectionError, match="UPLOAD_SERVICE_URL"):
        await tool.create_upload_ticket("a/b.jpg")


async def test_registered_and_audited_without_secrets(
    settings: Settings, tmp_path: Path
) -> None:
    server = build_server(settings, client=FakeClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "create_upload_ticket" in tools
    schema = tools["create_upload_ticket"].inputSchema
    assert schema["required"] == ["path"]
    assert schema["properties"]["overwrite"]["default"] is False
    assert "5 KB" in tools["upload_file"].description
    assert "create_upload_ticket" in tools["upload_file"].description

    # Sin configuración, la llamada falla cerrada y queda auditada sin secretos.
    with pytest.raises(ToolError):
        await server.call_tool("create_upload_ticket", {"path": "a/b.jpg"})
    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"action":"create_upload_ticket"' in audit
    assert '"outcome":"error"' in audit
    assert SECRET not in audit


def test_config_loads_upload_variables() -> None:
    env = {
        "CPANEL_HOST": "cpanel.test",
        "CPANEL_USER": "tester",
        "CPANEL_TOKEN": "tok",
        "UPLOAD_SERVICE_URL": "https://upload.acopios.test/",
        "UPLOAD_ADMIN_SECRET": SECRET,
    }
    loaded = load_settings(env)
    assert loaded.upload_service_url == "https://upload.acopios.test"
    assert SECRET not in repr(loaded)

    with pytest.raises(ConfigError, match="https"):
        load_settings({**env, "UPLOAD_SERVICE_URL": "http://upload.acopios.test"})
