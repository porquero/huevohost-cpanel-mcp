"""Validaciones estrictas de las entradas expuestas al modelo."""

from __future__ import annotations

import re

from .errors import ValidationError

_USER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_RESERVED_USERS = {"cpanel"}

# Nombres que cPanel o el correo del dominio ya usan. Crear un subdominio con
# estos nombres puede sobrescribir un alias o romper servicios.
_RESERVED_SUBDOMAINS = {
    "autoconfig",
    "autodiscover",
    "cgi-bin",
    "cpcalendars",
    "cpcontacts",
    "cpanel",
    "ftp",
    "localhost",
    "mail",
    "webdisk",
    "webmail",
    "whm",
    "www",
}


def validate_username(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("El usuario debe ser texto.")
    user = value.strip().lower()
    if not user:
        raise ValidationError("El usuario no puede estar vacío.")
    if len(user) > 64:
        raise ValidationError("El usuario no puede superar 64 caracteres.")
    if ".." in user:
        raise ValidationError("El usuario no puede contener puntos consecutivos.")
    if user in _RESERVED_USERS:
        raise ValidationError(f"'{user}' es un nombre reservado por cPanel.")
    if not _USER_RE.fullmatch(user):
        raise ValidationError(
            "El usuario solo puede contener minúsculas, números, punto, guion "
            "y guion bajo, y debe empezar y terminar con letra o número."
        )
    return user


def validate_domain(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("El dominio debe ser texto.")
    domain = value.strip().lower()
    if not domain:
        raise ValidationError("El dominio no puede estar vacío.")
    if domain.endswith("."):
        raise ValidationError("El dominio no debe terminar en punto.")
    if len(domain) > 253:
        raise ValidationError("El dominio no puede superar 253 caracteres.")
    labels = domain.split(".")
    if len(labels) < 2:
        raise ValidationError("El dominio debe incluir al menos un punto.")
    for label in labels:
        if not _LABEL_RE.fullmatch(label):
            raise ValidationError(
                "El dominio no tiene un formato válido. Usa un FQDN en minúsculas "
                "sin esquema, ruta, puerto ni guiones al inicio o al final."
            )
    return domain


def validate_subdomain(value: object) -> str:
    """Valida solo la etiqueta del subdominio, sin el dominio padre."""

    if not isinstance(value, str):
        raise ValidationError("El subdominio debe ser texto.")
    label = value.strip().lower()
    if not label:
        raise ValidationError("El subdominio no puede estar vacío.")
    if len(label) > 63:
        raise ValidationError("El subdominio no puede superar 63 caracteres.")
    if "." in label:
        raise ValidationError(
            "Indica solo la etiqueta del subdominio, por ejemplo 'blog'. "
            "El dominio padre va en el parámetro domain."
        )
    if label in _RESERVED_SUBDOMAINS:
        raise ValidationError(
            f"'{label}' es un nombre reservado por cPanel o por el correo del "
            "dominio."
        )
    if not _LABEL_RE.fullmatch(label):
        raise ValidationError(
            "El subdominio solo puede contener minúsculas, números y guiones, "
            "y debe empezar y terminar con letra o número."
        )
    return label


def validate_quota_mb(value: object) -> int:
    if type(value) is not int:
        raise ValidationError("La cuota debe ser un número entero de MB.")
    if not 1 <= value <= 5120:
        raise ValidationError("La cuota debe estar entre 1 y 5120 MB.")
    return value


def validate_execute(value: object) -> bool:
    if type(value) is not bool:
        raise ValidationError("execute debe ser true o false.")
    return value


def validate_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("El correo debe ser texto.")
    email = value.strip().lower()
    if email.count("@") != 1:
        raise ValidationError("El correo no tiene un formato válido.")
    user, domain = email.split("@", 1)
    validated = f"{validate_username(user)}@{validate_domain(domain)}"
    if len(validated) > 254:
        raise ValidationError("El correo no puede superar 254 caracteres.")
    return validated
