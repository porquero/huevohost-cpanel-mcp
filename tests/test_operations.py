from __future__ import annotations

from typing import Any

import pytest

from cpanel_mcp.errors import (
    AccountExistsError,
    DomainNotHostedError,
    FileAlreadyExistsError,
    RemoteFileNotFoundError,
    SubdomainExistsError,
    SymlinkError,
    UnsafePathError,
    ValidationError,
)
from cpanel_mcp.operations import Operations


class FakeClient:
    def __init__(
        self,
        *,
        mail_domains: list[str] | None = None,
        pops: list[dict[str, Any]] | None = None,
        add_succeeds: bool = True,
        verification_visible: bool = True,
        domains: dict[str, Any] | None = None,
        subdomains: list[dict[str, Any]] | None = None,
        existing_paths: set[str] | None = None,
        symlink_paths: set[str] | None = None,
        subdomain_succeeds: bool = True,
        subdomain_verification_visible: bool = True,
    ) -> None:
        self.mail_domains = mail_domains or ["example.com"]
        self.pops = list(pops or [])
        self.add_succeeds = add_succeeds
        self.verification_visible = verification_visible
        self.domains = domains
        self.subdomains = list(subdomains or [])
        self.existing_paths = set(existing_paths or ())
        self.symlink_paths = set(symlink_paths or ())
        self.subdomain_succeeds = subdomain_succeeds
        self.subdomain_verification_visible = subdomain_verification_visible
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.added: dict[str, Any] | None = None
        self.added_subdomain: dict[str, Any] | None = None

    async def list_mail_domains(self) -> list[str]:
        self.calls.append(("list_mail_domains", {}))
        return self.mail_domains

    async def list_pops(self) -> list[dict[str, Any]]:
        self.calls.append(("list_pops", {}))
        return list(self.pops)

    async def add_pop(
        self,
        username: str,
        domain: str,
        password: str,
        quota_mb: int,
    ) -> None:
        self.calls.append(
            (
                "add_pop",
                {
                    "username": username,
                    "domain": domain,
                    "password": password,
                    "quota_mb": quota_mb,
                },
            )
        )
        if self.add_succeeds:
            self.added = {
                "email": f"{username}@{domain}",
                "login": f"{username}@{domain}",
            }
            if self.verification_visible:
                self.pops.append(self.added)

    async def list_domains(self) -> dict[str, Any]:
        self.calls.append(("list_domains", {}))
        if self.domains is not None:
            return dict(self.domains)
        return {
            "main_domain": "example.com",
            "addon_domains": ["addon.example"],
            "parked_domains": None,
            "sub_domains": ["sub.example.com"],
        }

    async def list_subdomains(self) -> list[dict[str, Any]]:
        self.calls.append(("list_subdomains", {}))
        return list(self.subdomains)

    async def add_subdomain(
        self,
        subdomain: str,
        root_domain: str,
        directory: str,
    ) -> None:
        self.calls.append(
            (
                "add_subdomain",
                {
                    "subdomain": subdomain,
                    "root_domain": root_domain,
                    "directory": directory,
                },
            )
        )
        if not self.subdomain_succeeds:
            return
        entry = {
            "domain": f"{subdomain}.{root_domain}",
            "subdomain": subdomain,
            "rootdomain": root_domain,
            "basedir": directory.lstrip("/"),
        }
        self.added_subdomain = entry
        if self.subdomain_verification_visible:
            self.subdomains.append(entry)

    async def get_file_information(self, path: str) -> dict[str, Any]:
        self.calls.append(("get_file_information", path))
        if path in self.symlink_paths:
            return {
                "exists": 1,
                "type": "symlink",
                "size": "0",
                "mimetype": "inode/symlink",
            }
        exists = (
            path == "public_html"
            or path in self.existing_paths
            or any(item.startswith(path + "/") for item in self.existing_paths)
        )
        if not exists:
            raise RemoteFileNotFoundError("no existe")
        return {
            "exists": 1,
            "type": "dir",
            "size": "4096",
            "mimetype": "httpd/unix-directory",
        }


async def test_dry_run_makes_no_calls() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_email_account(
        "juan", "example.com", 500, execute=False
    )
    assert client.calls == []
    assert result["dry_run"] is True
    assert result["created"] is False
    assert "password" not in result


async def test_create_executes_and_verifies() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_email_account(
        "Juan", "Example.com", 500, execute=True
    )
    assert result["created"] is True
    assert result["verified"] is True
    assert result["email"] == "juan@example.com"
    assert len(result["password"]) == 20
    assert [name for name, _ in client.calls] == [
        "list_mail_domains",
        "list_pops",
        "add_pop",
        "list_pops",
    ]
    added = client.calls[2][1]
    assert added["password"] == result["password"]
    assert added["quota_mb"] == 500


async def test_existing_account_stops_before_add() -> None:
    client = FakeClient(
        pops=[{"email": "juan@example.com", "login": "juan@example.com"}]
    )
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(AccountExistsError):
        await operations.create_email_account(
            "juan", "example.com", 500, execute=True
        )
    assert "add_pop" not in [name for name, _ in client.calls]


async def test_domain_not_hosted_stops_before_add() -> None:
    client = FakeClient(mail_domains=["otro.example"])
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(DomainNotHostedError):
        await operations.create_email_account(
            "juan", "example.com", 500, execute=True
        )
    assert "add_pop" not in [name for name, _ in client.calls]


async def test_verification_failure_keeps_password_once() -> None:
    client = FakeClient(verification_visible=False)
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_email_account(
        "juan", "example.com", 500, execute=True
    )
    assert result["created"] is True
    assert result["verified"] is False
    assert "warning" in result
    assert len(result["password"]) == 20


async def test_list_email_accounts_filters_and_reports_suspension() -> None:
    client = FakeClient(
        pops=[
            {
                "email": "a@example.com",
                "login": "a@example.com",
                "suspended_login": 0,
                "suspended_incoming": 0,
            },
            {
                "email": "b@other.com",
                "login": "b@other.com",
            },
            {
                "email": "c@example.com",
                "login": "c@example.com",
                "suspended_login": 0,
                "suspended_incoming": 1,
            },
            {"email": "tester", "login": "Main Account"},
        ]
    )
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.list_email_accounts("Example.com")
    assert result["domain"] == "example.com"
    assert result["count"] == 2
    assert [item["email"] for item in result["accounts"]] == [
        "a@example.com",
        "c@example.com",
    ]
    assert result["accounts"][1]["suspended"] is True


async def test_list_domains_normalizes() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.list_domains()
    assert result["main_domain"] == "example.com"
    assert result["parked_domains"] == []
    assert result["count"] == 3
    assert {"domain": "sub.example.com", "type": "subdomain"} in result["domains"]


async def test_create_subdomain_dry_run_makes_no_calls() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_subdomain(
        "Blog", "Example.com", execute=False
    )
    assert client.calls == []
    assert result["dry_run"] is True
    assert result["fqdn"] == "blog.example.com"
    assert result["document_root"] == "/public_html/blog"
    assert result["created"] is False
    assert result["verified"] is False


async def test_create_subdomain_executes_and_verifies() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_subdomain(
        "blog", "example.com", execute=True
    )
    assert result["created"] is True
    assert result["verified"] is True
    assert result["fqdn"] == "blog.example.com"
    assert result["document_root"] == "/public_html/blog"
    assert [name for name, _ in client.calls] == [
        "list_domains",
        "get_file_information",
        "get_file_information",
        "add_subdomain",
        "list_subdomains",
    ]
    added = client.calls[3][1]
    assert added == {
        "subdomain": "blog",
        "root_domain": "example.com",
        "directory": "/public_html/blog",
    }


async def test_create_subdomain_uses_custom_docroot() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_subdomain(
        "blog",
        "example.com",
        directory="/public_html/sites/blog",
        execute=True,
    )
    assert result["document_root"] == "/public_html/sites/blog"
    assert client.calls[3][1]["directory"] == "/public_html/sites/blog"
    assert client.added_subdomain is not None
    assert client.added_subdomain["basedir"] == "public_html/sites/blog"


async def test_create_subdomain_accepts_addon_domain() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_subdomain(
        "blog", "addon.example", execute=True
    )
    assert result["fqdn"] == "blog.addon.example"


async def test_create_subdomain_rejects_foreign_domain() -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(DomainNotHostedError):
        await operations.create_subdomain(
            "blog", "otro.example", execute=True
        )
    assert "add_subdomain" not in [name for name, _ in client.calls]


async def test_create_subdomain_rejects_existing_subdomain() -> None:
    client = FakeClient(
        domains={
            "main_domain": "example.com",
            "addon_domains": [],
            "parked_domains": [],
            "sub_domains": ["blog.example.com"],
        }
    )
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(SubdomainExistsError):
        await operations.create_subdomain(
            "blog", "example.com", execute=True
        )
    assert "add_subdomain" not in [name for name, _ in client.calls]


async def test_create_subdomain_rejects_existing_docroot() -> None:
    client = FakeClient(existing_paths={"public_html/blog"})
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(FileAlreadyExistsError):
        await operations.create_subdomain(
            "blog", "example.com", execute=True
        )
    assert "add_subdomain" not in [name for name, _ in client.calls]


async def test_create_subdomain_rejects_symlink_docroot() -> None:
    client = FakeClient(symlink_paths={"public_html/blog"})
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(SymlinkError):
        await operations.create_subdomain(
            "blog", "example.com", execute=True
        )
    assert "add_subdomain" not in [name for name, _ in client.calls]


@pytest.mark.parametrize(
    "raw",
    [
        "../blog",
        "public_html",
        "/etc/blog",
        "public_html/.git/blog",
        "public_html/cgi-bin/blog",
    ],
)
async def test_create_subdomain_rejects_unsafe_docroot(raw: str) -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(UnsafePathError):
        await operations.create_subdomain(
            "blog",
            "example.com",
            directory=raw,
            execute=True,
        )
    assert client.calls == []


@pytest.mark.parametrize("raw", ["www", "mail", "cpanel", "sub.example.com"])
async def test_create_subdomain_rejects_reserved_or_compound_label(
    raw: str,
) -> None:
    client = FakeClient()
    operations = Operations(client)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        await operations.create_subdomain(
            raw, "example.com", execute=True
        )
    assert client.calls == []


async def test_subdomain_verification_failure_returns_warning() -> None:
    client = FakeClient(subdomain_verification_visible=False)
    operations = Operations(client)  # type: ignore[arg-type]
    result = await operations.create_subdomain(
        "blog", "example.com", execute=True
    )
    assert result["created"] is True
    assert result["verified"] is False
    assert "warning" in result
