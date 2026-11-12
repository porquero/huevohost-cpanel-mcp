"""Lógica de las acciones de dominios, correo y subdominios."""

from __future__ import annotations

import asyncio
import secrets
import string
from typing import Any

from .cpanel import CPanelClient
from .errors import (
    AccountExistsError,
    CPanelError,
    DatabaseExistsError,
    DomainNotHostedError,
    FileAlreadyExistsError,
    SubdomainExistsError,
    UnsafePathError,
    ValidationError,
)
from .file_ops import (
    PUBLIC_ROOT,
    display_path,
    ensure_safe_remote_path,
    normalize_public_path,
)
from .validators import (
    validate_database_name,
    validate_domain,
    validate_execute,
    validate_quota_mb,
    validate_subdomain,
    validate_username,
)

_PASSWORD_LENGTH = 20
_PASSWORD_SYMBOLS = "!@#$%^&*_-+=?"
_PASSWORD_ALPHABET = (
    string.ascii_lowercase + string.ascii_uppercase + string.digits + _PASSWORD_SYMBOLS
)


def generate_password(length: int = _PASSWORD_LENGTH) -> str:
    """Genera una contraseña fuerte sin caracteres problemáticos para shells."""

    if length < 8:
        raise ValueError("La longitud mínima de contraseña es 8.")

    random_source = secrets.SystemRandom()
    while True:
        characters = [
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.digits),
            secrets.choice(_PASSWORD_SYMBOLS),
        ]
        characters.extend(
            secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - len(characters))
        )
        random_source.shuffle(characters)
        password = "".join(characters)
        if (
            any(char in string.ascii_lowercase for char in password)
            and any(char in string.ascii_uppercase for char in password)
            and any(char in string.digits for char in password)
            and any(char in _PASSWORD_SYMBOLS for char in password)
        ):
            return password


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip().lower()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def _account_domains(data: dict[str, Any]) -> set[str]:
    """Dominios sobre los que se puede crear un subdominio."""

    domains: set[str] = set()
    main = data.get("main_domain")
    if isinstance(main, str) and main.strip():
        domains.add(main.strip().lower())
    domains.update(_string_list(data.get("addon_domains")))
    domains.update(_string_list(data.get("parked_domains")))
    return domains


def _existing_domains(data: dict[str, Any]) -> set[str]:
    return _account_domains(data) | set(_string_list(data.get("sub_domains")))


_RESERVED_DOCROOT_COMPONENTS = {".git", ".hg", ".ssh", ".svn", "cgi-bin"}


def _resolve_docroot(subdomain: str, directory: object) -> str:
    """Normaliza el document root y lo confina a public_html."""

    if directory is None:
        return f"{PUBLIC_ROOT}/{subdomain}"
    if not isinstance(directory, str) or not directory.strip():
        raise ValidationError(
            "directory debe ser una ruta de texto dentro de /public_html, o null "
            "para usar /public_html/<subdominio>."
        )

    docroot = normalize_public_path(directory, allow_root=False)
    components = docroot.split("/")[1:]
    if any(part.lower() in _RESERVED_DOCROOT_COMPONENTS for part in components):
        raise UnsafePathError(
            "El directorio del subdominio no puede estar dentro de .git, .svn, "
            ".hg, .ssh ni cgi-bin."
        )
    return docroot


class Operations:
    """Operaciones de negocio independientes del transporte MCP."""

    def __init__(self, client: CPanelClient) -> None:
        self.client = client

    async def list_domains(self) -> dict[str, Any]:
        data = await self.client.list_domains()
        main_domain = data.get("main_domain")
        main_domain = (
            main_domain.strip().lower()
            if isinstance(main_domain, str) and main_domain.strip()
            else None
        )
        addon_domains = _string_list(data.get("addon_domains"))
        parked_domains = _string_list(data.get("parked_domains"))
        sub_domains = _string_list(data.get("sub_domains"))

        domains: list[dict[str, str]] = []
        if main_domain:
            domains.append({"domain": main_domain, "type": "main"})
        domains.extend(
            {"domain": domain, "type": "addon"} for domain in addon_domains
        )
        domains.extend(
            {"domain": domain, "type": "parked"} for domain in parked_domains
        )
        domains.extend(
            {"domain": domain, "type": "subdomain"} for domain in sub_domains
        )

        return {
            "main_domain": main_domain,
            "addon_domains": addon_domains,
            "parked_domains": parked_domains,
            "sub_domains": sub_domains,
            "domains": domains,
            "count": len(domains),
        }

    async def list_databases(self) -> dict[str, Any]:
        """Lista las bases de datos MySQL de la cuenta (solo lectura)."""

        items = await self.client.list_databases()
        databases: list[dict[str, Any]] = []
        for item in items:
            name = item.get("database")
            if not isinstance(name, str) or not name.strip():
                continue
            users: list[str] = []
            raw_users = item.get("users")
            if isinstance(raw_users, list):
                for user in raw_users:
                    if isinstance(user, dict):
                        user = user.get("user")
                    if isinstance(user, str) and user.strip():
                        users.append(user.strip())
            usage = item.get("disk_usage")
            databases.append(
                {
                    "name": name.strip(),
                    "disk_usage_bytes": (
                        usage
                        if isinstance(usage, int) and not isinstance(usage, bool)
                        else None
                    ),
                    "users": users,
                }
            )
        databases.sort(key=lambda database: database["name"])
        return {"count": len(databases), "databases": databases}

    async def create_database(
        self,
        name: str,
        execute: bool = False,
    ) -> dict[str, Any]:
        """Crea una base de datos MySQL vacía (sin usuarios ni permisos)."""

        name = validate_database_name(name)
        execute = validate_execute(execute)

        if not execute:
            return {
                "dry_run": True,
                "name": name,
                "created": False,
                "verified": False,
                "message": (
                    "Simulación: no se contactó cPanel. Al ejecutar, cPanel "
                    "antepone el prefijo de la cuenta (por ejemplo "
                    "usuario_" + name + "), se comprueba que no exista y se "
                    "crea una base de datos vacía, sin usuarios ni permisos."
                ),
            }

        restrictions = await self.client.get_mysql_restrictions()
        prefix = restrictions.get("prefix")
        prefix = prefix.strip() if isinstance(prefix, str) else ""
        if prefix and not all(char.isalnum() or char == "_" for char in prefix):
            raise CPanelError(
                "cPanel devolvió un prefijo de base de datos con caracteres "
                "inesperados. No se creó nada."
            )
        full_name = name if prefix and name.startswith(prefix) else prefix + name

        max_length = restrictions.get("max_database_name_length")
        if isinstance(max_length, int) and not isinstance(max_length, bool):
            if len(full_name) > max_length:
                raise ValidationError(
                    f"'{full_name}' tiene {len(full_name)} caracteres y cPanel "
                    f"permite como máximo {max_length} (incluido el prefijo "
                    f"'{prefix}'). Usa un nombre más corto."
                )

        existing = await self.client.list_databases()
        if self._database_exists(existing, full_name):
            raise DatabaseExistsError(
                f"La base de datos '{full_name}' ya existe. No se modificó nada."
            )

        try:
            await self.client.create_database(full_name)
        except DatabaseExistsError:
            raise DatabaseExistsError(
                f"La base de datos '{full_name}' ya existe. No se modificó nada."
            ) from None

        verified = False
        verification_error: str | None = None
        try:
            verified = self._database_exists(
                await self.client.list_databases(), full_name
            )
        except CPanelError as exc:
            verification_error = str(exc)

        result: dict[str, Any] = {
            "dry_run": False,
            "name": full_name,
            "created": True,
            "verified": verified,
            "message": (
                "Base de datos creada y verificada en cPanel. Está vacía y "
                "sin usuarios: este servidor no crea usuarios MySQL ni "
                "contraseñas; asígnalos desde cPanel → Bases de datos MySQL."
            ),
        }
        if not verified:
            warning = (
                "cPanel aceptó la creación, pero la base de datos aún no "
                "aparece en el listado de verificación. Revisa cPanel antes de "
                "reutilizar el nombre."
            )
            if verification_error:
                warning += f" Detalle: {verification_error}"
            result["warning"] = warning
        return result

    @staticmethod
    def _database_exists(items: list[dict[str, Any]], name: str) -> bool:
        return any(
            isinstance(item.get("database"), str)
            and item["database"].strip() == name
            for item in items
        )

    async def list_email_accounts(self, domain: str) -> dict[str, Any]:
        domain = validate_domain(domain)

        mail_domains = await self.client.list_mail_domains()
        if domain not in mail_domains:
            raise DomainNotHostedError(
                f"El dominio '{domain}' no es un dominio de correo de esta cuenta cPanel."
            )

        pops = await self.client.list_pops()
        accounts: list[dict[str, Any]] = []
        suffix = f"@{domain}"
        for item in pops:
            email = item.get("email")
            login = item.get("login")
            email_text = email.strip().lower() if isinstance(email, str) else ""
            login_text = login.strip().lower() if isinstance(login, str) else ""
            if not email_text and "@" in login_text:
                email_text = login_text
            if not email_text.endswith(suffix):
                continue

            suspended = (
                str(item.get("suspended_login", 0)) == "1"
                or str(item.get("suspended_incoming", 0)) == "1"
            )
            accounts.append(
                {
                    "email": email_text,
                    "login": login_text or email_text,
                    "suspended": suspended,
                }
            )

        accounts.sort(key=lambda account: account["email"])
        return {
            "domain": domain,
            "count": len(accounts),
            "accounts": accounts,
        }

    async def create_email_account(
        self,
        user: str,
        domain: str,
        quota_mb: int,
        execute: bool = False,
    ) -> dict[str, Any]:
        user = validate_username(user)
        domain = validate_domain(domain)
        quota_mb = validate_quota_mb(quota_mb)
        execute = validate_execute(execute)
        email = f"{user}@{domain}"

        if not execute:
            return {
                "dry_run": True,
                "email": email,
                "domain": domain,
                "quota_mb": quota_mb,
                "created": False,
                "verified": False,
                "message": (
                    "Simulación: no se contactó cPanel y no se generó contraseña. "
                    "Al ejecutar se comprobará que el dominio pertenece a la cuenta, "
                    "que la cuenta no existe y se creará con una contraseña aleatoria."
                ),
            }

        mail_domains = await self.client.list_mail_domains()
        if domain not in mail_domains:
            raise DomainNotHostedError(
                f"El dominio '{domain}' no es un dominio de correo de esta cuenta cPanel."
            )

        pops = await self.client.list_pops()
        if self._email_exists(pops, email):
            raise AccountExistsError(
                f"La cuenta '{email}' ya existe. No se modificó nada."
            )

        password = generate_password()
        try:
            await self.client.add_pop(user, domain, password, quota_mb)
        except AccountExistsError:
            raise AccountExistsError(
                f"La cuenta '{email}' ya existe. No se modificó nada."
            ) from None

        verified = False
        verification_error: str | None = None
        try:
            verified = await self._verify_email(email)
        except CPanelError as exc:
            verification_error = str(exc)

        result: dict[str, Any] = {
            "dry_run": False,
            "email": email,
            "domain": domain,
            "quota_mb": quota_mb,
            "created": True,
            "verified": verified,
            "password": password,
            "message": (
                "Cuenta creada y verificada en el listado de cPanel. "
                "Guarda la contraseña ahora: se muestra una sola vez y no se "
                "registra en logs ni auditoría."
            ),
        }
        if not verified:
            warning = (
                "cPanel aceptó la creación, pero la cuenta aún no aparece en el "
                "listado de verificación. Revisa cPanel antes de reutilizar el nombre."
            )
            if verification_error:
                warning = f"{warning} Detalle: {verification_error}"
            result["warning"] = warning
            result["message"] = (
                "Cuenta creada. La verificación inmediata no la encontró todavía. "
                "Guarda la contraseña ahora: se muestra una sola vez."
            )
        return result

    async def create_subdomain(
        self,
        subdomain: str,
        domain: str,
        directory: str | None = None,
        execute: bool = False,
    ) -> dict[str, Any]:
        """Crea un subdominio sobre un dominio existente de la cuenta.

        No crea addon domains ni parked domains: solo subdominios. El
        document root queda confinado a /public_html y no puede existir.
        """

        subdomain = validate_subdomain(subdomain)
        domain = validate_domain(domain)
        execute = validate_execute(execute)
        docroot = _resolve_docroot(subdomain, directory)
        fqdn = f"{subdomain}.{domain}"

        if not execute:
            return {
                "dry_run": True,
                "subdomain": subdomain,
                "domain": domain,
                "fqdn": fqdn,
                "document_root": display_path(docroot),
                "created": False,
                "verified": False,
                "message": (
                    "Simulación: no se contactó cPanel y no se creó nada. Al "
                    "ejecutar se comprobará que el dominio padre pertenece a la "
                    "cuenta, que el subdominio no existe y que el directorio está "
                    "libre dentro de /public_html."
                ),
            }

        data = await self.client.list_domains()
        if domain not in _account_domains(data):
            raise DomainNotHostedError(
                f"El dominio '{domain}' no pertenece a esta cuenta cPanel. Solo "
                "se pueden crear subdominios sobre el dominio principal, un "
                "addon domain o un parked domain de la cuenta."
            )

        if fqdn in _existing_domains(data):
            raise SubdomainExistsError(
                f"El subdominio '{fqdn}' ya existe. No se modificó nada."
            )

        existing = await ensure_safe_remote_path(
            self.client,
            docroot,
            must_exist=False,
            expect_dir=True,
        )
        if existing is not None:
            raise FileAlreadyExistsError(
                f"El directorio '{display_path(docroot)}' ya existe. Elige otro "
                "directory o crea el subdominio manualmente desde cPanel si "
                "quieres reutilizar contenido existente."
            )

        try:
            await self.client.add_subdomain(
                subdomain,
                domain,
                "/" + docroot,
            )
        except SubdomainExistsError:
            raise SubdomainExistsError(
                f"El subdominio '{fqdn}' ya existe. No se modificó nada."
            ) from None

        verified = False
        verification_error: str | None = None
        try:
            verified = await self._verify_subdomain(fqdn)
        except CPanelError as exc:
            verification_error = str(exc)

        result: dict[str, Any] = {
            "dry_run": False,
            "subdomain": subdomain,
            "domain": domain,
            "fqdn": fqdn,
            "document_root": display_path(docroot),
            "created": True,
            "verified": verified,
            "message": (
                "Subdominio creado y verificado en cPanel. Sube el contenido "
                f"con upload_file o create_file dentro de {display_path(docroot)}."
            ),
        }
        if not verified:
            warning = (
                "cPanel aceptó la creación, pero el subdominio aún no aparece en "
                "el listado de verificación. Revisa cPanel antes de reutilizar "
                "el nombre."
            )
            if verification_error:
                warning = f"{warning} Detalle: {verification_error}"
            result["warning"] = warning
            result["message"] = (
                "Subdominio creado. La verificación inmediata no lo encontró "
                "todavía. Revisa cPanel."
            )
        return result

    async def _verify_subdomain(self, fqdn: str) -> bool:
        for attempt in range(2):
            entries = await self.client.list_subdomains()
            for item in entries:
                value = item.get("domain")
                if isinstance(value, str) and value.strip().lower() == fqdn:
                    return True
            if attempt == 0:
                await asyncio.sleep(0.5)
        return False

    @staticmethod
    def _email_exists(pops: list[dict[str, Any]], email: str) -> bool:
        wanted = email.lower()
        for item in pops:
            for key in ("email", "login"):
                value = item.get(key)
                if isinstance(value, str) and value.strip().lower() == wanted:
                    return True
        return False

    async def _verify_email(self, email: str) -> bool:
        for attempt in range(2):
            pops = await self.client.list_pops()
            if self._email_exists(pops, email):
                return True
            if attempt == 0:
                await asyncio.sleep(0.5)
        return False
