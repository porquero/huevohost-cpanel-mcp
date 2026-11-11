from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path

import pytest

from cpanel_mcp.config import Settings, load_settings
from cpanel_mcp.errors import (
    ConfigError,
    FileAlreadyExistsError,
    FileOperationError,
    FileTooLargeError,
    FileTypeNotAllowedError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
)
from cpanel_mcp.file_ops import FileOperations
from cpanel_mcp.server import build_server

from .conftest import write_actions
from .test_file_ops import FakeFileClient

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40  # ~10 KB
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 300


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    return folder


def make_ops(
    client: FakeFileClient, downloads: Path, *, max_bytes: int = 1_048_576
) -> FileOperations:
    settings = Settings(
        host="cpanel.test",
        user="tester",
        token="TOKEN-SECRETO-123",
        max_file_bytes=max_bytes,
        local_upload_dirs=(downloads,),
    )
    return FileOperations(client, settings)  # type: ignore[arg-type]


def site_client() -> FakeFileClient:
    return FakeFileClient(dirs={"public_html/site"})


async def test_uploads_local_binary_without_base64(downloads: Path) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    client = site_client()

    result = await make_ops(client, downloads).upload_local_file(
        str(source), "site/logo.png"
    )

    assert client.files["public_html/site/logo.png"] == PNG
    assert result["bytes"] == len(PNG)
    assert result["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result["mime"] == "image/png"
    assert result["verified"] is True
    assert result["source_filename"] == "logo.png"
    # La ruta local completa no se devuelve al modelo.
    assert str(downloads) not in str(result)


async def test_destination_name_is_independent_of_source_name(downloads: Path) -> None:
    source = downloads / "IMG_0001.JPG"
    source.write_bytes(JPEG)
    client = site_client()
    await make_ops(client, downloads).upload_local_file(str(source), "site/foto.jpg")
    assert "public_html/site/foto.jpg" in client.files


async def test_subfolders_of_allowed_dir_are_allowed(downloads: Path) -> None:
    (downloads / "cliente").mkdir()
    source = downloads / "cliente" / "logo.png"
    source.write_bytes(PNG)
    client = site_client()
    await make_ops(client, downloads).upload_local_file(str(source), "site/l.png")
    assert "public_html/site/l.png" in client.files


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "logo.png", "Downloads/logo.png", "C:\\x\\..\\y.png", "/a/\x00b.png"],
)
async def test_invalid_local_paths(downloads: Path, bad: str) -> None:
    client = site_client()
    with pytest.raises((ValidationError, UnsafePathError)):
        await make_ops(client, downloads).upload_local_file(bad, "site/logo.png")
    assert not [c for c in client.calls if c[0] == "upload_file"]


async def test_dotdot_cannot_escape_allowed_dir(downloads: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secreto.png"
    secret.write_bytes(PNG)
    sneaky = str(downloads) + os.sep + ".." + os.sep + "secreto.png"
    with pytest.raises(UnsafePathError):
        await make_ops(site_client(), downloads).upload_local_file(
            sneaky, "site/x.png"
        )


async def test_file_outside_allowed_dirs_is_rejected(
    downloads: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "Documentos"
    outside.mkdir()
    (outside / "privado.pdf").write_bytes(b"%PDF-1.7 privado")
    client = site_client()
    with pytest.raises(UnsafePathError, match="MCP_LOCAL_UPLOAD_DIRS"):
        await make_ops(client, downloads).upload_local_file(
            str(outside / "privado.pdf"), "site/privado.pdf"
        )
    assert client.files == {}


async def test_sibling_dir_with_same_prefix_is_rejected(
    downloads: Path, tmp_path: Path
) -> None:
    sibling = tmp_path / "Downloads-privado"
    sibling.mkdir()
    (sibling / "a.png").write_bytes(PNG)
    with pytest.raises(UnsafePathError):
        await make_ops(site_client(), downloads).upload_local_file(
            str(sibling / "a.png"), "site/a.png"
        )


async def test_symlinks_are_not_followed(downloads: Path, tmp_path: Path) -> None:
    private = tmp_path / "privado"
    private.mkdir()
    (private / "foto.png").write_bytes(PNG)
    link_file = downloads / "enlace.png"
    link_dir = downloads / "carpeta"
    try:
        link_file.symlink_to(private / "foto.png")
        link_dir.symlink_to(private, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no se pueden crear enlaces simbólicos en este sistema")
    ops = make_ops(site_client(), downloads)
    with pytest.raises(SymlinkError):
        await ops.upload_local_file(str(link_file), "site/a.png")
    with pytest.raises(SymlinkError):
        await ops.upload_local_file(str(link_dir / "foto.png"), "site/b.png")


async def test_missing_file_and_directory(downloads: Path) -> None:
    ops = make_ops(site_client(), downloads)
    with pytest.raises(FileOperationError, match="no existe"):
        await ops.upload_local_file(str(downloads / "nada.png"), "site/a.png")
    (downloads / "carpeta").mkdir()
    with pytest.raises(FileOperationError, match="archivo regular"):
        await ops.upload_local_file(str(downloads / "carpeta"), "site/a.png")


@pytest.mark.parametrize(
    "dest",
    [
        "site/shell.php",
        "site/foto.php.jpg",
        "site/foto.jpg.php",
        "site/.htaccess",
        "site/logo.svg",
        "site/app.exe",
        "site/notas.txt",
        "site/sin_extension",
        "site/.git/foto.png",
    ],
)
async def test_dangerous_destinations_are_rejected(downloads: Path, dest: str) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    client = site_client()
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client, downloads).upload_local_file(str(source), dest)
    assert client.files == {}


async def test_destination_traversal_is_rejected(downloads: Path) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    with pytest.raises(UnsafePathError):
        await make_ops(site_client(), downloads).upload_local_file(
            str(source), "../fuera.png"
        )


async def test_content_must_match_extension(downloads: Path) -> None:
    fake = downloads / "falso.jpg"
    fake.write_bytes(b"<?php system($_GET['c']); ?>")
    png = downloads / "real.png"
    png.write_bytes(PNG)
    client = site_client()
    ops = make_ops(client, downloads)
    with pytest.raises(FileTypeNotAllowedError, match="no coincide"):
        await ops.upload_local_file(str(fake), "site/foto.jpg")
    with pytest.raises(FileTypeNotAllowedError, match="image/png"):
        await ops.upload_local_file(str(png), "site/foto.jpg")
    assert client.files == {}


async def test_size_limit_is_respected(downloads: Path) -> None:
    source = downloads / "grande.png"
    source.write_bytes(PNG + b"\x00" * 5000)
    client = site_client()
    with pytest.raises(FileTooLargeError):
        await make_ops(client, downloads, max_bytes=2048).upload_local_file(
            str(source), "site/grande.png"
        )
    assert client.files == {}


async def test_empty_file_is_rejected(downloads: Path) -> None:
    source = downloads / "vacio.png"
    source.write_bytes(b"")
    with pytest.raises(ValidationError, match="vacío"):
        await make_ops(site_client(), downloads).upload_local_file(
            str(source), "site/vacio.png"
        )


async def test_existing_destination_without_overwrite(downloads: Path) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    client = FakeFileClient(
        dirs={"public_html/site"}, files={"public_html/site/logo.png": b"viejo"}
    )
    with pytest.raises(FileAlreadyExistsError):
        await make_ops(client, downloads).upload_local_file(str(source), "site/logo.png")
    assert client.files["public_html/site/logo.png"] == b"viejo"
    assert client.trashed == []


async def test_overwrite_trashes_previous_file_first(downloads: Path) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    client = FakeFileClient(
        dirs={"public_html/site"}, files={"public_html/site/logo.png": b"viejo"}
    )
    result = await make_ops(client, downloads).upload_local_file(
        str(source), "site/logo.png", overwrite=True
    )
    names = [call[0] for call in client.calls]
    assert names.index("trash_file") < names.index("upload_file")
    assert result["backed_up_to_trash"] is True
    assert client.files["public_html/site/logo.png"] == PNG


async def test_overwrite_must_be_boolean(downloads: Path) -> None:
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    with pytest.raises(ValidationError):
        await make_ops(site_client(), downloads).upload_local_file(
            str(source), "site/logo.png", overwrite="si"  # type: ignore[arg-type]
        )


# ------------------------------------------------- registro, auditoría, config
async def test_tool_registered_with_schema_and_audited_without_content(
    settings: Settings, downloads: Path
) -> None:
    settings = dataclasses.replace(settings, local_upload_dirs=(downloads,))
    source = downloads / "logo.png"
    source.write_bytes(PNG)
    client = site_client()
    server = build_server(settings, client=client)  # type: ignore[arg-type]

    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = tools["upload_local_file"].inputSchema
    assert set(schema["required"]) == {"local_path", "path"}
    assert schema["properties"]["overwrite"]["default"] is False
    assert "upload_local_file" in tools["upload_file"].description
    assert "5 KB" in tools["upload_file"].description

    _, structured = await server.call_tool(
        "upload_local_file", {"local_path": str(source), "path": "site/logo.png"}
    )
    assert structured["result"]["sha256"] == hashlib.sha256(PNG).hexdigest()

    audit = settings.audit_path.read_text(encoding="utf-8")
    assert '"action":"upload_local_file"' in audit
    assert '"outcome":"success"' in audit
    assert settings.token not in audit
    assert PNG[:16].hex() not in audit


async def test_tool_is_not_registered_when_disabled(
    settings: Settings, tmp_path: Path
) -> None:
    actions = write_actions(
        tmp_path / "actions.json",
        [
            {"name": "upload_file", "level": "escritura_reversible", "enabled": True},
            {
                "name": "upload_local_file",
                "level": "escritura_reversible",
                "enabled": False,
            },
        ],
    )
    server = build_server(settings, client=site_client(), actions_path=actions)  # type: ignore[arg-type]
    assert {tool.name for tool in await server.list_tools()} == {"upload_file"}


def test_local_upload_dirs_config(tmp_path: Path) -> None:
    base = {"CPANEL_HOST": "cpanel.test", "CPANEL_USER": "u", "CPANEL_TOKEN": "t"}
    assert load_settings(base).local_upload_dirs == (Path.home() / "Downloads",)

    first, second = tmp_path / "a", tmp_path / "b"
    configured = load_settings(
        {**base, "MCP_LOCAL_UPLOAD_DIRS": f"{first}{os.pathsep}{second}"}
    )
    assert configured.local_upload_dirs == (first, second)

    with pytest.raises(ConfigError, match="absolutas"):
        load_settings({**base, "MCP_LOCAL_UPLOAD_DIRS": "relativa/carpeta"})
