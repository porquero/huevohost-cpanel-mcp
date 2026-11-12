from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cpanel_mcp import wordpress
from cpanel_mcp.config import Settings
from cpanel_mcp.cpanel import CPanelClient
from cpanel_mcp.errors import (
    CPanelAPIError,
    CPanelAuthError,
    CPanelPermissionError,
    FileAlreadyExistsError,
    FileOperationError,
    RemoteFileNotFoundError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
    WordPressPackageError,
)
from cpanel_mcp.server import build_server
from cpanel_mcp.wordpress import (
    TRANSFER_TIMEOUT_SECONDS,
    WordPressInstaller,
    repack_without_top_level,
)

from .conftest import make_http_client, uapi_payload, write_actions
from .test_file_ops import FakeFileClient

HOME = "/home/tester"


# ---------------------------------------------------------------- fixtures
def build_zip(extra: dict[str, bytes] | None = None, *, skip: set[str] | None = None) -> bytes:
    members: dict[str, bytes] = {
        "wordpress/index.php": b"<?php // silence",
        "wordpress/wp-load.php": b"<?php",
        "wordpress/wp-config-sample.php": b"<?php",
        "wordpress/wp-settings.php": b"<?php",
        "wordpress/wp-admin/index.php": b"<?php",
        "wordpress/wp-includes/version.php": b"<?php\n$wp_version = '6.8.1';\n",
        "wordpress/wp-content/index.php": b"<?php",
        "wordpress/wp-content/plugins/index.php": b"<?php",
    }
    members.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("wordpress/"), b"")
        archive.writestr(zipfile.ZipInfo("wordpress/wp-admin/"), b"")
        for name, data in members.items():
            if name.removeprefix("wordpress/") in (skip or set()):
                continue
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeWpClient(FakeFileClient):
    def __init__(
        self,
        *,
        extract_fails: bool = False,
        trash_zip_fails: bool = False,
        upload_fails: bool = False,
        skip_extracted: set[str] | None = None,
        subdomains: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.extract_fails = extract_fails
        self.trash_zip_fails = trash_zip_fails
        self.upload_fails = upload_fails
        self.skip_extracted = skip_extracted or set()
        self.subdomains = subdomains if subdomains is not None else [
            {"domain": "wp.example.com", "basedir": "public_html/site"}
        ]
        self.timeouts: dict[str, float | None] = {}
        self.extract_args: tuple[str, str] | None = None

    async def upload_file(self, directory, filename, content, *, overwrite, timeout=None):  # type: ignore[override]
        if self.upload_fails:
            raise CPanelAPIError("cPanel rechazó la subida")
        self.timeouts["upload"] = timeout
        await super().upload_file(directory, filename, content, overwrite=overwrite)

    async def get_home_directory(self) -> str:
        self.calls.append(("get_home_directory", None))
        return HOME

    async def extract_archive(self, source, destination, *, timeout=None):  # type: ignore[no-untyped-def]
        self.calls.append(("extract_archive", (source, destination)))
        self.extract_args = (source, destination)
        self.timeouts["extract"] = timeout
        if self.extract_fails:
            raise FileOperationError("cPanel no pudo extraer el archivo: disco lleno")
        archive = zipfile.ZipFile(io.BytesIO(self.files[source]))
        assert destination.startswith(HOME + "/")
        base = destination[len(HOME) + 1:]
        for info in archive.infolist():
            if info.filename in self.skip_extracted:
                continue
            if info.is_dir():
                self.dirs.add(f"{base}/{info.filename.rstrip('/')}")
                continue
            parts = info.filename.split("/")
            for index in range(1, len(parts)):
                self.dirs.add(base + "/" + "/".join(parts[:index]))
            self.files[f"{base}/{info.filename}"] = archive.read(info)

    async def trash_file(self, path: str) -> dict[str, Any]:
        if self.trash_zip_fails and path.endswith(".zip"):
            raise CPanelAPIError("papelera no disponible")
        return await super().trash_file(path)

    async def list_subdomains(self) -> list[dict[str, Any]]:
        self.calls.append(("list_subdomains", None))
        return list(self.subdomains)


class FakeWordPressOrg:
    def __init__(
        self,
        package: bytes | None = None,
        *,
        sha1: str | None = None,
        redirect_to: str | None = "https://downloads.wordpress.org/release/wordpress-6.8.1.zip",
    ) -> None:
        self.package = package if package is not None else build_zip()
        self.sha1 = sha1 or hashlib.sha1(self.package).hexdigest()
        self.redirect_to = redirect_to
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == "https://wordpress.org/latest.zip.sha1":
            return httpx.Response(200, text=self.sha1 + "\n")
        if url == "https://wordpress.org/latest.zip":
            if self.redirect_to:
                return httpx.Response(302, headers={"location": self.redirect_to})
            return httpx.Response(200, content=self.package)
        if self.redirect_to and url == self.redirect_to:
            return httpx.Response(200, content=self.package)
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def installer(client: FakeWpClient, org: FakeWordPressOrg) -> WordPressInstaller:
    return WordPressInstaller(client, http=org.client())  # type: ignore[arg-type]


def site_client(**kwargs: Any) -> FakeWpClient:
    return FakeWpClient(dirs={"public_html/site"}, **kwargs)


# ------------------------------------------------------------------ dry-run
async def test_dry_run_contacts_nobody() -> None:
    client, org = site_client(), FakeWordPressOrg()
    result = await installer(client, org).prepare_wordpress("site")
    assert client.calls == [] and org.requests == []
    assert result["dry_run"] is True and result["prepared"] is False
    assert "navegador" in result["message"]


# ---------------------------------------------------------------- flujo real
async def test_prepares_wordpress_end_to_end() -> None:
    client, org = site_client(), FakeWordPressOrg()
    result = await installer(client, org).prepare_wordpress("site", execute=True)

    # Archivos en la raíz del directorio, sin la carpeta wordpress/.
    for name in ("index.php", "wp-load.php", "wp-config-sample.php", "wp-admin/index.php"):
        assert f"public_html/site/{name}" in client.files
    assert not [path for path in client.files if "/wordpress/" in path]
    # El zip se subió y se envió a la papelera.
    assert not [path for path in client.files if path.endswith(".zip")]
    assert any(path.endswith(".zip") for path in client.trashed)
    zip_path = client.extract_args[0]  # type: ignore[index]
    assert zip_path.startswith("public_html/site/wordpress-install-")
    assert client.extract_args[1] == f"{HOME}/public_html/site"  # type: ignore[index]
    # Transferencias grandes con timeout ampliado.
    assert client.timeouts == {
        "upload": TRANSFER_TIMEOUT_SECONDS,
        "extract": TRANSFER_TIMEOUT_SECONDS,
    }
    # Descarga solo de wordpress.org, con redirección a su subdominio.
    assert org.requests == [
        "https://wordpress.org/latest.zip.sha1",
        "https://wordpress.org/latest.zip",
        "https://downloads.wordpress.org/release/wordpress-6.8.1.zip",
    ]
    assert result["prepared"] is True and result["verified"] is True
    assert result["version"] == "6.8.1"
    assert result["zip_removed"] is True
    assert result["site_url"] == "https://wp.example.com"
    assert "https://wp.example.com" in result["next_steps"][0]
    assert "NO está instalado" in result["message"]
    assert "warning" not in result


async def test_site_url_unknown_still_returns_steps() -> None:
    client = site_client(subdomains=[])
    result = await installer(client, FakeWordPressOrg()).prepare_wordpress(
        "site", execute=True
    )
    assert result["site_url"] is None
    assert "dominio" in result["next_steps"][0]


async def test_harmless_default_entries_are_allowed() -> None:
    client = FakeWpClient(
        dirs={"public_html/site", "public_html/site/cgi-bin", "public_html/site/.well-known"}
    )
    result = await installer(client, FakeWordPressOrg()).prepare_wordpress(
        "site", execute=True
    )
    assert result["prepared"] is True


# ------------------------------------------------------------- descarga
async def test_checksum_mismatch_uploads_nothing() -> None:
    client, org = site_client(), FakeWordPressOrg(sha1="0" * 40)
    with pytest.raises(WordPressPackageError, match="SHA-1"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert "upload_file" not in [name for name, _ in client.calls]
    assert client.files == {}


async def test_unreadable_checksum_aborts() -> None:
    client, org = site_client(), FakeWordPressOrg(sha1="no es un hash")
    org.sha1 = "no es un hash"
    with pytest.raises(WordPressPackageError, match="SHA-1 oficial"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert client.files == {}


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example.com/wordpress.zip",
        "http://downloads.wordpress.org/x.zip",
        "https://wordpress.org.evil.com/x.zip",
        "https://evilwordpress.org/x.zip",
    ],
)
async def test_redirects_outside_wordpress_org_are_refused(target: str) -> None:
    client, org = site_client(), FakeWordPressOrg(redirect_to=target)
    with pytest.raises(WordPressPackageError, match="no permitido"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert target not in org.requests
    assert client.files == {}


async def test_too_many_redirects() -> None:
    client = site_client()

    def loop(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(".sha1"):
            return httpx.Response(200, text="a" * 40)
        return httpx.Response(302, headers={"location": "https://wordpress.org/latest.zip"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(loop))
    with pytest.raises(WordPressPackageError, match="redirecciones"):
        await WordPressInstaller(client, http=http).prepare_wordpress(  # type: ignore[arg-type]
            "site", execute=True
        )


async def test_http_errors_are_friendly() -> None:
    client = site_client()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    with pytest.raises(WordPressPackageError, match="conexión a internet"):
        await WordPressInstaller(client, http=http).prepare_wordpress(  # type: ignore[arg-type]
            "site", execute=True
        )

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    with pytest.raises(WordPressPackageError, match="503"):
        await WordPressInstaller(client, http=http).prepare_wordpress(  # type: ignore[arg-type]
            "site", execute=True
        )


async def test_oversized_download_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wordpress, "MAX_DOWNLOAD_BYTES", 100)
    client = site_client()
    with pytest.raises(WordPressPackageError, match="tamaño máximo"):
        await installer(client, FakeWordPressOrg()).prepare_wordpress("site", execute=True)
    assert client.files == {}


# ------------------------------------------------------- validación del zip
def malicious_zip(name: str, *, symlink: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("wordpress/index.php", b"<?php")
        info = zipfile.ZipInfo(name)
        if symlink:
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"/etc/passwd" if symlink else b"x")
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "wordpress/../evil.php",
        "wordpress/wp-content/../../evil.php",
        "evil.php",
        "/etc/cron.d/x",
        "wordpress/./a.php",
        # zipfile normaliza "\" en Windows y recorta en el byte NUL al leer
        # (ambos casos son inocuos allí); en Linux los rechaza nuestro filtro.
        pytest.param(
            "wordpress\\evil.php",
            marks=pytest.mark.skipif(os.sep == "\\", reason="zipfile normaliza '\\' en Windows"),
        ),
    ],
)
async def test_dangerous_zip_members_are_rejected(name: str) -> None:
    client, org = site_client(), FakeWordPressOrg(malicious_zip(name))
    with pytest.raises(WordPressPackageError, match="rutas no permitidas"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert client.files == {}


async def test_symlink_members_are_rejected() -> None:
    client = site_client()
    org = FakeWordPressOrg(malicious_zip("wordpress/link.php", symlink=True))
    with pytest.raises(WordPressPackageError, match="enlaces simbólicos"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert client.files == {}


async def test_not_a_zip_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    client = site_client()
    with pytest.raises(WordPressPackageError, match="zip válido"):
        await installer(client, FakeWordPressOrg(b"esto no es un zip")).prepare_wordpress(
            "site", execute=True
        )
    monkeypatch.setattr(wordpress, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(WordPressPackageError, match="expande"):
        repack_without_top_level(build_zip())
    monkeypatch.setattr(wordpress, "MAX_UNCOMPRESSED_BYTES", 10**9)
    monkeypatch.setattr(wordpress, "MAX_MEMBERS", 3)
    with pytest.raises(WordPressPackageError, match="tamaño inesperado"):
        repack_without_top_level(build_zip())
    assert client.files == {}


def test_repack_strips_top_level_and_reports_version() -> None:
    repacked, version, count = repack_without_top_level(build_zip())
    names = zipfile.ZipFile(io.BytesIO(repacked)).namelist()
    assert "index.php" in names and "wp-admin/" in names
    assert not [name for name in names if name.startswith("wordpress")]
    assert version == "6.8.1" and count == 8


# ---------------------------------------------------- validación del destino
async def test_non_empty_directory_is_never_touched() -> None:
    client = FakeWpClient(
        dirs={"public_html/site"},
        files={"public_html/site/index.html": "sitio existente"},
    )
    org = FakeWordPressOrg()
    with pytest.raises(FileAlreadyExistsError, match="no está vacío"):
        await installer(client, org).prepare_wordpress("site", execute=True)
    assert org.requests == []  # ni siquiera se descargó
    assert client.files == {"public_html/site/index.html": "sitio existente"}


async def test_missing_directory_points_to_create_subdomain() -> None:
    client = FakeWpClient()
    with pytest.raises(RemoteFileNotFoundError, match="create_subdomain"):
        await installer(client, FakeWordPressOrg()).prepare_wordpress("nuevo", execute=True)


@pytest.mark.parametrize("bad", ["public_html", "/public_html", "../x", "site/../..", ""])
async def test_unsafe_directories_are_rejected(bad: str) -> None:
    client = site_client()
    with pytest.raises(UnsafePathError):
        await installer(client, FakeWordPressOrg()).prepare_wordpress(bad, execute=True)
    assert client.calls == []


async def test_reserved_and_symlink_directories() -> None:
    client = FakeWpClient(
        dirs={"public_html/site", "public_html/enlace"}, symlinks={"public_html/enlace"}
    )
    with pytest.raises(UnsafePathError):
        await installer(client, FakeWordPressOrg()).prepare_wordpress(
            "site/.git", execute=True
        )
    with pytest.raises(SymlinkError):
        await installer(client, FakeWordPressOrg()).prepare_wordpress(
            "enlace", execute=True
        )


async def test_execute_must_be_boolean() -> None:
    client = site_client()
    with pytest.raises(ValidationError):
        await installer(client, FakeWordPressOrg()).prepare_wordpress(
            "site", execute="si"  # type: ignore[arg-type]
        )
    assert client.calls == []


# ------------------------------------------------------------- fallos
async def test_upload_failure_changes_nothing_else() -> None:
    client = site_client(upload_fails=True)
    with pytest.raises(CPanelAPIError):
        await installer(client, FakeWordPressOrg()).prepare_wordpress("site", execute=True)
    names = [name for name, _ in client.calls]
    assert "extract_archive" not in names and "trash_file" not in names


async def test_extract_failure_removes_zip_and_warns_about_partial_files() -> None:
    client = site_client(extract_fails=True)
    with pytest.raises(FileOperationError, match="a medias") as info:
        await installer(client, FakeWordPressOrg()).prepare_wordpress("site", execute=True)
    assert "disco lleno" in str(info.value)
    assert not [path for path in client.files if path.endswith(".zip")]
    assert any(path.endswith(".zip") for path in client.trashed)


async def test_zip_trash_failure_is_reported_as_warning() -> None:
    client = site_client(trash_zip_fails=True)
    result = await installer(client, FakeWordPressOrg()).prepare_wordpress(
        "site", execute=True
    )
    assert result["prepared"] is True and result["zip_removed"] is False
    assert "wordpress-install-" in result["warning"]


async def test_missing_files_after_extraction_are_flagged() -> None:
    client = site_client(skip_extracted={"wp-load.php"})
    result = await installer(client, FakeWordPressOrg()).prepare_wordpress(
        "site", execute=True
    )
    assert result["verified"] is False
    assert "wp-load.php" in result["warning"]


# ----------------------------------------------- cliente UAPI / API 2 real
async def test_extract_archive_request_and_success(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "cpanelresult": {
                    "apiversion": 2,
                    "func": "fileop",
                    "event": {"result": 1},
                    "data": [{"result": 1, "src": "x", "dest": "y", "output": "ok"}],
                }
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    await client.extract_archive(
        "public_html/site/w.zip", "/home/tester/public_html/site", timeout=300
    )
    await client.aclose()

    request = seen[0]
    assert request.method == "POST" and request.url.path == "/json-api/cpanel"
    assert request.headers["authorization"] == "cpanel tester:TOKEN-SECRETO-123"
    from urllib.parse import parse_qs

    body = parse_qs(request.content.decode())
    assert body["cpanel_jsonapi_module"] == ["Fileman"]
    assert body["cpanel_jsonapi_func"] == ["fileop"]
    assert body["cpanel_jsonapi_apiversion"] == ["2"]
    assert body["op"] == ["extract"]
    assert body["sourcefiles"] == ["public_html/site/w.zip"]
    assert body["destfiles"] == ["/home/tester/public_html/site"]
    assert request.extensions["timeout"]["read"] == 300


@pytest.mark.parametrize(
    "payload",
    [
        {"cpanelresult": {"error": "fuera de tu directorio", "data": [{"result": 0, "err": "fuera de tu directorio"}]}},
        {"cpanelresult": {"data": [{"result": 0, "err": "x"}]}},
        {"cpanelresult": {"data": []}},
        {"cpanelresult": "raro"},
    ],
)
async def test_extract_archive_failures(settings: Settings, payload: dict[str, Any]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises((FileOperationError, Exception)):
        await client.extract_archive("a.zip", "/home/tester/x")
    await client.aclose()


async def test_extract_archive_http_errors(settings: Settings) -> None:
    for status, error in [(401, CPanelAuthError), (403, CPanelPermissionError)]:
        client = CPanelClient(
            settings,
            http_client=make_http_client(lambda _r, s=status: httpx.Response(s, text="no")),
        )
        with pytest.raises(error):
            await client.extract_archive("a.zip", "/home/tester/x")
        await client.aclose()


async def test_home_directory_and_long_upload_timeout(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("get_user_information"):
            return httpx.Response(200, json=uapi_payload({"home": "/home/tester/"}))
        return httpx.Response(200, json=uapi_payload({"failed": 0, "uploads": []}))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    assert await client.get_home_directory() == "/home/tester"
    await client.upload_file("public_html/s", "w.zip", b"zip", overwrite=False, timeout=300)
    await client.upload_file("public_html/s", "a.txt", b"x", overwrite=False)
    await client.aclose()

    assert seen[0].url.params["name"] == "home"
    assert seen[1].extensions["timeout"]["read"] == 300
    assert seen[2].extensions["timeout"]["read"] == 30.0  # el resto sigue en 30 s


# ----------------------------------------------- registro y auditoría
async def test_tool_registered_audited_and_follows_enabled(
    settings: Settings, tmp_path: Path
) -> None:
    client = site_client()
    server = build_server(settings, client=client)  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = tools["prepare_wordpress"].inputSchema
    assert schema["required"] == ["directory"]
    assert schema["properties"]["execute"]["default"] is False
    assert "navegador" in tools["prepare_wordpress"].description

    _, dry = await server.call_tool("prepare_wordpress", {"directory": "site"})
    assert dry["result"]["dry_run"] is True and client.calls == []
    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"action":"prepare_wordpress"' in audit and '"dry_run":true' in audit
    assert settings.token not in audit

    disabled = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "list_domains", "level": "lectura", "enabled": True},
            {"name": "prepare_wordpress", "level": "alto_impacto", "enabled": False},
        ],
    )
    server = build_server(settings, client=client, actions_path=disabled)  # type: ignore[arg-type]
    assert {t.name for t in await server.list_tools()} == {"list_domains"}
    with pytest.raises(ToolError):
        await server.call_tool("prepare_wordpress", {"directory": "site", "execute": True})


def test_instructions_describe_the_wordpress_flow() -> None:
    from cpanel_mcp.server import INSTRUCTIONS

    for step in ("create_subdomain", "create_database", "create_database_user", "prepare_wordpress"):
        assert step in INSTRUCTIONS
    assert "navegador" in INSTRUCTIONS
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "allowed_actions.json").read_text(encoding="utf-8")
    )
    spec = next(a for a in data["actions"] if a["name"] == "prepare_wordpress")
    assert spec["level"] == "alto_impacto"
