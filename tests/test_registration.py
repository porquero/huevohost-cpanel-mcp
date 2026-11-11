from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp.config import Settings
from cpanel_mcp.errors import ConfigError
from cpanel_mcp.server import build_server

from .conftest import write_actions
from .test_operations import FakeClient


async def test_only_enabled_tools_are_registered(
    settings: Settings, tmp_path: Path
) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_domains", "level": "lectura", "enabled": True},
            {"name": "list_email_accounts", "level": "lectura", "enabled": True},
            {
                "name": "create_email_account",
                "level": "escritura_reversible",
                "enabled": True,
            },
            {
                "name": "publish_landing_page",
                "level": "alto_impacto",
                "enabled": False,
            },
        ],
    )
    server = build_server(settings, client=FakeClient(), actions_path=actions)  # type: ignore[arg-type]
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "list_domains",
        "list_email_accounts",
        "create_email_account",
    }


async def test_disabled_tool_does_not_exist(
    settings: Settings, tmp_path: Path
) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_domains", "level": "lectura", "enabled": True},
            {
                "name": "create_email_account",
                "level": "escritura_reversible",
                "enabled": False,
            },
        ],
    )
    server = build_server(settings, client=FakeClient(), actions_path=actions)  # type: ignore[arg-type]
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {"list_domains"}

    with pytest.raises(ToolError):
        await server.call_tool(
            "create_email_account",
            {"user": "juan", "domain": "example.com", "quota_mb": 500},
        )


async def test_enabled_but_unimplemented_action_fails_closed(
    settings: Settings, tmp_path: Path
) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {
                "name": "publish_landing_page",
                "level": "alto_impacto",
                "enabled": True,
            }
        ],
    )
    with pytest.raises(ConfigError):
        build_server(settings, client=FakeClient(), actions_path=actions)  # type: ignore[arg-type]


async def test_every_tool_has_description_and_schema(
    settings: Settings,
) -> None:
    server = build_server(settings, client=FakeClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert tools["list_domains"].description
    assert "domain" in tools["list_email_accounts"].inputSchema["properties"]
    create_schema = tools["create_email_account"].inputSchema
    assert create_schema["properties"]["execute"]["default"] is False
    assert set(create_schema["required"]) >= {"user", "domain", "quota_mb"}


async def test_call_tool_returns_structured_result(
    settings: Settings,
) -> None:
    server = build_server(settings, client=FakeClient())  # type: ignore[arg-type]
    content, structured = await server.call_tool("list_domains", {})
    assert content
    assert structured["result"]["main_domain"] == "example.com"


async def test_subdomain_tool_is_registered_with_expected_schema(
    settings: Settings,
) -> None:
    server = build_server(settings, client=FakeClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "create_subdomain" in tools

    schema = tools["create_subdomain"].inputSchema
    assert {"subdomain", "domain"} <= set(schema["required"])
    assert schema["properties"]["execute"]["default"] is False
    assert schema["properties"]["directory"]["default"] is None
    assert "subdominio" in tools["create_subdomain"].description.lower()


async def test_file_tools_are_registered_with_expected_schemas(
    settings: Settings,
) -> None:
    server = build_server(settings, client=FakeClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {
        "list_files",
        "download_file",
        "create_file",
        "upload_file",
        "delete_file",
    } <= set(tools)

    assert "path" in tools["list_files"].inputSchema["properties"]
    assert "show_hidden" in tools["list_files"].inputSchema["properties"]
    assert "path" in tools["download_file"].inputSchema["properties"]
    assert "content" in tools["create_file"].inputSchema["properties"]
    assert "overwrite" in tools["create_file"].inputSchema["properties"]
    upload_schema = tools["upload_file"].inputSchema
    assert {"content", "content_base64", "overwrite"} <= set(
        upload_schema["properties"]
    )
    delete_schema = tools["delete_file"].inputSchema
    assert {"path", "confirm"} <= set(delete_schema["required"])
    assert "papelera" in tools["delete_file"].description
