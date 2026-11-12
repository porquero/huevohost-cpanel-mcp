from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp.config import Settings
from cpanel_mcp.cpanel import CPanelClient
from cpanel_mcp.errors import (
    CPanelAPIError,
    DatabaseExistsError,
    MysqlRoleDisabledError,
    ValidationError,
)
from cpanel_mcp.operations import Operations
from cpanel_mcp.server import build_server
from cpanel_mcp.validators import validate_database_name

from .conftest import make_http_client, uapi_payload, write_actions
from .test_operations import FakeClient


class FakeDbClient(FakeClient):
    def __init__(
        self,
        *,
        databases: list[dict[str, Any]] | None = None,
        restrictions: dict[str, Any] | None = None,
        verification_visible: bool = True,
        create_race: bool = False,
    ) -> None:
        super().__init__()
        self.databases = list(databases or [])
        self.restrictions = (
            {"prefix": "tester_", "max_database_name_length": 64}
            if restrictions is None
            else restrictions
        )
        self.db_verification_visible = verification_visible
        self.create_race = create_race

    async def list_databases(self) -> list[dict[str, Any]]:
        self.calls.append(("list_databases", {}))
        return list(self.databases)

    async def get_mysql_restrictions(self) -> dict[str, Any]:
        self.calls.append(("get_mysql_restrictions", {}))
        return dict(self.restrictions)

    async def create_database(self, name: str) -> None:
        self.calls.append(("create_database", {"name": name}))
        if self.create_race:
            raise DatabaseExistsError("ya existe")
        if self.db_verification_visible:
            self.databases.append({"database": name, "disk_usage": 0, "users": []})


def ops(client: FakeDbClient) -> Operations:
    return Operations(client)  # type: ignore[arg-type]


def call_names(client: FakeDbClient) -> list[str]:
    return [name for name, _ in client.calls]


# ------------------------------------------------------------- validación
@pytest.mark.parametrize("name", ["blog", "tienda_2024", "a", "wp_clientes_01"])
def test_valid_database_names(name: str) -> None:
    assert validate_database_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "Blog",
        "1blog",
        "_blog",
        "mi-blog",
        "mi blog",
        "blog;drop",
        "blog`x`",
        "bl.og",
        "ñandú",
        "a" * 64,
        None,
        5,
    ],
)
def test_invalid_database_names(name: object) -> None:
    with pytest.raises(ValidationError):
        validate_database_name(name)


# ---------------------------------------------------------------- listado
async def test_list_databases_normalizes_entries() -> None:
    client = FakeDbClient(
        databases=[
            {"database": "tester_b", "disk_usage": 2048, "users": ["tester_u"]},
            {
                "database": "tester_a",
                "disk_usage": "x",
                "users": [{"user": "tester_w"}, 5, ""],
            },
            {"disk_usage": 1},
        ]
    )
    result = await ops(client).list_databases()
    assert result["count"] == 2
    assert [db["name"] for db in result["databases"]] == ["tester_a", "tester_b"]
    assert result["databases"][0]["users"] == ["tester_w"]
    assert result["databases"][0]["disk_usage_bytes"] is None
    assert result["databases"][1]["disk_usage_bytes"] == 2048


# --------------------------------------------------------------- creación
async def test_dry_run_makes_no_calls() -> None:
    client = FakeDbClient()
    result = await ops(client).create_database("blog")
    assert client.calls == []
    assert result["dry_run"] is True
    assert result["created"] is False
    assert result["name"] == "blog"


async def test_execute_creates_with_account_prefix_and_verifies() -> None:
    client = FakeDbClient()
    result = await ops(client).create_database("blog", execute=True)
    assert call_names(client) == [
        "get_mysql_restrictions",
        "list_databases",
        "create_database",
        "list_databases",
    ]
    assert client.calls[2][1] == {"name": "tester_blog"}
    assert result["name"] == "tester_blog"
    assert result["created"] is True and result["verified"] is True
    assert "usuarios" in result["message"]


async def test_already_prefixed_name_is_not_prefixed_twice() -> None:
    client = FakeDbClient()
    result = await ops(client).create_database("tester_blog", execute=True)
    assert result["name"] == "tester_blog"


async def test_works_when_prefixing_is_disabled() -> None:
    client = FakeDbClient(restrictions={"prefix": ""})
    result = await ops(client).create_database("blog", execute=True)
    assert result["name"] == "blog"


async def test_existing_database_is_not_touched() -> None:
    client = FakeDbClient(databases=[{"database": "tester_blog", "users": []}])
    with pytest.raises(DatabaseExistsError, match="tester_blog"):
        await ops(client).create_database("blog", execute=True)
    assert "create_database" not in call_names(client)


async def test_race_on_create_is_reported_as_existing() -> None:
    client = FakeDbClient(create_race=True)
    with pytest.raises(DatabaseExistsError, match="No se modificó nada"):
        await ops(client).create_database("blog", execute=True)


async def test_name_longer_than_cpanel_limit_is_rejected() -> None:
    client = FakeDbClient(
        restrictions={"prefix": "tester_", "max_database_name_length": 10}
    )
    with pytest.raises(ValidationError, match="10"):
        await ops(client).create_database("blog_largo", execute=True)
    assert "create_database" not in call_names(client)


async def test_unexpected_prefix_is_refused() -> None:
    client = FakeDbClient(restrictions={"prefix": "x; DROP"})
    with pytest.raises(Exception, match="prefijo"):
        await ops(client).create_database("blog", execute=True)
    assert "create_database" not in call_names(client)


async def test_missing_verification_returns_warning() -> None:
    client = FakeDbClient(verification_visible=False)
    result = await ops(client).create_database("blog", execute=True)
    assert result["created"] is True and result["verified"] is False
    assert "warning" in result


@pytest.mark.parametrize("value", ["true", 1, None])
async def test_execute_must_be_boolean(value: object) -> None:
    client = FakeDbClient()
    with pytest.raises(ValidationError):
        await ops(client).create_database("blog", execute=value)  # type: ignore[arg-type]
    assert client.calls == []


# ------------------------------------------------------- cliente UAPI real
async def test_uapi_calls_and_params(settings: Settings) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("list_databases"):
            return httpx.Response(
                200, json=uapi_payload([{"database": "tester_a", "users": []}])
            )
        if request.url.path.endswith("get_restrictions"):
            return httpx.Response(200, json=uapi_payload({"prefix": "tester_"}))
        return httpx.Response(200, json=uapi_payload({}))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    assert (await client.list_databases())[0]["database"] == "tester_a"
    assert (await client.get_mysql_restrictions())["prefix"] == "tester_"
    await client.create_database("tester_blog")
    await client.aclose()

    assert seen[0][0] == "/execute/Mysql/list_databases"
    assert seen[1][0] == "/execute/Mysql/get_restrictions"
    assert seen[2] == ("/execute/Mysql/create_database", {"name": "tester_blog"})


async def test_uapi_already_exists_maps_to_database_exists(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=uapi_payload(
                None, status=0, errors=["The database “tester_blog” already exists."]
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(DatabaseExistsError):
        await client.create_database("tester_blog")
    await client.aclose()


async def test_uapi_disabled_role_maps_to_clear_error(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=uapi_payload(
                None, status=0, errors=["The MySQL role is not enabled for this account."]
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(MysqlRoleDisabledError):
        await client.list_databases()
    await client.aclose()


async def test_uapi_other_errors_stay_generic(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=uapi_payload(None, status=0, errors=["Nombre inválido."])
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelAPIError, match="Mysql::create_database"):
        await client.create_database("x")
    await client.aclose()


# ------------------------------------------------- registro y auditoría
async def test_tools_registered_with_expected_schema(settings: Settings) -> None:
    server = build_server(settings, client=FakeDbClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {"list_databases", "create_database"} <= set(tools)
    schema = tools["create_database"].inputSchema
    assert schema["required"] == ["name"]
    assert schema["properties"]["execute"]["default"] is False
    # Nunca borrado de bases, GRANT libre ni acceso remoto.
    forbidden = ("drop", "delete_database", "grant", "remote", "add_host")
    assert not [
        name for name in tools if any(word in name for word in forbidden)
    ]


async def test_tools_follow_enabled_flag(settings: Settings, tmp_path: Path) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_databases", "level": "lectura", "enabled": True},
            {
                "name": "create_database",
                "level": "escritura_reversible",
                "enabled": False,
            },
        ],
    )
    server = build_server(settings, client=FakeDbClient(), actions_path=actions)  # type: ignore[arg-type]
    assert {tool.name for tool in await server.list_tools()} == {"list_databases"}
    with pytest.raises(ToolError):
        await server.call_tool("create_database", {"name": "blog", "execute": True})


async def test_dry_run_and_execution_are_audited_without_secrets(
    settings: Settings,
) -> None:
    client = FakeDbClient()
    server = build_server(settings, client=client)  # type: ignore[arg-type]

    _, dry = await server.call_tool("create_database", {"name": "blog"})
    assert dry["result"]["dry_run"] is True
    assert client.calls == []

    _, real = await server.call_tool(
        "create_database", {"name": "blog", "execute": True}
    )
    assert real["result"]["name"] == "tester_blog"
    await server.call_tool("list_databases", {})

    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"action":"create_database"' in audit
    assert '"dry_run":true' in audit and '"verified":true' in audit
    assert '"action":"list_databases"' in audit
    assert settings.token not in audit
