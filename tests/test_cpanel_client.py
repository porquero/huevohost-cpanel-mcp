from __future__ import annotations

import httpx
import pytest

from cpanel_mcp.config import Settings
from cpanel_mcp.cpanel import CPanelClient
from cpanel_mcp.errors import (
    AccountExistsError,
    CPanelAuthError,
    CPanelPermissionError,
    CPanelProtocolError,
    CPanelTimeoutError,
    FileOperationError,
    MailRoleDisabledError,
    RemoteFileNotFoundError,
    SubdomainExistsError,
)

from .conftest import make_http_client, uapi_payload


async def test_request_uses_token_header_and_params(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["hide"] = request.url.params.get("hide_temporary_domains")
        return httpx.Response(200, json=uapi_payload({"main_domain": "example.com"}))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    result = await client.list_domains()
    await client.aclose()

    assert result["main_domain"] == "example.com"
    assert str(seen["url"]).startswith(
        "https://cpanel.test:2083/execute/DomainInfo/list_domains"
    )
    assert seen["authorization"] == "cpanel tester:TOKEN-SECRETO-123"
    assert seen["hide"] == "1"


async def test_401_maps_to_invalid_token(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="no")

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelAuthError):
        await client.list_pops()
    await client.aclose()


async def test_403_with_role_error(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json=uapi_payload(
                None,
                status=0,
                errors=[
                    "The system disabled the function because the Receive Mail "
                    "role is disabled."
                ],
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(MailRoleDisabledError):
        await client.list_pops()
    await client.aclose()


async def test_403_without_json_is_permission_error(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelPermissionError):
        await client.list_pops()
    await client.aclose()


async def test_timeout_maps_to_clear_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelTimeoutError) as exc_info:
        await client.list_pops()
    assert "30" in str(exc_info.value)
    await client.aclose()


async def test_existing_account_error(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=uapi_payload(
                None,
                status=0,
                errors=["The email account already exists."],
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(AccountExistsError):
        await client.add_pop("juan", "example.com", "secret", 500)
    await client.aclose()


async def test_flat_uapi_payload_is_supported(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "metadata": {},
                "messages": None,
                "status": 1,
                "warnings": None,
                "errors": None,
                "data": {"main_domain": "flat.example"},
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    result = await client.list_domains()
    assert result["main_domain"] == "flat.example"
    await client.aclose()


async def test_list_pops_parses_data(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=uapi_payload(
                [
                    {"email": "a@example.com", "login": "a@example.com"},
                    {"email": "b@example.com", "login": "b@example.com"},
                    "ignored",
                ]
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    pops = await client.list_pops()
    assert [item["email"] for item in pops] == [
        "a@example.com",
        "b@example.com",
    ]
    await client.aclose()


async def test_malformed_json(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no json</html>")

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelProtocolError):
        await client.list_pops()
    await client.aclose()


async def test_missing_result_object(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelProtocolError):
        await client.list_pops()
    await client.aclose()


async def test_http_500(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(CPanelProtocolError):
        await client.list_pops()
    await client.aclose()


async def test_default_timeout_is_30_seconds(settings: Settings) -> None:
    client = CPanelClient(settings)
    assert client._client.timeout.read == 30.0
    assert client._client.timeout.connect == 30.0
    await client.aclose()


async def test_add_subdomain_sends_expected_params(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["domain"] = request.url.params.get("domain")
        seen["rootdomain"] = request.url.params.get("rootdomain")
        seen["dir"] = request.url.params.get("dir")
        seen["disallowdot"] = request.url.params.get("disallowdot")
        return httpx.Response(200, json=uapi_payload(None))

    client = CPanelClient(settings, http_client=make_http_client(handler))
    await client.add_subdomain("blog", "example.com", "/public_html/blog")
    await client.aclose()

    assert str(seen["url"]).startswith(
        "https://cpanel.test:2083/execute/SubDomain/addsubdomain"
    )
    assert seen["domain"] == "blog"
    assert seen["rootdomain"] == "example.com"
    assert seen["dir"] == "/public_html/blog"
    assert seen["disallowdot"] == "1"


async def test_list_subdomains_parses_entries(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/execute/SubDomain/listsubdomains" in str(request.url)
        return httpx.Response(
            200,
            json=uapi_payload(
                [
                    {
                        "domain": "blog.example.com",
                        "subdomain": "blog",
                        "rootdomain": "example.com",
                        "basedir": "public_html/blog",
                    },
                    "ignorado",
                ]
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    entries = await client.list_subdomains()
    await client.aclose()
    assert entries == [
        {
            "domain": "blog.example.com",
            "subdomain": "blog",
            "rootdomain": "example.com",
            "basedir": "public_html/blog",
        }
    ]


async def test_subdomain_already_exists_maps_to_error(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": 0,
                "errors": [
                    'The subdomain “blog.example.com” already exists.',
                ],
                "messages": None,
                "data": None,
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(SubdomainExistsError):
        await client.add_subdomain("blog", "example.com", "/public_html/blog")
    await client.aclose()


async def test_list_files_accepts_flat_list(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/execute/Fileman/list_files" in str(request.url)
        assert request.url.params.get("dir") == "public_html/site"
        assert request.url.params.get("show_hidden") == "0"
        return httpx.Response(
            200,
            json={
                "status": 1,
                "data": [
                    {"file": "index.html", "type": "file", "size": "10"},
                    {"file": "sub", "type": "dir", "size": "4096"},
                    "ignorado",
                ],
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    entries = await client.list_files("public_html/site")
    assert [entry["file"] for entry in entries] == ["index.html", "sub"]
    await client.aclose()


async def test_list_files_accepts_dirs_and_files_object(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=uapi_payload(
                {
                    "dirs": [{"file": "sub", "type": "dir"}],
                    "files": [{"file": "a.txt", "type": "file"}],
                }
            ),
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    entries = await client.list_files("public_html", show_hidden=True)
    assert [entry["file"] for entry in entries] == ["sub", "a.txt"]
    await client.aclose()


async def test_upload_file_sends_multipart(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type")
        seen["dir"] = request.url.params.get("dir")
        seen["overwrite"] = request.url.params.get("overwrite")
        seen["permissions"] = request.url.params.get("permissions")
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "status": 1,
                "data": {"succeeded": 1, "failed": 0, "uploads": []},
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    await client.upload_file(
        "public_html/site",
        "index.html",
        b"<h1>Hola</h1>",
        overwrite=False,
    )
    await client.aclose()

    assert str(seen["content_type"]).startswith("multipart/form-data; boundary=")
    assert seen["dir"] == "public_html/site"
    assert seen["overwrite"] == "0"
    assert seen["permissions"] == "0644"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="file-0"' in body
    assert b'filename="index.html"' in body
    assert b"<h1>Hola</h1>" in body


async def test_upload_file_failed_result_raises(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": 1,
                "data": {
                    "succeeded": 0,
                    "failed": 1,
                    "uploads": [
                        {
                            "file": "index.html",
                            "reason": "Upload of “index.html” failed.",
                        }
                    ],
                },
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(FileOperationError) as exc_info:
        await client.upload_file(
            "public_html/site",
            "index.html",
            b"<h1>Hola</h1>",
            overwrite=False,
        )
    assert "index.html" in str(exc_info.value)
    await client.aclose()


async def test_fileman_not_found_maps_to_remote_file_not_found(
    settings: Settings,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": 0,
                "errors": ["El archivo “no-existe.txt” no está disponible."],
                "messages": None,
                "data": None,
            },
        )

    client = CPanelClient(settings, http_client=make_http_client(handler))
    with pytest.raises(RemoteFileNotFoundError):
        await client.get_file_information("public_html/no-existe.txt")
    await client.aclose()



