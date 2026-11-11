from __future__ import annotations

import re
import string

import pytest

from cpanel_mcp.errors import ValidationError
from cpanel_mcp.operations import generate_password
from cpanel_mcp.validators import (
    validate_domain,
    validate_email,
    validate_execute,
    validate_quota_mb,
    validate_subdomain,
    validate_username,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Juan.Perez", "juan.perez"),
        ("  ventas  ", "ventas"),
        ("a", "a"),
        ("cuenta_1", "cuenta_1"),
    ],
)
def test_username_valid(raw: str, expected: str) -> None:
    assert validate_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "cpanel", ".juan", "juan.", "juan..perez", "juan perez", "juan/perez", "x" * 65],
)
def test_username_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_username(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", "example.com"),
        ("sub.example.co.uk", "sub.example.co.uk"),
        ("xn--caf-dma.com", "xn--caf-dma.com"),
    ],
)
def test_domain_valid(raw: str, expected: str) -> None:
    assert validate_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "example",
        "http://example.com",
        "example.com.",
        "-bad.com",
        "bad-.com",
        "exa mple.com",
        "example..com",
        "example.com/path",
    ],
)
def test_domain_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_domain(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Blog", "blog"),
        ("  promo-2026  ", "promo-2026"),
        ("a1", "a1"),
        ("mi-sitio", "mi-sitio"),
    ],
)
def test_subdomain_valid(raw: str, expected: str) -> None:
    assert validate_subdomain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "www",
        "mail",
        "webmail",
        "cpanel",
        "cgi-bin",
        "-blog",
        "blog-",
        "sub.example.com",
        "a_b",
        "a b",
        "blog/",
        "x" * 64,
    ],
)
def test_subdomain_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_subdomain(raw)


def test_quota_valid() -> None:
    assert validate_quota_mb(1) == 1
    assert validate_quota_mb(5120) == 5120


@pytest.mark.parametrize("raw", [0, 5121, -1, True, 1.5, "500", None])
def test_quota_invalid(raw: object) -> None:
    with pytest.raises(ValidationError):
        validate_quota_mb(raw)


def test_execute_strict() -> None:
    assert validate_execute(True) is True
    assert validate_execute(False) is False
    for raw in (1, 0, "true", "false", None):
        with pytest.raises(ValidationError):
            validate_execute(raw)


def test_email_valid() -> None:
    assert validate_email("Juan@Example.com") == "juan@example.com"


def test_password_strength() -> None:
    generated = {generate_password() for _ in range(50)}
    assert len(generated) == 50
    for password in generated:
        assert len(password) == 20
        assert re.search(r"[a-z]", password)
        assert re.search(r"[A-Z]", password)
        assert re.search(r"[0-9]", password)
        assert any(char in "!@#$%^&*_-+=?" for char in password)
        assert all(char in set(string.printable) for char in password)
        assert not any(char in password for char in "\"'\\ ")
