from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from cpanel_mcp.errors import CPanelAPIError
from cpanel_mcp.uploads.service import UploadApp
from cpanel_mcp.uploads.settings import AccountConfig, UploadSettings

ADMIN = "A" * 40
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 80  # ~20 KB
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


class World:
    """Aplicación, relojes y directorios de prueba."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.clock = Clock()
        self.homes = {
            name: tmp_path / name for name in ("acopios", "otra")
        }
        for home in self.homes.values():
            (home / "public_html" / "criffoh").mkdir(parents=True)
        self.trashed: list[tuple[str, str]] = []
        self.audit_path = tmp_path / "audit.jsonl"
        self.settings = UploadSettings(
            public_base_url="https://upload.acopios.test",
            admin_secret=ADMIN,
            accounts={
                name: AccountConfig(user=name, home=home, token=f"TOKEN-{name}-XYZ")
                for name, home in self.homes.items()
            },
            db_path=tmp_path / "state.db",
            audit_path=self.audit_path,
            **overrides,
        )
        self.app = UploadApp(
            self.settings, clock=self.clock, trasher=self._trash
        )

    def _trash(self, account: str, path: str) -> None:
        self.trashed.append((account, path))
        target = self.homes[account] / path
        os.replace(target, target.with_name(target.name + ".trashed"))

    def request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        headers: dict[str, str] | None = None,
        ip: str = "203.0.113.7",
        https: bool = True,
        length: bool = True,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        environ: dict[str, Any] = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "REMOTE_ADDR": ip,
            "wsgi.url_scheme": "https" if https else "http",
            "wsgi.input": io.BytesIO(body),
        }
        if length:
            environ["CONTENT_LENGTH"] = str(len(body))
        for key, value in (headers or {}).items():
            name = key.upper().replace("-", "_")
            if name in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
                environ[name] = value
            else:
                environ["HTTP_" + name] = value
        captured: dict[str, Any] = {}

        def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(response_headers)

        chunks = self.app(environ, start_response)
        return (
            captured["status"],
            json.loads(b"".join(chunks).decode("utf-8")),
            captured["headers"],
        )

    def ticket(
        self,
        path: str = "criffoh/foto.jpg",
        *,
        account: str = "acopios",
        ip: str = "198.51.100.1",
        **fields: Any,
    ) -> tuple[int, dict[str, Any]]:
        payload = {"account": account, "path": path, **fields}
        status, body, _ = self.request(
            "POST",
            "/admin/tickets",
            json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {ADMIN}"},
            ip=ip,
        )
        return status, body

    def upload_path(self, url: str) -> str:
        return url.removeprefix("https://upload.acopios.test")

    def file(self, rel: str, account: str = "acopios") -> Path:
        return self.homes[account] / "public_html" / rel


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def put(world: World, url_path: str, body: bytes, **kwargs: Any):
    return world.request("PUT", url_path, body, **kwargs)


# ---------------------------------------------------------------- tickets
def test_create_ticket_returns_https_url_and_expiry(world: World) -> None:
    status, body = world.ticket()
    assert status == 201
    assert body["upload_url"].startswith("https://upload.acopios.test/upload/")
    assert body["method"] == "PUT"
    assert body["expires_at"].endswith("Z")
    assert body["path"] == "/public_html/criffoh/foto.jpg"


def test_ticket_requires_admin_secret(world: World) -> None:
    status, body, _ = world.request(
        "POST",
        "/admin/tickets",
        b"{}",
        headers={"Authorization": "Bearer incorrecto"},
    )
    assert status == 401
    status, _, _ = world.request("POST", "/admin/tickets", b"{}")
    assert status == 401


def test_ticket_rejects_unmanaged_account(world: World) -> None:
    status, body = world.ticket(account="intruso")
    assert status == 403
    assert body["error"] == "account_not_allowed"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("../fuera.jpg", 400),
        ("criffoh/../../x.jpg", 400),
        ("/etc/passwd.jpg", 400),
        ("criffoh/a\x00.jpg", 400),
        ("criffoh/foto.php.jpg", 415),
        ("criffoh/foto.jpg.php", 415),
        ("criffoh/.htaccess", 415),
        ("criffoh/php.ini", 415),
        ("criffoh/logo.svg", 415),
        ("criffoh/datos.zip", 415),
        ("criffoh/shell.sh", 415),
        ("criffoh/sin_extension", 415),
        ("criffoh/.git/foto.jpg", 415),
    ],
)
def test_ticket_rejects_dangerous_targets(world: World, path: str, status: int) -> None:
    got, body = world.ticket(path)
    assert got == status, body


def test_zip_can_be_enabled(tmp_path: Path) -> None:
    world = World(tmp_path, allow_zip=True)
    assert world.ticket("criffoh/datos.zip")[0] == 201


def test_ticket_max_bytes_is_capped(world: World) -> None:
    cap = world.settings.max_bytes_cap
    assert world.ticket(max_bytes=cap)[0] == 201
    status, body = world.ticket(max_bytes=cap + 1)
    assert status == 400 and body["error"] == "invalid_max_bytes"


def test_only_token_hash_is_stored(world: World) -> None:
    _, body = world.ticket()
    token = body["upload_url"].rsplit("/", 1)[1]
    assert len(token) == 43  # 256 bits
    raw = world.settings.db_path.read_bytes()
    assert token.encode() not in raw
    assert hashlib.sha256(token.encode()).hexdigest().encode() in raw


# ---------------------------------------------------------------- subida
def test_upload_happy_path(world: World) -> None:
    _, ticket = world.ticket()
    status, body, headers = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 201, body
    assert body == {
        "path": "/public_html/criffoh/foto.jpg",
        "bytes": len(JPEG),
        "sha256": hashlib.sha256(JPEG).hexdigest(),
        "mime": "image/jpeg",
    }
    assert world.file("criffoh/foto.jpg").read_bytes() == JPEG
    assert "Access-Control-Allow-Origin" not in headers
    assert not list((world.homes["acopios"] / ".mcp-upload-tmp").iterdir())


def test_ticket_is_single_use(world: World) -> None:
    _, ticket = world.ticket()
    url = world.upload_path(ticket["upload_url"])
    assert put(world, url, JPEG)[0] == 201
    status, body, _ = put(world, url, JPEG)
    assert status == 403 and body["error"] == "ticket_used"


def test_expired_ticket_writes_nothing(world: World) -> None:
    _, ticket = world.ticket()
    world.clock.now += 301
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 403 and body["error"] == "ticket_expired"
    assert not world.file("criffoh/foto.jpg").exists()


def test_unknown_and_malformed_tokens_are_404(world: World) -> None:
    assert put(world, "/upload/" + "a" * 43, JPEG)[0] == 404
    assert put(world, "/upload/corto", JPEG)[0] == 404
    assert not world.file("criffoh/foto.jpg").exists()


def test_ticket_is_bound_to_its_account_and_path(world: World) -> None:
    _, ticket = world.ticket("criffoh/foto.jpg", account="otra")
    status, _, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 201
    # Solo se escribió en la cuenta y la ruta del ticket.
    assert world.file("criffoh/foto.jpg", "otra").exists()
    assert not world.file("criffoh/foto.jpg", "acopios").exists()
    written = [p.name for p in world.file("criffoh", "otra").iterdir()]
    assert written == ["foto.jpg"]


def test_missing_directory_is_404_and_burns_nothing_on_disk(world: World) -> None:
    _, ticket = world.ticket("noexiste/foto.jpg")
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 404 and body["error"] == "directory_not_found"


def test_magic_bytes_must_match_extension(world: World) -> None:
    _, ticket = world.ticket("criffoh/foto.jpg")
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), PNG)
    assert status == 415 and body["error"] == "content_mismatch"
    assert not world.file("criffoh/foto.jpg").exists()
    assert not list((world.homes["acopios"] / ".mcp-upload-tmp").iterdir())


def test_php_disguised_as_jpg_is_rejected(world: World) -> None:
    _, ticket = world.ticket("criffoh/foto.jpg")
    status, _, _ = put(world, world.upload_path(ticket["upload_url"]), b"<?php system($_GET[1]);")
    assert status == 415
    assert not world.file("criffoh/foto.jpg").exists()


def test_size_limit_is_enforced_while_streaming(world: World) -> None:
    _, ticket = world.ticket(max_bytes=1000)
    # Sin Content-Length: el servidor solo puede confiar en lo que lee.
    status, body, _ = put(
        world, world.upload_path(ticket["upload_url"]), JPEG, length=False
    )
    assert status == 413 and body["error"] == "payload_too_large"
    assert not world.file("criffoh/foto.jpg").exists()


def test_size_limit_with_declared_content_length(world: World) -> None:
    _, ticket = world.ticket(max_bytes=1000)
    status, _, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 413


def test_empty_body_is_rejected(world: World) -> None:
    _, ticket = world.ticket()
    assert put(world, world.upload_path(ticket["upload_url"]), b"")[0] == 400


# --------------------------------------------------------------- overwrite
def test_existing_file_without_overwrite_is_409(world: World) -> None:
    world.file("criffoh/foto.jpg").write_bytes(b"original")
    _, ticket = world.ticket()
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 409 and body["error"] == "file_exists"
    assert world.file("criffoh/foto.jpg").read_bytes() == b"original"
    assert world.trashed == []


def test_overwrite_moves_previous_file_to_trash(world: World) -> None:
    world.file("criffoh/foto.jpg").write_bytes(b"original")
    _, ticket = world.ticket(overwrite=True)
    status, _, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 201
    assert world.trashed == [("acopios", "public_html/criffoh/foto.jpg")]
    assert world.file("criffoh/foto.jpg").read_bytes() == JPEG


def test_failed_validation_does_not_trash_existing_file(world: World) -> None:
    world.file("criffoh/foto.jpg").write_bytes(b"original")
    _, ticket = world.ticket(overwrite=True)
    status, _, _ = put(world, world.upload_path(ticket["upload_url"]), PNG)
    assert status == 415
    assert world.trashed == []
    assert world.file("criffoh/foto.jpg").read_bytes() == b"original"


def test_trash_failure_writes_nothing(tmp_path: Path) -> None:
    world = World(tmp_path)

    def broken(account: str, path: str) -> None:
        raise CPanelAPIError("cPanel dijo que no")

    world.app.trasher = broken
    world.file("criffoh/foto.jpg").write_bytes(b"original")
    _, ticket = world.ticket(overwrite=True)
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 502 and body["error"] == "trash_failed"
    assert world.file("criffoh/foto.jpg").read_bytes() == b"original"


# ------------------------------------------------------------------ sha256
def test_expected_sha256_match(world: World) -> None:
    digest = hashlib.sha256(JPEG).hexdigest()
    _, ticket = world.ticket(expected_sha256=digest)
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 201 and body["sha256"] == digest


def test_expected_sha256_mismatch_removes_file(world: World) -> None:
    _, ticket = world.ticket(expected_sha256="0" * 64)
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 422 and body["error"] == "sha256_mismatch"
    assert not world.file("criffoh/foto.jpg").exists()
    assert not list((world.homes["acopios"] / ".mcp-upload-tmp").iterdir())


def test_invalid_expected_sha256_is_rejected_at_creation(world: World) -> None:
    assert world.ticket(expected_sha256="xyz")[0] == 400


# -------------------------------------------------------------------- POST
def test_post_with_raw_body(world: World) -> None:
    _, ticket = world.ticket()
    status, _, _ = world.request("POST", world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 201


def _multipart(parts: list[bytes], boundary: str = "XBOUNDX") -> tuple[bytes, str]:
    body = b""
    for index, content in enumerate(parts):
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file{index}"; filename="x{index}.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode() + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_post_multipart_single_file(world: World) -> None:
    _, ticket = world.ticket()
    body, ctype = _multipart([JPEG])
    status, result, _ = world.request(
        "POST",
        world.upload_path(ticket["upload_url"]),
        body,
        headers={"Content-Type": ctype},
    )
    assert status == 201, result
    assert result["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert world.file("criffoh/foto.jpg").read_bytes() == JPEG


def test_post_multipart_rejects_several_files(world: World) -> None:
    _, ticket = world.ticket()
    body, ctype = _multipart([JPEG, JPEG])
    status, result, _ = world.request(
        "POST",
        world.upload_path(ticket["upload_url"]),
        body,
        headers={"Content-Type": ctype},
    )
    assert status == 400 and result["error"] == "bad_multipart"
    assert not world.file("criffoh/foto.jpg").exists()


# ------------------------------------------------------ transporte y límites
def test_http_is_refused(world: World) -> None:
    status, body, _ = world.request("PUT", "/upload/" + "a" * 43, b"x", https=False)
    assert status == 403 and body["error"] == "https_required"


def test_methods_and_routes(world: World) -> None:
    status, _, headers = world.request("GET", "/upload/" + "a" * 43)
    assert status == 405 and "PUT" in headers["Allow"]
    assert world.request("OPTIONS", "/upload/" + "a" * 43)[0] == 405
    assert world.request("GET", "/otra-cosa")[0] == 404


def test_upload_rate_limit_per_ip(tmp_path: Path) -> None:
    world = World(tmp_path, limit_uploads_per_ip=(3, 60), limit_bad_attempts_per_ip=(100, 60))
    for _ in range(3):
        assert put(world, "/upload/" + "a" * 43, b"x")[0] == 404
    status, body, headers = put(world, "/upload/" + "a" * 43, b"x")
    assert status == 429 and "Retry-After" in headers
    # Otra IP no se ve afectada.
    assert put(world, "/upload/" + "a" * 43, b"x", ip="192.0.2.9")[0] == 404


def test_bad_token_guessing_is_throttled(tmp_path: Path) -> None:
    world = World(tmp_path, limit_bad_attempts_per_ip=(3, 600))
    codes = [put(world, "/upload/" + "b" * 43, b"x")[0] for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]


def test_ticket_creation_rate_limited_per_account(tmp_path: Path) -> None:
    world = World(tmp_path, limit_tickets_per_account=(2, 60))
    assert world.ticket(ip="10.0.0.1")[0] == 201
    assert world.ticket(ip="10.0.0.2")[0] == 201
    assert world.ticket(ip="10.0.0.3")[0] == 429
    assert world.ticket(account="otra", ip="10.0.0.4")[0] == 201


def test_wrong_admin_secret_is_throttled(tmp_path: Path) -> None:
    world = World(tmp_path, limit_bad_attempts_per_ip=(2, 600))
    bad = {"Authorization": "Bearer nope"}
    codes = [
        world.request("POST", "/admin/tickets", b"{}", headers=bad)[0] for _ in range(3)
    ]
    assert codes == [401, 401, 429]


# ----------------------------------------------------------------- symlinks
def test_symlinked_directory_is_rejected(world: World, tmp_path: Path) -> None:
    outside = tmp_path / "fuera"
    outside.mkdir()
    link = world.file("enlace")
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no se pueden crear enlaces simbólicos en este sistema")
    _, ticket = world.ticket("enlace/foto.jpg")
    status, body, _ = put(world, world.upload_path(ticket["upload_url"]), JPEG)
    assert status == 403 and body["error"] == "symlink_rejected"
    assert not (outside / "foto.jpg").exists()


# ---------------------------------------------------------------- auditoría
def test_audit_has_metadata_but_no_tokens_or_content(world: World) -> None:
    _, ticket = world.ticket()
    token = ticket["upload_url"].rsplit("/", 1)[1]
    put(world, world.upload_path(ticket["upload_url"]), JPEG)
    put(world, world.upload_path(ticket["upload_url"]), JPEG)  # reutilizado

    text = world.audit_path.read_text(encoding="utf-8")
    assert token not in text
    assert ADMIN not in text
    assert "TOKEN-acopios-XYZ" not in text
    entries = [json.loads(line) for line in text.splitlines()]
    uploads = [e for e in entries if e["action"] == "upload"]
    ok = next(e for e in uploads if e["outcome"] == "success")
    assert ok["args"]["account"] == "acopios"
    assert ok["args"]["path"] == "public_html/criffoh/foto.jpg"
    assert ok["args"]["bytes"] == len(JPEG)
    assert ok["args"]["sha256"] == hashlib.sha256(JPEG).hexdigest()
    assert ok["args"]["ip"] == "203.0.113.7"
    failed = next(e for e in uploads if e["outcome"] == "error")
    assert failed["error_code"] == "ticket_used"


# ------------------------------------------------------------ configuración
def test_upload_settings_from_env(tmp_path: Path) -> None:
    from cpanel_mcp.errors import ConfigError
    from cpanel_mcp.uploads.settings import load_upload_settings

    env = {
        "UPLOAD_ADMIN_SECRET": ADMIN,
        "UPLOAD_PUBLIC_BASE_URL": "https://upload.acopios.cl/",
        "CPANEL_HOST": "acopios.cl",
        "CPANEL_USER": "acopios",
        "CPANEL_TOKEN": "tok-1",
        "UPLOAD_ACCOUNTS": "acopios=/home/acopios,otra=/home/otra",
        "CPANEL_TOKEN_OTRA": "tok-2",
        "UPLOAD_DB_PATH": str(tmp_path / "s.db"),
    }
    loaded = load_upload_settings(env)
    assert loaded.public_base_url == "https://upload.acopios.cl"
    assert set(loaded.accounts) == {"acopios", "otra"}
    assert loaded.accounts["otra"].token == "tok-2"
    assert loaded.allow_zip is False
    assert ADMIN not in repr(loaded) and "tok-1" not in repr(loaded)

    with pytest.raises(ConfigError, match="UPLOAD_ADMIN_SECRET"):
        load_upload_settings({**env, "UPLOAD_ADMIN_SECRET": "corto"})
    with pytest.raises(ConfigError, match="https"):
        load_upload_settings({**env, "UPLOAD_PUBLIC_BASE_URL": "http://x.cl"})
    with pytest.raises(ConfigError, match="CPANEL_TOKEN_OTRA"):
        load_upload_settings({k: v for k, v in env.items() if k != "CPANEL_TOKEN_OTRA"})
