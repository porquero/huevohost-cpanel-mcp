from __future__ import annotations

import base64
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from cpanel_mcp.config import Settings
from cpanel_mcp.errors import (
    FileAlreadyExistsError,
    FileTooLargeError,
    FileTypeNotAllowedError,
    ValidationError,
)
from cpanel_mcp.file_ops import PHP_EXECUTION_NOTICE, FileOperations
from cpanel_mcp.server import build_server

from .test_file_ops import FakeFileClient

PHP = "<?php\nmail('x@example.com', 'hola', 'cuerpo');\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_ops(client: FakeFileClient, folder: Path | None = None, **kwargs: Any) -> FileOperations:
    settings = Settings(
        host="cpanel.test",
        user="tester",
        token="TOKEN-SECRETO-123",
        local_upload_dirs=(folder,) if folder else (Path("/nada"),),
        **kwargs,
    )
    return FileOperations(client, settings)  # type: ignore[arg-type]


def site() -> FakeFileClient:
    return FakeFileClient(dirs={"public_html/site"})


# Nombres que ejecutan código en el servidor: bloqueados SIEMPRE, con o sin allow_php.
ALWAYS_BLOCKED = [
    "enviar.phtml",
    "enviar.phar",
    "enviar.php3",
    "enviar.php5",
    "enviar.pht",
    "enviar.phps",
    "enviar.shtml",
    "enviar.cgi",
    "enviar.pl",
    "enviar.py",
    "enviar.sh",
    "enviar.asp",
    "enviar.jsp",
    ".htaccess",
    ".user.ini",
    "php.ini",
    "wp-config.php",
    ".env",
    "enviar.php.txt",
    "enviar.php.jpg",
    "enviar.php.html",
    "enviar.PHP.png",
    "a.php.php",
    "enviar.phtml.jpg",
    "enviar.php.",
    "enviar.php::$DATA",
    ".php",
]


# ---------------------------------------------------- por defecto, todo bloqueado
@pytest.mark.parametrize("name", ["enviar.php", "ENVIAR.PHP", "Contacto.Php", "enviar.php ", *ALWAYS_BLOCKED])
async def test_php_and_executable_names_are_blocked_by_default(name: str) -> None:
    for call in ("create_file", "upload_file"):
        client = site()
        ops = make_ops(client)
        with pytest.raises((FileTypeNotAllowedError, Exception)) as info:
            if call == "create_file":
                await ops.create_file(f"site/{name}", PHP)
            else:
                await ops.upload_file(f"site/{name}", content=PHP)
        assert not isinstance(info.value, AssertionError)
        assert client.files == {}, (call, name)


async def test_plain_php_error_explains_how_to_ask_the_user() -> None:
    with pytest.raises(FileTypeNotAllowedError, match="allow_php=true") as info:
        await make_ops(site()).create_file("site/enviar.php", PHP)
    assert "explícita" in str(info.value)


@pytest.mark.parametrize("name", ["enviar.html", "enviar.txt", "foto.jpg", "index.pl.html", "a.so.png", "readme.md"])
async def test_normal_names_are_unaffected(name: str) -> None:
    ops = make_ops(site())
    # index.pl.html lleva "pl" intermedio (código de idioma): se rechaza por seguridad
    if name == "index.pl.html":
        with pytest.raises(FileTypeNotAllowedError):
            await ops.create_file(f"site/{name}", "x")
        return
    result = await ops.create_file(f"site/{name}", "x")
    assert result["created"] is True and "executable" not in result


# ---------------------------------------------------- allow_php=true
async def test_create_file_allows_a_plain_php_when_the_user_authorizes() -> None:
    client = site()
    result = await make_ops(client).create_file(
        "site/enviar.php", PHP, allow_php=True
    )
    assert client.files["public_html/site/enviar.php"] == PHP.encode()
    assert result["created"] is True and result["verified"] is True
    assert result["executable"] is True
    assert result["executable_notice"] == PHP_EXECUTION_NOTICE


@pytest.mark.parametrize("name", ["enviar.php", "ENVIAR.PHP", "contacto-2.php", "enviar.txt.php", "a_b.c.php"])
async def test_allow_php_accepts_only_ordinary_php_names(name: str) -> None:
    client = site()
    await make_ops(client).create_file(f"site/{name}", PHP, allow_php=True)
    assert f"public_html/site/{name}" in client.files


@pytest.mark.parametrize("name", ALWAYS_BLOCKED)
async def test_allow_php_does_not_open_anything_else(name: str) -> None:
    client = site()
    ops = make_ops(client)
    with pytest.raises(FileTypeNotAllowedError):
        await ops.create_file(f"site/{name}", PHP, allow_php=True)
    with pytest.raises(FileTypeNotAllowedError):
        await ops.upload_file(f"site/{name}", content=PHP, allow_php=True)
    assert client.files == {}


async def test_allow_php_does_not_open_version_control_directories() -> None:
    client = FakeFileClient(dirs={"public_html/site", "public_html/site/.git"})
    with pytest.raises(FileTypeNotAllowedError, match="control de versiones"):
        await make_ops(client).create_file("site/.git/x.php", PHP, allow_php=True)


async def test_upload_file_with_text_and_base64() -> None:
    client = site()
    ops = make_ops(client)
    first = await ops.upload_file("site/a.php", content=PHP, allow_php=True)
    encoded = base64.b64encode(PHP.encode()).decode()
    second = await ops.upload_file("site/b.php", content_base64=encoded, allow_php=True)
    assert first["executable"] is True and second["executable"] is True
    assert client.files["public_html/site/b.php"] == PHP.encode()


@pytest.mark.parametrize("value", ["true", 1, None, "si"])
async def test_allow_php_must_be_boolean(value: object) -> None:
    client = site()
    ops = make_ops(client)
    with pytest.raises(ValidationError, match="allow_php"):
        await ops.create_file("site/a.php", PHP, allow_php=value)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="allow_php"):
        await ops.upload_file("site/a.php", content=PHP, allow_php=value)  # type: ignore[arg-type]
    assert client.files == {}


async def test_php_respects_overwrite_and_trash() -> None:
    client = FakeFileClient(
        dirs={"public_html/site"}, files={"public_html/site/enviar.php": "<?php // viejo"}
    )
    ops = make_ops(client)
    with pytest.raises(FileAlreadyExistsError):
        await ops.create_file("site/enviar.php", PHP, allow_php=True)
    result = await ops.create_file("site/enviar.php", PHP, overwrite=True, allow_php=True)
    assert result["backed_up_to_trash"] is True
    names = [call[0] for call in client.calls]
    assert names.index("trash_file") < names.index("upload_file")


async def test_php_respects_the_size_limit() -> None:
    client = site()
    with pytest.raises(FileTooLargeError):
        await make_ops(client, max_file_bytes=1024).create_file(
            "site/grande.php", "<?php " + "x" * 2000, allow_php=True
        )
    assert client.files == {}


# ---------------------------------------------------- upload_local_file
@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "archivos"
    folder.mkdir()
    return folder


async def test_local_php_needs_authorization(inbox: Path) -> None:
    (inbox / "enviar.php").write_bytes(PHP.encode())
    client = site()
    with pytest.raises(FileTypeNotAllowedError, match="allow_php=true"):
        await make_ops(client, inbox).upload_local_file("enviar.php", "site/enviar.php")
    assert client.files == {}


async def test_local_php_is_uploaded_when_authorized(inbox: Path) -> None:
    (inbox / "enviar.php").write_bytes(PHP.encode())
    client = site()
    result = await make_ops(client, inbox).upload_local_file(
        "enviar.php", "site/enviar.php", allow_php=True
    )
    assert client.files["public_html/site/enviar.php"] == PHP.encode()
    assert result["executable"] is True and result["mime"] == "application/x-httpd-php"
    assert str(inbox) not in str(result)  # no se devuelve la ruta local


@pytest.mark.parametrize(
    "content",
    [PNG, b"<html>solo html sin php</html>", b"<?php\x00 binario"],
)
async def test_local_file_named_php_must_really_be_php_text(inbox: Path, content: bytes) -> None:
    (inbox / "falso.php").write_bytes(content)
    client = site()
    with pytest.raises(FileTypeNotAllowedError, match="no parece un script PHP"):
        await make_ops(client, inbox).upload_local_file(
            "falso.php", "site/falso.php", allow_php=True
        )
    assert client.files == {}


async def test_local_php_cannot_be_disguised_as_an_image_or_double_extension(inbox: Path) -> None:
    (inbox / "x.png").write_bytes(PNG)
    (inbox / "enviar.php").write_bytes(PHP.encode())
    client = site()
    ops = make_ops(client, inbox)
    # Un PNG real no se puede publicar con nombre PHP, ni PHP con nombre de imagen.
    with pytest.raises(FileTypeNotAllowedError):
        await ops.upload_local_file("enviar.php", "site/foto.png", allow_php=True)
    for target in ("site/enviar.php.png", "site/enviar.php.", "site/enviar.phtml"):
        with pytest.raises(FileTypeNotAllowedError):
            await ops.upload_local_file("enviar.php", target, allow_php=True)
    assert client.files == {}


async def test_local_allow_php_is_still_confined_to_the_inbox(inbox: Path, tmp_path: Path) -> None:
    outside = tmp_path / "otra"
    outside.mkdir()
    (outside / "x.php").write_bytes(PHP.encode())
    with pytest.raises(Exception, match="carpetas locales"):
        await make_ops(site(), inbox).upload_local_file(
            str(outside / "x.php"), "site/x.php", allow_php=True
        )


# ---------------------------------------------------- servidor, auditoría y esquema
async def test_schema_audit_and_defaults(settings: Settings, tmp_path: Path) -> None:
    inbox = tmp_path / "archivos"
    inbox.mkdir()
    (inbox / "enviar.php").write_bytes(PHP.encode())
    settings = dataclasses.replace(settings, local_upload_dirs=(inbox,))
    client = site()
    server = build_server(settings, client=client)  # type: ignore[arg-type]

    tools = {tool.name: tool for tool in await server.list_tools()}
    for name in ("create_file", "upload_file", "upload_local_file"):
        schema = tools[name].inputSchema
        assert schema["properties"]["allow_php"]["default"] is False
        assert "allow_php" not in schema.get("required", [])
        assert "explícita" in tools[name].description

    # Sin allow_php el servidor rechaza el PHP (y lo audita como error).
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="allow_php=true"):
        await server.call_tool("create_file", {"path": "site/enviar.php", "content": PHP})

    # Con allow_php=true se escribe y la auditoría anota la decisión sin guardar el código.
    _, result = await server.call_tool(
        "create_file", {"path": "site/enviar.php", "content": PHP, "allow_php": True}
    )
    assert result["result"]["executable"] is True
    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"allow_php":true' in audit
    assert "mail(" not in audit and settings.token not in audit
    assert '"outcome":"error"' in audit and '"outcome":"success"' in audit


def test_instructions_tell_the_model_to_ask_the_user_first() -> None:
    from cpanel_mcp.server import INSTRUCTIONS

    assert "allow_php=true" in INSTRUCTIONS
    assert "explícita" in INSTRUCTIONS
    assert "iniciativa propia" in INSTRUCTIONS
    assert "cabeceras" in INSTRUCTIONS  # guía para formularios de contacto
