"""Cliente mínimo de la UAPI de cPanel sobre httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .errors import (
    AccountExistsError,
    CPanelAPIError,
    CPanelAuthError,
    CPanelConnectionError,
    CPanelPermissionError,
    CPanelProtocolError,
    CPanelTimeoutError,
    DatabaseExistsError,
    DatabaseUserExistsError,
    DomainNotHostedError,
    FileAlreadyExistsError,
    FileOperationError,
    FileStorageRoleDisabledError,
    MailRoleDisabledError,
    MysqlRoleDisabledError,
    RemoteFileNotFoundError,
    SubdomainExistsError,
)

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass
class UAPIResult:
    data: Any
    status: int = 1
    errors: list[Any] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    warnings: list[Any] = field(default_factory=list)


def _error_text(errors: list[Any], messages: list[Any]) -> str:
    parts = [str(item) for item in [*errors, *messages] if item]
    return " | ".join(parts).strip()


def _classify_api_error(
    module: str,
    function: str,
    errors: list[Any],
    messages: list[Any],
) -> Exception:
    text = _error_text(errors, messages)
    lower = text.lower()

    if module == "SubDomain" and any(
        term in lower
        for term in (
            "already exists",
            "already in use",
            "already configured",
            "already added",
        )
    ):
        return SubdomainExistsError(
            "El subdominio ya existe en cPanel. No se modificó nada."
        )

    if module == "Mysql" and function == "create_user" and (
        "already exists" in lower or "already in use" in lower
    ):
        return DatabaseUserExistsError(
            "El usuario MySQL ya existe en cPanel. No se modificó nada."
        )

    if module == "Mysql" and (
        "already exists" in lower or "already in use" in lower
    ):
        return DatabaseExistsError(
            "La base de datos ya existe en cPanel. No se modificó nada."
        )

    if module == "Mysql" and (
        "mysql" in lower or "database" in lower or "role" in lower
    ) and ("disabled" in lower or "not enabled" in lower):
        return MysqlRoleDisabledError(
            "El rol de bases de datos (MySQL / Database Server) está "
            "desactivado en esta cuenta cPanel."
        )

    if "already exists" in lower or "already in use" in lower:
        if module == "Email":
            return AccountExistsError(
                "La cuenta de correo ya existe en cPanel. No se modificó nada."
            )
        return FileAlreadyExistsError(
            "El archivo o directorio ya existe. No se sobrescribió."
        )

    if ("file storage" in lower or "fileman" in lower) and (
        "disabled" in lower or "not enabled" in lower
    ):
        return FileStorageRoleDisabledError(
            "El rol de almacenamiento de archivos (File Storage) está desactivado "
            "en esta cuenta cPanel."
        )

    not_found_terms = (
        "not available",
        "does not exist",
        "no such file",
        "not found",
        "no está disponible",
        "no existe",
    )
    if module == "Fileman" and any(term in lower for term in not_found_terms):
        return RemoteFileNotFoundError(
            "El archivo o directorio no existe en la cuenta cPanel."
        )

    if module == "SubDomain" and any(
        term in lower
        for term in (
            "does not exist",
            "not a valid domain",
            "does not belong",
            "no existe",
        )
    ):
        return DomainNotHostedError(
            "El dominio padre no pertenece a esta cuenta cPanel o no es válido."
        )

    role_terms = ("receive mail", "email role", "mail role", "email module")
    if any(term in lower for term in role_terms) and (
        "disabled" in lower or "not enabled" in lower
    ):
        return MailRoleDisabledError(
            "El rol de correo (Receive Mail) está desactivado en esta cuenta cPanel. "
            "Actívalo en WHM antes de crear cuentas de correo."
        )

    if (
        "access denied" in lower
        or "not permitted" in lower
        or "permission" in lower
        or "unauthorized" in lower
    ):
        return CPanelPermissionError(
            "cPanel rechazó la operación por permisos. Revisa el token y los roles "
            "de la cuenta."
        )

    detail = text or "sin detalle del servidor"
    return CPanelAPIError(
        f"cPanel rechazó {module}::{function}: {detail}",
        details=errors,
    )


class CPanelClient:
    """Cliente async con timeout fijo y autenticación por token UAPI."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._settings = settings
        self._timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            verify=settings.verify_ssl,
            follow_redirects=False,
            headers={"User-Agent": "huevohost-cpanel-mcp/0.1.0"},
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "CPanelClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def call(
        self,
        module: str,
        function: str,
        params: dict[str, Any] | None = None,
    ) -> UAPIResult:
        return await self._request(
            "GET",
            module,
            function,
            params=params,
        )

    async def post(
        self,
        module: str,
        function: str,
        data: dict[str, Any],
    ) -> UAPIResult:
        """Llama a la UAPI con POST y parámetros en el cuerpo del formulario.

        Se usa cuando hay secretos (contraseñas): así no quedan en la URL ni en
        los registros de acceso de cPanel.
        """

        return await self._request("POST", module, function, data=data)

    async def _request(
        self,
        method: str,
        module: str,
        function: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> UAPIResult:
        url = f"{self._settings.base_url}/execute/{module}/{function}"
        headers = {
            "Authorization": f"cpanel {self._settings.user}:{self._settings.token}"
        }

        try:
            response = await self._client.request(
                method,
                url,
                params=params or {},
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise CPanelTimeoutError(
                f"cPanel no respondió en {self._timeout:g} segundos. "
                "La operación no se completó."
            ) from exc
        except httpx.ConnectError as exc:
            detail = str(exc).upper()
            if "CERTIFICATE" in detail or "SSL" in detail or "TLS" in detail:
                raise CPanelConnectionError(
                    "No se pudo verificar el certificado TLS de cPanel. "
                    "Si el servidor usa un certificado autofirmado en pruebas, "
                    "configura CPANEL_VERIFY_SSL=false solo para ese entorno."
                ) from exc
            raise CPanelConnectionError(
                "No se pudo conectar con cPanel. Revisa CPANEL_HOST, CPANEL_PORT "
                "y la conectividad de red."
            ) from exc
        except httpx.HTTPError as exc:
            raise CPanelConnectionError(
                "Error de red al comunicarse con cPanel."
            ) from exc

        if response.status_code in {401, 403}:
            parsed = self._try_parse(response)
            if parsed is not None:
                result = self._result_object(parsed)
                if result is not None:
                    errors = list(result.get("errors") or [])
                    messages = list(result.get("messages") or [])
                    if errors or messages:
                        raise _classify_api_error(module, function, errors, messages)

            if response.status_code == 401:
                raise CPanelAuthError(
                    "Token inválido o no aceptado por cPanel (HTTP 401). "
                    "Revisa CPANEL_USER y CPANEL_TOKEN."
                )
            raise CPanelPermissionError(
                "cPanel rechazó la petición (HTTP 403). El token puede no tener "
                "permisos sobre esta función, o el rol de correo o de archivos "
                "puede estar desactivado."
            )

        if response.status_code >= 500:
            raise CPanelProtocolError(
                f"cPanel respondió con un error interno (HTTP {response.status_code}). "
                "Inténtalo de nuevo más tarde."
            )

        if not 200 <= response.status_code < 300:
            raise CPanelProtocolError(
                f"cPanel respondió HTTP {response.status_code}. "
                "Revisa la configuración del servidor."
            )

        parsed = self._try_parse(response)
        if parsed is None:
            raise CPanelProtocolError(
                "cPanel devolvió una respuesta que no es JSON válido."
            )
        return self._parse_payload(parsed, module, function)

    @staticmethod
    def _try_parse(response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _result_object(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Acepta la respuesta UAPI plana y la variante envuelta en 'result'."""

        result = payload.get("result")
        if isinstance(result, dict):
            return result
        if "status" in payload and "data" in payload:
            return payload
        return None

    def _parse_payload(
        self,
        payload: dict[str, Any],
        module: str,
        function: str,
    ) -> UAPIResult:
        result = self._result_object(payload)
        if result is None:
            raise CPanelProtocolError(
                "Respuesta UAPI inesperada: no contiene 'status' y 'data'. "
                "¿El host y el puerto son de cPanel?"
            )

        errors = list(result.get("errors") or [])
        messages = list(result.get("messages") or [])
        warnings = list(result.get("warnings") or [])
        status = result.get("status")

        if status not in {1, "1", True}:
            raise _classify_api_error(module, function, errors, messages)

        return UAPIResult(
            data=result.get("data"),
            status=1,
            errors=errors,
            messages=messages,
            warnings=warnings,
        )

    async def list_mail_domains(self) -> list[str]:
        result = await self.call(
            "Email",
            "list_mail_domains",
            {"return_temporary_domain": 0},
        )
        data = result.data or []
        if not isinstance(data, list):
            raise CPanelProtocolError(
                "Email::list_mail_domains devolvió un formato inesperado."
            )
        domains: list[str] = []
        for item in data:
            if isinstance(item, dict):
                domain = item.get("domain")
                if isinstance(domain, str) and domain.strip():
                    domains.append(domain.strip().lower())
        return domains

    async def list_pops(self) -> list[dict[str, Any]]:
        result = await self.call(
            "Email",
            "list_pops",
            {"no_validate": 0, "skip_main": 0},
        )
        data = result.data or []
        if not isinstance(data, list):
            raise CPanelProtocolError(
                "Email::list_pops devolvió un formato inesperado."
            )
        return [item for item in data if isinstance(item, dict)]

    async def add_pop(
        self,
        username: str,
        domain: str,
        password: str,
        quota_mb: int,
    ) -> UAPIResult:
        return await self.call(
            "Email",
            "add_pop",
            {
                "email": username,
                "domain": domain,
                "password": password,
                "quota": quota_mb,
                "send_welcome_email": 0,
            },
        )

    async def list_domains(self) -> dict[str, Any]:
        result = await self.call(
            "DomainInfo",
            "list_domains",
            {"hide_temporary_domains": 1},
        )
        data = result.data or {}
        if not isinstance(data, dict):
            raise CPanelProtocolError(
                "DomainInfo::list_domains devolvió un formato inesperado."
            )
        return data

    async def list_subdomains(self) -> list[dict[str, Any]]:
        result = await self.call("SubDomain", "listsubdomains", {})
        data = result.data or []
        if not isinstance(data, list):
            raise CPanelProtocolError(
                "SubDomain::listsubdomains devolvió un formato inesperado."
            )
        return [item for item in data if isinstance(item, dict)]

    async def add_subdomain(
        self,
        subdomain: str,
        root_domain: str,
        directory: str,
    ) -> UAPIResult:
        return await self.call(
            "SubDomain",
            "addsubdomain",
            {
                "domain": subdomain,
                "rootdomain": root_domain,
                "dir": directory,
                "disallowdot": 1,
            },
        )

    async def list_databases(self) -> list[dict[str, Any]]:
        result = await self.call("Mysql", "list_databases", {})
        data = result.data or []
        if not isinstance(data, list):
            raise CPanelProtocolError(
                "Mysql::list_databases devolvió un formato inesperado."
            )
        return [item for item in data if isinstance(item, dict)]

    async def get_mysql_restrictions(self) -> dict[str, Any]:
        result = await self.call("Mysql", "get_restrictions", {})
        data = result.data or {}
        if not isinstance(data, dict):
            raise CPanelProtocolError(
                "Mysql::get_restrictions devolvió un formato inesperado."
            )
        return data

    async def list_database_users(self) -> list[dict[str, Any]]:
        result = await self.call("Mysql", "list_users", {})
        data = result.data or []
        if not isinstance(data, list):
            raise CPanelProtocolError(
                "Mysql::list_users devolvió un formato inesperado."
            )
        return [item for item in data if isinstance(item, dict)]

    async def create_database_user(self, name: str, password: str) -> UAPIResult:
        return await self.post(
            "Mysql", "create_user", {"name": name, "password": password}
        )

    async def set_database_user_password(
        self, user: str, password: str
    ) -> UAPIResult:
        return await self.post(
            "Mysql", "set_password", {"user": user, "password": password}
        )

    async def set_privileges_on_database(
        self, user: str, database: str, privileges: str
    ) -> UAPIResult:
        return await self.call(
            "Mysql",
            "set_privileges_on_database",
            {"user": user, "database": database, "privileges": privileges},
        )

    async def create_database(self, name: str) -> UAPIResult:
        return await self.call("Mysql", "create_database", {"name": name})

    async def list_files(
        self,
        directory: str,
        *,
        show_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        result = await self.call(
            "Fileman",
            "list_files",
            {
                "dir": directory,
                "show_hidden": 1 if show_hidden else 0,
                "include_mime": 1,
                "include_permissions": 1,
            },
        )
        data = result.data

        # Algunas versiones devuelven una lista plana y otras un objeto con
        # 'dirs' y 'files'. Soportamos ambas formas.
        if isinstance(data, dict):
            entries = [
                *(data.get("dirs") or []),
                *(data.get("files") or []),
            ]
        elif isinstance(data, list):
            entries = data
        else:
            raise CPanelProtocolError(
                "Fileman::list_files devolvió un formato inesperado."
            )

        return [item for item in entries if isinstance(item, dict)]

    async def get_file_information(self, path: str) -> dict[str, Any]:
        result = await self.call(
            "Fileman",
            "get_file_information",
            {
                "path": path,
                "include_mime": 1,
                "include_permissions": 1,
            },
        )
        data = result.data or {}
        if not isinstance(data, dict):
            raise CPanelProtocolError(
                "Fileman::get_file_information devolvió un formato inesperado."
            )
        return data

    async def get_file_content(
        self,
        directory: str,
        filename: str,
    ) -> dict[str, Any]:
        result = await self.call(
            "Fileman",
            "get_file_content",
            {
                "dir": directory,
                "file": filename,
                "to_charset": "UTF-8",
            },
        )
        data = result.data or {}
        if not isinstance(data, dict):
            raise CPanelProtocolError(
                "Fileman::get_file_content devolvió un formato inesperado."
            )
        return data

    async def upload_file(
        self,
        directory: str,
        filename: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> UAPIResult:
        result = await self._request(
            "POST",
            "Fileman",
            "upload_files",
            params={
                "dir": directory,
                "overwrite": 1 if overwrite else 0,
                "permissions": "0644",
            },
            files={
                "file-0": (
                    filename,
                    content,
                    "application/octet-stream",
                )
            },
        )

        data = result.data or {}
        if isinstance(data, dict):
            failed = data.get("failed")
            try:
                failed_count = int(failed) if failed is not None else 0
            except (TypeError, ValueError):
                failed_count = 0
            if failed_count > 0:
                uploads = data.get("uploads") or []
                reasons = [
                    str(item.get("reason"))
                    for item in uploads
                    if isinstance(item, dict) and item.get("reason")
                ]
                detail = " | ".join(reasons) or "sin detalle del servidor"
                raise FileOperationError(
                    f"cPanel no guardó el archivo '{filename}': {detail}"
                )
        return result

    async def trash_file(self, path: str) -> dict[str, Any]:
        result = await self.call(
            "Fileman",
            "trash_file",
            {"path": path},
        )
        data = result.data or {}
        if not isinstance(data, dict):
            raise CPanelProtocolError(
                "Fileman::trash_file devolvió un formato inesperado."
            )
        return data
