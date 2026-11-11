#!/usr/bin/env python
"""Prueba de humo de la herramienta create_subdomain.

Por defecto solo hace un dry-run y no contacta cPanel. Con --confirm CREAR
crea un subdominio temporal, lo verifica y lo elimina al terminar. La
eliminación usa la UAPI directamente porque MCP no expone borrado de
subdominios; el document root se mueve a la papelera de cPanel.

El nombre de prueba empieza por ``mcp-smoke-`` y se usa sobre el dominio
indicado en --domain.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from cpanel_mcp.config import load_settings  # noqa: E402
from cpanel_mcp.cpanel import CPanelClient  # noqa: E402
from cpanel_mcp.errors import RemoteFileNotFoundError  # noqa: E402


def extract_result(result) -> object:
    if result.structuredContent:
        payload = result.structuredContent
        return payload.get("result", payload)
    texts = [
        getattr(block, "text", "")
        for block in result.content
        if getattr(block, "text", None)
    ]
    return "\n".join(texts)


async def _exists(client: CPanelClient, path: str) -> bool:
    try:
        info = await client.get_file_information(path)
    except RemoteFileNotFoundError:
        return False
    return info.get("exists") not in {0, "0", False}


async def cleanup(
    client: CPanelClient,
    *,
    label: str,
    domain: str,
    fqdn: str,
    docroot: str,
) -> int:
    failures = 0

    subdomains = await client.list_subdomains()
    still_there = any(
        isinstance(item.get("domain"), str)
        and item["domain"].strip().lower() == fqdn
        for item in subdomains
    )
    if still_there:
        try:
            await client.call(
                "SubDomain",
                "delsubdomain",
                {"domain": f"{label}_{domain}"},
            )
            print(f"Subdominio eliminado: {fqdn}")
        except Exception as exc:  # noqa: BLE001 - se informa y se continúa
            print(f"ERROR al eliminar {fqdn}: {type(exc).__name__}: {exc}")
            failures += 1

    relative = docroot.lstrip("/")
    if await _exists(client, relative):
        try:
            await client.trash_file(relative)
            print(f"Document root movido a la papelera: /{relative}")
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR al mover /{relative} a la papelera: {exc}")
            failures += 1

    remaining = await client.list_subdomains()
    if any(
        isinstance(item.get("domain"), str)
        and item["domain"].strip().lower() == fqdn
        for item in remaining
    ):
        print(f"ADVERTENCIA: {fqdn} sigue apareciendo en cPanel.", file=sys.stderr)
        failures += 1
    else:
        print(f"Verificación: {fqdn} ya no existe.")
    return failures


async def run(args: argparse.Namespace) -> int:
    root = ROOT
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")

    stamp = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y%m%d-%H%M%S")
    label = args.label or f"mcp-smoke-{stamp}"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cpanel_mcp"],
        env=env,
        cwd=str(root),
    )

    created_data: dict[str, object] | None = None
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=45),
        ) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if "create_subdomain" not in names:
                print(
                    "La herramienta create_subdomain no está habilitada. "
                    "Revisa allowed_actions.json.",
                    file=sys.stderr,
                )
                return 1

            dry_run = await session.call_tool(
                "create_subdomain",
                {
                    "subdomain": label,
                    "domain": args.domain,
                    "directory": args.directory,
                    "execute": False,
                },
            )
            if dry_run.isError:
                print("ERROR en el dry-run:", extract_result(dry_run))
                return 1
            print("Dry-run de create_subdomain:")
            print(
                json.dumps(
                    extract_result(dry_run), indent=2, ensure_ascii=False
                )
            )

            if not args.confirm:
                print(
                    "\nSin --confirm CREAR no se creó nada.",
                    file=sys.stderr,
                )
                return 0
            if args.confirm != "CREAR":
                print("Para ejecutar de verdad usa --confirm CREAR.", file=sys.stderr)
                return 2

            created = await session.call_tool(
                "create_subdomain",
                {
                    "subdomain": label,
                    "domain": args.domain,
                    "directory": args.directory,
                    "execute": True,
                },
            )
            if created.isError:
                print("ERROR al crear el subdominio:", extract_result(created))
                return 1
            payload = extract_result(created)
            if not isinstance(payload, dict):
                print("Respuesta inesperada de create_subdomain.", file=sys.stderr)
                return 1
            created_data = payload
            print("\ncreate_subdomain:")
            print(json.dumps(payload, indent=2, ensure_ascii=False))

    if created_data is None:
        return 1

    fqdn = str(created_data.get("fqdn") or f"{label}.{args.domain}")
    docroot = str(created_data.get("document_root") or f"/public_html/{label}")

    client = CPanelClient(load_settings())
    try:
        failures = await cleanup(
            client,
            label=label,
            domain=args.domain,
            fqdn=fqdn,
            docroot=docroot,
        )
    finally:
        await client.aclose()

    if not created_data.get("verified"):
        print(
            "ADVERTENCIA: la creación no se verificó de inmediato. "
            "Revisa cPanel.",
            file=sys.stderr,
        )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domain",
        required=True,
        help="Dominio padre existente en la cuenta, por ejemplo lowcode.cl",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Etiqueta del subdominio; por defecto mcp-smoke-<fecha>",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Document root dentro de public_html; por defecto public_html/<label>",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Escribe CREAR para crear y luego limpiar el subdominio de prueba",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
