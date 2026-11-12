from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp.config import Settings
from cpanel_mcp.cpanel import CPanelClient
from cpanel_mcp.errors import (
    CPanelAPIError,
    DatabaseNotFoundError,
    DatabaseUserExistsError,
    DatabaseUserNotFoundError,
    ValidationError,
)
from cpanel_mcp.operations import Operations, generate_database_password
from cpanel_mcp.server import build_server
from cpanel_mcp.validators import (
    PRIVILEGE_LEVELS,
    validate_database_user_name,
    validate_existing_database_user_ref,
    validate_privilege_level,
)

from .conftest import make_http_client, uapi_payload, write_actions
from .test_operations import FakeClient

PREFIX = "tester_"


class FakeUsersClient(FakeClient):
    def __init__(
        self,
        *,
        databases: list[str] | None = None,
        users: list[dict[str, Any]] | None = None,
        restrictions: dict[str, Any] | None = None,
        grant_fails: bool = False,
        grant_visible: bool = True,
        create_race: bool = False,
    ) -> None:
        super().__init__()
        self.databases = [
            {"database": name, "users": []}
            for name in (databases if databases is not None else ["tester_blog"])
        ]
        self.users = list(users or [])
        self.restrictions = restrictions or {
            "prefix": PREFIX,
            "max_username_length": 32,
            "max_database_name_length": 64,
        }
        self.grant_fails = grant_fails
        self.grant_visible = grant_visible
        self.create_race = create_race
        self.passwords: list[str] = []

    async def get_mysql_restrictions(self) -> dict[str, Any]:
        self.calls.append(("get_mysql_restrictions", {}))
        return dict(self.restrictions)

    async def list_databases(self) -> list[dict[str, Any]]:
        self.calls.append(("list_databases", {}))
        return list(self.databases)

    async def list_database_users(self) -> list[dict[str, Any]]:
        self.calls.append(("list_database_users", {}))
        return [dict(user) for user in self.users]

    async def create_database_user(self, name: str, password: str) -> None:
        self.calls.append(("create_database_user", {"name": name}))
        self.passwords.append(password)
        if self.create_race:
            raise DatabaseUserExistsError("ya existe")
        self.users.append({"user": name, "shortuser": name, "databases": []})

    async def set_privileges_on_database(
        self, user: str, database: str, privileges: str
    ) -> None:
        self.calls.append(
            (
                "set_privileges_on_database",
                {"user": user, "database": database, "privileges": privileges},
            )
        )
        if self.grant_fails:
            raise CPanelAPIError("cPanel rechazó Mysql::set_privileges_on_database: x")
        if self.grant_visible:
            for entry in self.users:
                if entry["user"] == user:
                    entry["databases"] = [*entry["databases"], database]

    async def set_database_user_password(self, user: str, password: str) -> None:
        self.calls.append(("set_database_user_password", {"user": user}))
        self.passwords.append(password)


def ops(client: FakeUsersClient) -> Operations:
    return Operations(client)  # type: ignore[arg-type]


def names(client: FakeUsersClient) -> list[str]:
    return [name for name, _ in client.calls]


# ------------------------------------------------------------- validación
def test_password_generator_is_strong_and_shell_safe() -> None:
    seen = {generate_database_password() for _ in range(50)}
    assert len(seen) == 50
    for password in seen:
        assert len(password) == 24 and password.isalnum()
        assert any(c.islower() for c in password)
        assert any(c.isupper() for c in password)
        assert any(c.isdigit() for c in password)
    with pytest.raises(ValueError):
        generate_database_password(8)


@pytest.mark.parametrize("name", ["app", "wp_user", "a1", "x" * 32])
def test_valid_user_names(name: str) -> None:
    assert validate_database_user_name(name) == name


@pytest.mark.parametrize(
    "name", ["", " ", "App", "1app", "_app", "mi-app", "app;x", "a b", "x" * 33, None, 3]
)
def test_invalid_user_names(name: object) -> None:
    with pytest.raises(ValidationError):
        validate_database_user_name(name)


def test_existing_user_ref_and_privilege_levels() -> None:
    assert validate_existing_database_user_ref("Tester_App1") == "Tester_App1"
    for bad in ["", "a b", "a;b", "a'b", None]:
        with pytest.raises(ValidationError):
            validate_existing_database_user_ref(bad)
    assert set(PRIVILEGE_LEVELS) == {"all", "read_write", "read_only"}
    for bad in ["ALL PRIVILEGES", "GRANT OPTION", "SELECT,FILE", "", None, 1]:
        with pytest.raises(ValidationError):
            validate_privilege_level(bad)


# --------------------------------------------------------------- listado
async def test_list_database_users() -> None:
    client = FakeUsersClient(
        users=[
            {"user": "tester_b", "shortuser": "b", "databases": ["tester_x", 5]},
            {"user": "tester_a", "databases": None},
            {"shortuser": "sin_user"},
        ]
    )
    result = await ops(client).list_database_users()
    assert result["count"] == 2
    assert [u["user"] for u in result["users"]] == ["tester_a", "tester_b"]
    assert result["users"][1]["databases"] == ["tester_x"]


# ------------------------------------------------------- crear usuario
async def test_dry_run_makes_no_calls_and_no_password() -> None:
    client = FakeUsersClient()
    result = await ops(client).create_database_user("app", "blog")
    assert client.calls == [] and client.passwords == []
    assert result["dry_run"] is True and "password" not in result


async def test_create_user_full_flow() -> None:
    client = FakeUsersClient()
    result = await ops(client).create_database_user(
        "app", "blog", execute=True
    )
    assert names(client) == [
        "get_mysql_restrictions",
        "list_databases",
        "list_database_users",
        "create_database_user",
        "set_privileges_on_database",
        "list_database_users",
    ]
    assert client.calls[3][1] == {"name": "tester_app"}
    assert client.calls[4][1] == {
        "user": "tester_app",
        "database": "tester_blog",
        "privileges": "ALL PRIVILEGES",
    }
    assert result["user"] == "tester_app" and result["database"] == "tester_blog"
    assert result["granted"] is True and result["verified"] is True
    assert result["password"] == client.passwords[0] and len(result["password"]) == 24
    assert result["host"] == "localhost"
    assert result["password"] not in result["message"]


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("all", "ALL PRIVILEGES"),
        ("read_write", "SELECT,INSERT,UPDATE,DELETE"),
        ("read_only", "SELECT"),
    ],
)
async def test_privilege_levels_map_to_fixed_lists(level: str, expected: str) -> None:
    client = FakeUsersClient()
    await ops(client).create_database_user("app", "blog", level, execute=True)
    grant = next(c for n, c in client.calls if n == "set_privileges_on_database")
    assert grant["privileges"] == expected


async def test_already_prefixed_names_are_not_double_prefixed() -> None:
    client = FakeUsersClient()
    result = await ops(client).create_database_user(
        "tester_app", "tester_blog", execute=True
    )
    assert result["user"] == "tester_app" and result["database"] == "tester_blog"


async def test_missing_database_creates_nothing() -> None:
    client = FakeUsersClient(databases=[])
    with pytest.raises(DatabaseNotFoundError, match="create_database"):
        await ops(client).create_database_user("app", "blog", execute=True)
    assert "create_database_user" not in names(client)


async def test_existing_user_is_never_modified() -> None:
    client = FakeUsersClient(users=[{"user": "tester_app", "databases": []}])
    with pytest.raises(DatabaseUserExistsError, match="change_database_user_password"):
        await ops(client).create_database_user("app", "blog", execute=True)
    assert "create_database_user" not in names(client)
    assert client.passwords == []


async def test_race_on_create_reports_existing() -> None:
    client = FakeUsersClient(create_race=True)
    with pytest.raises(DatabaseUserExistsError, match="No se modificó nada"):
        await ops(client).create_database_user("app", "blog", execute=True)


async def test_username_length_limit_includes_prefix() -> None:
    client = FakeUsersClient()
    with pytest.raises(ValidationError, match="32"):
        await ops(client).create_database_user("x" * 26, "blog", execute=True)
    assert "create_database_user" not in names(client)


async def test_grant_failure_returns_partial_result_with_password() -> None:
    client = FakeUsersClient(grant_fails=True)
    result = await ops(client).create_database_user("app", "blog", execute=True)
    assert result["user_created"] is True and result["granted"] is False
    assert result["verified"] is False
    assert result["password"] == client.passwords[0]
    assert "NO se pudieron asignar permisos" in result["warning"]


async def test_unverified_grant_returns_warning() -> None:
    client = FakeUsersClient(grant_visible=False)
    result = await ops(client).create_database_user("app", "blog", execute=True)
    assert result["granted"] is True and result["verified"] is False
    assert "warning" in result


async def test_invalid_arguments_make_no_calls() -> None:
    client = FakeUsersClient()
    with pytest.raises(ValidationError):
        await ops(client).create_database_user("app", "blog", "root", execute=True)
    with pytest.raises(ValidationError):
        await ops(client).create_database_user("A pp", "blog", execute=True)
    with pytest.raises(ValidationError):
        await ops(client).create_database_user("app", "blog", execute="yes")  # type: ignore[arg-type]
    assert client.calls == []


# ------------------------------------------------------ cambiar contraseña
def users_client() -> FakeUsersClient:
    return FakeUsersClient(
        users=[{"user": "tester_app", "databases": ["tester_blog", "tester_b2"]}]
    )


async def test_password_change_dry_run_makes_no_calls() -> None:
    client = users_client()
    result = await ops(client).change_database_user_password("app")
    assert client.calls == [] and client.passwords == []
    assert result["dry_run"] is True and result["password_changed"] is False
    assert "password" not in result


async def test_password_change_requires_matching_confirm() -> None:
    client = users_client()
    for confirm in ["", "otro", "tester_app"]:
        with pytest.raises(ValidationError, match="confirm"):
            await ops(client).change_database_user_password(
                "app", confirm=confirm, execute=True
            )
    assert client.calls == [] and client.passwords == []


async def test_password_change_resolves_prefix_and_returns_once() -> None:
    client = users_client()
    result = await ops(client).change_database_user_password(
        "app", confirm="app", execute=True
    )
    assert names(client) == [
        "get_mysql_restrictions",
        "list_database_users",
        "set_database_user_password",
    ]
    assert client.calls[2][1] == {"user": "tester_app"}
    assert result["user"] == "tester_app"
    assert result["databases"] == ["tester_b2", "tester_blog"]
    assert result["password"] == client.passwords[0] and len(result["password"]) == 24
    assert result["password_changed"] is True
    assert "wp-config" in result["warning"]


async def test_password_change_accepts_full_name() -> None:
    client = users_client()
    result = await ops(client).change_database_user_password(
        "tester_app", confirm="tester_app", execute=True
    )
    assert result["user"] == "tester_app"


async def test_password_change_unknown_user_changes_nothing() -> None:
    client = users_client()
    with pytest.raises(DatabaseUserNotFoundError, match="list_database_users"):
        await ops(client).change_database_user_password(
            "fantasma", confirm="fantasma", execute=True
        )
    assert "set_database_user_password" not in names(client)


# ----------------------------------------------- cliente UAPI: secretos por POST
async def test_passwords_travel_in_post_body_not_in_url(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=uapi_payload({}))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    await client.create_database_user("tester_app", "S3cretoLargo123")
    await client.set_database_user_password("tester_app", "OtroSecreto456")
    await client.aclose()

    for request, secret, path in [
        (seen[0], "S3cretoLargo123", "/execute/Mysql/create_user"),
        (seen[1], "OtroSecreto456", "/execute/Mysql/set_password"),
    ]:
        assert request.method == "POST" and request.url.path == path
        assert secret not in str(request.url) and not request.url.query
        body = parse_qs(request.content.decode())
        assert body["password"] == [secret]
    assert parse_qs(seen[0].content.decode())["name"] == ["tester_app"]
    assert parse_qs(seen[1].content.decode())["user"] == ["tester_app"]


async def test_uapi_user_calls_and_error_mapping(settings: Settings) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("create_user"):
            return httpx.Response(
                200,
                json=uapi_payload(
                    None, status=0, errors=["The MySQL user already exists."]
                ),
            )
        if request.url.path.endswith("list_users"):
            return httpx.Response(
                200,
                json=uapi_payload(
                    [{"user": "tester_a", "shortuser": "a", "databases": ["tester_x"]}]
                ),
            )
        return httpx.Response(200, json=uapi_payload({}))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    assert (await client.list_database_users())[0]["user"] == "tester_a"
    await client.set_privileges_on_database("tester_a", "tester_x", "SELECT")
    with pytest.raises(DatabaseUserExistsError):
        await client.create_database_user("tester_a", "xxxxxxxxxxxxxxxxxxxxxxxx")
    await client.aclose()

    assert seen[0][0] == "/execute/Mysql/list_users"
    assert seen[1] == (
        "/execute/Mysql/set_privileges_on_database",
        {"user": "tester_a", "database": "tester_x", "privileges": "SELECT"},
    )


# ----------------------------------------------- registro y auditoría
async def test_tools_registered_with_schemas(settings: Settings) -> None:
    server = build_server(settings, client=FakeUsersClient())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {
        "list_database_users",
        "create_database_user",
        "change_database_user_password",
    } <= set(tools)
    create = tools["create_database_user"].inputSchema
    assert set(create["required"]) == {"user", "database"}
    assert create["properties"]["privileges"]["default"] == "all"
    assert create["properties"]["execute"]["default"] is False
    change = tools["change_database_user_password"].inputSchema
    assert change["required"] == ["user"]
    assert change["properties"]["execute"]["default"] is False
    assert "contraseña" in tools["change_database_user_password"].description


async def test_tools_follow_enabled_flag(settings: Settings, tmp_path: Path) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_database_users", "level": "lectura", "enabled": True},
            {
                "name": "create_database_user",
                "level": "escritura_reversible",
                "enabled": False,
            },
            {
                "name": "change_database_user_password",
                "level": "alto_impacto",
                "enabled": False,
            },
        ],
    )
    server = build_server(settings, client=FakeUsersClient(), actions_path=actions)  # type: ignore[arg-type]
    assert {t.name for t in await server.list_tools()} == {"list_database_users"}
    with pytest.raises(ToolError):
        await server.call_tool(
            "change_database_user_password",
            {"user": "app", "confirm": "app", "execute": True},
        )


async def test_audit_never_contains_generated_passwords(settings: Settings) -> None:
    client = users_client()
    server = build_server(settings, client=client)  # type: ignore[arg-type]

    await server.call_tool("create_database_user", {"user": "nuevo", "database": "blog"})
    _, created = await server.call_tool(
        "create_database_user", {"user": "nuevo", "database": "blog", "execute": True}
    )
    _, changed = await server.call_tool(
        "change_database_user_password",
        {"user": "app", "confirm": "app", "execute": True},
    )

    secrets = [created["result"]["password"], changed["result"]["password"]]
    assert len(set(secrets)) == 2
    audit = settings.audit_path.read_text(encoding="utf-8")
    for secret in secrets:
        assert secret not in audit
    assert settings.token not in audit
    assert '"action":"create_database_user"' in audit
    assert '"action":"change_database_user_password"' in audit
    assert '"confirm":"app"' in audit


async def test_wrong_confirm_via_server_is_audited_as_error(settings: Settings) -> None:
    server = build_server(settings, client=users_client())  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="confirm"):
        await server.call_tool(
            "change_database_user_password",
            {"user": "app", "confirm": "no", "execute": True},
        )
    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"outcome":"error"' in audit
