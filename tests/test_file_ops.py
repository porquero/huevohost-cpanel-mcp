from __future__ import annotations

import base64
import posixpath
from typing import Any

import pytest

from cpanel_mcp.config import Settings
from cpanel_mcp.errors import (
    FileAlreadyExistsError,
    FileTooLargeError,
    FileTypeNotAllowedError,
    RemoteFileNotFoundError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
)
from cpanel_mcp.file_ops import FileOperations, normalize_public_path


def _mimetype(name: str) -> str:
    if name.endswith((".html", ".htm")):
        return "text/html"
    if name.endswith(".txt"):
        return "text/plain"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".png"):
        return "image/png"
    return "application/octet-stream"


class FakeFileClient:
    def __init__(
        self,
        *,
        files: dict[str, str | bytes] | None = None,
        dirs: set[str] | None = None,
        symlinks: set[str] | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.dirs = {"public_html", *(dirs or set())}
        self.symlinks = set(symlinks or set())
        self.trashed: list[str] = []
        self.calls: list[tuple[str, Any]] = []

    async def get_file_information(self, path: str) -> dict[str, Any]:
        self.calls.append(("get_file_information", path))
        if path in self.symlinks:
            return {
                "exists": 1,
                "type": "symlink",
                "size": "0",
                "mimetype": "inode/symlink",
            }
        if path in self.dirs:
            return {
                "exists": 1,
                "type": "dir",
                "size": "4096",
                "mimetype": "httpd/unix-directory",
            }
        if path in self.files:
            content = self.files[path]
            size = (
                len(content)
                if isinstance(content, bytes)
                else len(content.encode("utf-8"))
            )
            return {
                "exists": 1,
                "type": "file",
                "size": "" if size == 0 else str(size),
                "mimetype": _mimetype(path),
            }
        raise RemoteFileNotFoundError("no existe")

    async def list_files(
        self,
        directory: str,
        *,
        show_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(("list_files", directory))
        entries: list[dict[str, Any]] = []
        for path in sorted(self.dirs | self.symlinks | set(self.files)):
            if path == directory:
                continue
            if posixpath.dirname(path) != directory:
                continue
            name = posixpath.basename(path)
            if name.startswith(".") and not show_hidden:
                continue
            if path in self.symlinks:
                entry_type = "symlink"
                size = "0"
                mime = "inode/symlink"
            elif path in self.dirs:
                entry_type = "dir"
                size = "4096"
                mime = "httpd/unix-directory"
            else:
                entry_type = "file"
                content = self.files[path]
                size = (
                    len(content)
                    if isinstance(content, bytes)
                    else len(content.encode("utf-8"))
                )
                size = str(size)
                mime = _mimetype(path)
            entries.append(
                {
                    "file": name,
                    "type": entry_type,
                    "size": size,
                    "mtime": 1700000000,
                    "mimetype": mime,
                    "read": 1,
                    "write": 1,
                }
            )
        return entries

    async def get_file_content(self, directory: str, filename: str) -> dict[str, Any]:
        path = f"{directory}/{filename}"
        self.calls.append(("get_file_content", path))
        content = self.files[path]
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return {
            "content": content,
            "filename": filename,
            "to_charset": "UTF-8",
        }

    async def upload_file(
        self,
        directory: str,
        filename: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> None:
        path = f"{directory}/{filename}"
        self.calls.append(("upload_file", (path, content, overwrite)))
        self.files[path] = content

    async def trash_file(self, path: str) -> dict[str, Any]:
        self.calls.append(("trash_file", path))
        self.trashed.append(path)
        if path in self.files:
            del self.files[path]
        elif path in self.dirs:
            self.dirs.discard(path)
            for item in list(self.files):
                if item.startswith(path + "/"):
                    del self.files[item]
            for item in list(self.dirs):
                if item.startswith(path + "/"):
                    self.dirs.discard(item)
        return {"path": path, "trash_leaf": posixpath.basename(path)}


def make_ops(client: FakeFileClient, *, max_bytes: int = 1_048_576) -> FileOperations:
    settings = Settings(
        host="cpanel.test",
        user="tester",
        token="TOKEN-SECRETO-123",
        max_file_bytes=max_bytes,
    )
    return FileOperations(client, settings)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("public_html", "public_html"),
        ("/public_html", "public_html"),
        ("public_html/sitio.example.com/index.html", "public_html/sitio.example.com/index.html"),
        ("/public_html/sitio.example.com/index.html", "public_html/sitio.example.com/index.html"),
        ("sitio.example.com/index.html", "public_html/sitio.example.com/index.html"),
        ("public_html/sitio.example.com/", "public_html/sitio.example.com"),
    ],
)
def test_normalize_accepts_paths_under_public_html(raw: str, expected: str) -> None:
    assert normalize_public_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/",
        "/etc/passwd",
        "/home/usuario/public_html/index.html",
        "public_html/../secret.txt",
        "public_html/sitio.example.com/../../secret.txt",
        "public_html//index.html",
        "public_html/./index.html",
        "C:\\Windows\\win.ini",
        "public_html/\x00index.html",
        "public_html/index\n.html",
        "public_html/index,html",
        "~/public_html/index.html",
    ],
)
def test_normalize_rejects_unsafe_paths(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        normalize_public_path(raw)


def test_root_can_be_rejected_for_writes() -> None:
    with pytest.raises(UnsafePathError):
        normalize_public_path("public_html", allow_root=False)


def test_path_depth_is_limited() -> None:
    deep = "public_html/" + "/".join(f"d{index}" for index in range(21))
    with pytest.raises(UnsafePathError):
        normalize_public_path(deep)


async def test_list_files_returns_direct_children() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "<h1>Hola</h1>"},
        dirs={"public_html/site"},
    )
    result = await make_ops(client).list_files("site")
    assert result["path"] == "public_html/site"
    assert result["path_display"] == "/public_html/site"
    assert result["count"] == 1
    entry = result["entries"][0]
    assert entry["name"] == "index.html"
    assert entry["type"] == "file"
    assert entry["symlink"] is False


async def test_list_files_rejects_symlink_directory() -> None:
    client = FakeFileClient(symlinks={"public_html/enlace"})
    with pytest.raises(SymlinkError):
        await make_ops(client).list_files("enlace")


async def test_download_text_file() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "<h1>Hola</h1>"},
        dirs={"public_html/site"},
    )
    result = await make_ops(client).download_file("site/index.html")
    assert result["content"] == "<h1>Hola</h1>"
    assert result["size"] == 13
    assert result["verified"] is True


async def test_download_rejects_binary_file() -> None:
    client = FakeFileClient(
        files={"public_html/site/logo.png": "PNG"},
        dirs={"public_html/site"},
    )
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client).download_file("site/logo.png")


async def test_download_rejects_sensitive_name() -> None:
    client = FakeFileClient(
        files={"public_html/site/.env": "SECRET=1"},
        dirs={"public_html/site"},
    )
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client).download_file("site/.env")


@pytest.mark.parametrize(
    "path",
    [
        "site/.htaccess",
        "site/.git/config",
        "site/.ssh/id_rsa",
        "site/claves/server.key",
    ],
)
async def test_download_rejects_sensitive_files_and_directories(path: str) -> None:
    client = FakeFileClient(
        files={f"public_html/{path}": "texto"},
        dirs={"public_html/site", "public_html/site/.git", "public_html/site/.ssh"},
    )
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client).download_file(path)


async def test_download_rejects_file_over_limit() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "x" * 100},
        dirs={"public_html/site"},
    )
    with pytest.raises(FileTooLargeError):
        await make_ops(client, max_bytes=50).download_file("site/index.html")


async def test_create_file_new() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    result = await make_ops(client).create_file("site/index.html", "<h1>Nuevo</h1>")
    assert result["created"] is True
    assert result["overwritten"] is False
    assert result["verified"] is True
    assert client.files["public_html/site/index.html"] == "<h1>Nuevo</h1>".encode()


async def test_create_empty_file_is_verified() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    result = await make_ops(client).create_file("site/vacio.txt", "")
    assert result["created"] is True
    assert result["bytes"] == 0
    assert result["verified"] is True
    assert client.files["public_html/site/vacio.txt"] == b""


async def test_create_file_existing_requires_overwrite() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "viejo"},
        dirs={"public_html/site"},
    )
    with pytest.raises(FileAlreadyExistsError):
        await make_ops(client).create_file("site/index.html", "nuevo")
    assert client.files["public_html/site/index.html"] == "viejo"
    assert client.trashed == []


async def test_create_file_overwrite_trashes_before_writing() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "viejo"},
        dirs={"public_html/site"},
    )
    result = await make_ops(client).create_file(
        "site/index.html",
        "nuevo",
        overwrite=True,
    )
    assert client.trashed == ["public_html/site/index.html"]
    assert client.files["public_html/site/index.html"] == b"nuevo"
    assert result["overwritten"] is True
    assert result["backed_up_to_trash"] is True
    names = [name for name, _ in client.calls]
    assert names.index("trash_file") < names.index("upload_file")


async def test_create_file_rejects_executable_extension() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client).create_file("site/shell.php", "<?php echo 1;")
    with pytest.raises(FileTypeNotAllowedError):
        await make_ops(client).create_file("site/.htaccess", "RewriteEngine On")


async def test_write_rejects_sensitive_directories() -> None:
    client = FakeFileClient(dirs={"public_html/site", "public_html/site/.git"})
    ops = make_ops(client)
    with pytest.raises(FileTypeNotAllowedError):
        await ops.create_file("site/.git/config", "texto")
    with pytest.raises(FileTypeNotAllowedError):
        await ops.upload_file("site/.ssh/authorized_keys", content="ssh-rsa AAAA")
    assert client.files == {}


async def test_upload_text_file() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    result = await make_ops(client).upload_file(
        "site/robots.txt",
        content="User-agent: *\n",
    )
    assert result["uploaded"] is True
    assert result["bytes"] == 14
    assert result["verified"] is True


async def test_upload_base64_binary_file() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    payload = b"\x89PNG\r\n"
    result = await make_ops(client).upload_file(
        "site/logo.png",
        content_base64=base64.b64encode(payload).decode("ascii"),
    )
    assert result["bytes"] == len(payload)
    assert client.files["public_html/site/logo.png"] == payload


async def test_upload_requires_exactly_one_content_field() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    ops = make_ops(client)
    with pytest.raises(ValidationError):
        await ops.upload_file("site/a.txt")
    with pytest.raises(ValidationError):
        await ops.upload_file(
            "site/a.txt",
            content="texto",
            content_base64=base64.b64encode(b"x").decode("ascii"),
        )


async def test_upload_rejects_invalid_base64() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(ValidationError):
        await make_ops(client).upload_file(
            "site/a.txt",
            content_base64="no-es-base64!",
        )


async def test_write_rejects_symlink_component() -> None:
    client = FakeFileClient(
        dirs={"public_html/site"},
        symlinks={"public_html/site/enlace"},
    )
    with pytest.raises(SymlinkError):
        await make_ops(client).create_file("site/enlace/index.html", "x")


async def test_delete_requires_exact_confirmation() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "x"},
        dirs={"public_html/site"},
    )
    with pytest.raises(ValidationError):
        await make_ops(client).delete_file("site/index.html", confirm="otra")
    assert client.trashed == []


async def test_delete_moves_file_to_trash() -> None:
    client = FakeFileClient(
        files={"public_html/site/index.html": "x"},
        dirs={"public_html/site"},
    )
    result = await make_ops(client).delete_file(
        "site/index.html",
        confirm="/public_html/site/index.html",
    )
    assert result["trashed"] is True
    assert result["verified"] is True
    assert client.trashed == ["public_html/site/index.html"]
    assert "public_html/site/index.html" not in client.files


async def test_delete_rejects_public_html_root() -> None:
    client = FakeFileClient()
    with pytest.raises(UnsafePathError):
        await make_ops(client).delete_file("public_html", confirm="public_html")


async def test_delete_rejects_symlink() -> None:
    client = FakeFileClient(
        dirs={"public_html/site"},
        symlinks={"public_html/site/enlace"},
    )
    with pytest.raises(SymlinkError):
        await make_ops(client).delete_file(
            "site/enlace",
            confirm="site/enlace",
        )
    assert client.trashed == []


async def test_upload_returns_sha256_and_accepts_expected_hash() -> None:
    import hashlib

    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    digest = hashlib.sha256(payload).hexdigest()
    client = FakeFileClient(dirs={"public_html/site"})
    result = await make_ops(client).upload_file(
        "site/a.png",
        content_base64=base64.b64encode(payload).decode("ascii"),
        expected_sha256=digest.upper(),
    )
    assert result["sha256"] == digest


async def test_upload_wrong_expected_sha256_writes_nothing() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(ValidationError, match="sha256"):
        await make_ops(client).upload_file(
            "site/a.png",
            content="hola",
            expected_sha256="0" * 64,
        )
    assert not [c for c in client.calls if c[0] == "upload_file"]


async def test_upload_rejects_malformed_expected_sha256() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(ValidationError, match="64"):
        await make_ops(client).upload_file(
            "site/a.txt", content="hola", expected_sha256="abc"
        )


async def test_base64_error_reports_position_of_first_invalid_char() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(ValidationError, match="posición 4"):
        await make_ops(client).upload_file(
            "site/a.png", content_base64="QUJD*EZH"
        )


async def test_base64_error_reports_padding_state() -> None:
    client = FakeFileClient(dirs={"public_html/site"})
    with pytest.raises(ValidationError, match="padding incorrecto"):
        await make_ops(client).upload_file(
            "site/a.png", content_base64="QUJDRA="
        )
    with pytest.raises(ValidationError, match="truncada"):
        await make_ops(client).upload_file(
            "site/a.png", content_base64="QUJDR"
        )
