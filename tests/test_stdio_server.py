from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import write_actions


async def test_stdio_server_lists_only_enabled_tools(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_domains", "level": "lectura", "enabled": True},
            {"name": "list_email_accounts", "level": "lectura", "enabled": True},
            {
                "name": "create_email_account",
                "level": "escritura_reversible",
                "enabled": False,
            },
        ],
    )
    env = os.environ.copy()
    env.update(
        {
            "CPANEL_HOST": "cpanel.invalid",
            "CPANEL_USER": "tester",
            "CPANEL_TOKEN": "TOKEN-SECRETO-123",
            "ALLOWED_ACTIONS_PATH": str(actions),
            "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
            "CPANEL_MCP_LOG_LEVEL": "ERROR",
            "PYTHONPATH": str(root / "src")
            + os.pathsep
            + env.get("PYTHONPATH", ""),
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cpanel_mcp"],
        env=env,
        cwd=str(root),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=15),
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "list_domains",
                "list_email_accounts",
            }

            blocked = await session.call_tool(
                "create_email_account",
                {"user": "juan", "domain": "example.com", "quota_mb": 500},
            )
            assert blocked.isError is True
