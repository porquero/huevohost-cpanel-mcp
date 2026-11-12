"""Construcción del servidor MCP y registro filtrado de herramientas."""

from __future__ import annotations

import functools
import inspect
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .actions_config import ActionSpec, load_actions
from .audit import AuditLogger
from .config import Settings
from .cpanel import CPanelClient
from .errors import ConfigError
from .file_ops import FileOperations
from .logging_setup import redact_text
from .operations import Operations

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]

INSTRUCTIONS = (
    "Servidor local de Huevohost para acciones permitidas sobre una cuenta cPanel. "
    "Solo están disponibles las herramientas habilitadas en allowed_actions.json. "
    "Las lecturas son seguras. create_email_account es una simulación salvo que "
    "execute=true; en ese caso crea una cuenta y muestra su contraseña una sola vez. "
    "create_subdomain también es una simulación salvo execute=true; solo crea "
    "subdominios sobre dominios existentes de la cuenta (nunca addon ni parked "
    "domains) y su document root debe quedar dentro de /public_html. "
    "Las herramientas de archivos trabajan únicamente dentro de /public_html. "
    "No siguen enlaces simbólicos, no aceptan rutas con '..' y no permiten escribir "
    "archivos ejecutables como .php, .cgi o .htaccess. Para subir binarios (fotos, "
    "logos, PDFs, fuentes) usa upload_local_file con el nombre del archivo dentro "
    "de la carpeta 'archivos' del equipo del usuario (list_local_files "
    "muestra su ruta y contenido). Los adjuntos del chat no son visibles "
    "para este servidor: pide al usuario que copie el archivo a esa "
    "carpeta, o cópialo tú si tienes herramientas de archivos locales. "
    "No pases base64 largo por upload_file. delete_file mueve a la "
    "papelera de cPanel y exige confirmar la ruta exacta. No existen herramientas "
    "de cambio de contraseñas, usuarios o permisos de bases de datos, borrado de "
    "bases de datos, DNS ni SSL. create_database también es una simulación salvo "
    "execute=true y solo crea una base de datos MySQL vacía (sin usuarios)."
)


def _implemented_handlers(
    operations: Operations,
    file_operations: FileOperations,
) -> dict[str, ToolHandler]:
    return {
        "list_domains": operations.list_domains,
        "list_email_accounts": operations.list_email_accounts,
        "create_email_account": operations.create_email_account,
        "create_subdomain": operations.create_subdomain,
        "list_databases": operations.list_databases,
        "create_database": operations.create_database,
        "list_files": file_operations.list_files,
        "download_file": file_operations.download_file,
        "create_file": file_operations.create_file,
        "upload_file": file_operations.upload_file,
        "list_local_files": file_operations.list_local_files,
        "upload_local_file": file_operations.upload_local_file,
        "delete_file": file_operations.delete_file,
    }


def _wrap_tool(
    spec: ActionSpec,
    handler: ToolHandler,
    audit: AuditLogger,
    secrets: tuple[str, ...],
) -> ToolHandler:
    """Envuelve una acción con auditoría y mensajes de error saneados."""

    @functools.wraps(handler)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await handler(*args, **kwargs)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            code = getattr(exc, "code", "internal_error")
            message = redact_text(str(exc), secrets)
            audit.log(
                action=spec.name,
                args=kwargs,
                outcome="error",
                error_code=str(code),
                duration_ms=duration_ms,
                message=message,
            )
            logger.warning("%s falló (%s): %s", spec.name, code, message)
            if isinstance(exc, ToolError):
                raise
            raise ToolError(message) from None

        duration_ms = int((time.perf_counter() - started) * 1000)
        dry_run = result.get("dry_run") if isinstance(result, dict) else None
        verified = result.get("verified") if isinstance(result, dict) else None
        audit.log(
            action=spec.name,
            args=kwargs,
            outcome="success",
            duration_ms=duration_ms,
            dry_run=dry_run if isinstance(dry_run, bool) else None,
            verified=verified if isinstance(verified, bool) else None,
        )
        return result

    wrapper.__signature__ = inspect.signature(handler)  # type: ignore[attr-defined]
    return wrapper


def build_server(
    settings: Settings,
    *,
    client: CPanelClient | None = None,
    audit: AuditLogger | None = None,
    actions_path: Path | None = None,
) -> FastMCP:
    """Crea el servidor registrando únicamente acciones enabled=true."""

    specs = load_actions(actions_path or settings.allowed_actions_path)
    audit = audit or AuditLogger(settings.audit_path, secrets=(settings.token,))
    cpanel_client = client or CPanelClient(settings)
    operations = Operations(cpanel_client)
    file_operations = FileOperations(cpanel_client, settings)
    handlers = _implemented_handlers(operations, file_operations)

    mcp = FastMCP(
        name="huevohost-cpanel",
        instructions=INSTRUCTIONS,
    )

    enabled_names = {spec.name for spec in specs if spec.enabled}
    implemented_names = set(handlers)
    missing = enabled_names - implemented_names
    if missing:
        raise ConfigError(
            "allowed_actions.json habilita acciones no implementadas en esta fase: "
            f"{', '.join(sorted(missing))}. Deshabilítalas para arrancar."
        )

    for spec in specs:
        if not spec.enabled:
            continue
        handler = handlers[spec.name]
        mcp.add_tool(
            _wrap_tool(spec, handler, audit, (settings.token,)),
            name=spec.name,
            description=spec.description or None,
        )

    logger.info(
        "Herramientas MCP habilitadas: %s",
        ", ".join(sorted(enabled_names)) or "(ninguna)",
    )
    return mcp
