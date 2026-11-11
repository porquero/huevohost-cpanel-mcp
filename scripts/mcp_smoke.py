#!/usr/bin/env python
"""Cliente MCP de humo contra el servidor local.

Por defecto solo hace lecturas y un dry-run. Para crear una cuenta real hay que
pasar --execute --confirm CREAR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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


async def run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cpanel_mcp"],
        env=env,
        cwd=str(root),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=45),
        ) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("Herramientas disponibles:")
            for name in names:
                print(f"  - {name}")

            domains = await session.call_tool("list_domains", {})
            if domains.isError:
                print("ERROR en list_domains:", extract_result(domains))
                return 1
            domains_data = extract_result(domains)
            print("\nDominios:")
            print(json.dumps(domains_data, indent=2, ensure_ascii=False))

            domain = args.domain
            if not domain and isinstance(domains_data, dict):
                domain = domains_data.get("main_domain")
            if not domain:
                print("No se pudo determinar un dominio; usa --domain.", file=sys.stderr)
                return 1

            accounts = await session.call_tool(
                "list_email_accounts",
                {"domain": domain},
            )
            if accounts.isError:
                print("ERROR en list_email_accounts:", extract_result(accounts))
                return 1
            print(f"\nCuentas de correo en {domain}:")
            print(json.dumps(extract_result(accounts), indent=2, ensure_ascii=False))

            dry_run = await session.call_tool(
                "create_email_account",
                {
                    "user": args.user,
                    "domain": domain,
                    "quota_mb": args.quota,
                    "execute": False,
                },
            )
            if dry_run.isError:
                print("ERROR en dry-run:", extract_result(dry_run))
                return 1
            print("\nDry-run de create_email_account:")
            print(json.dumps(extract_result(dry_run), indent=2, ensure_ascii=False))

            if args.execute:
                if args.confirm != "CREAR":
                    print(
                        "Para ejecutar de verdad usa --confirm CREAR.",
                        file=sys.stderr,
                    )
                    return 2
                created = await session.call_tool(
                    "create_email_account",
                    {
                        "user": args.user,
                        "domain": domain,
                        "quota_mb": args.quota,
                        "execute": True,
                    },
                )
                if created.isError:
                    print("ERROR al crear:", extract_result(created))
                    return 1
                print("\nCuenta creada (la contraseña se muestra una sola vez):")
                print(json.dumps(extract_result(created), indent=2, ensure_ascii=False))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="Dominio de correo a consultar")
    parser.add_argument("--user", default="mcp-test", help="Usuario de prueba")
    parser.add_argument("--quota", type=int, default=500, help="Cuota en MB")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Crea la cuenta de verdad (requiere --confirm CREAR)",
    )
    parser.add_argument("--confirm", default="", help="Escribe CREAR para confirmar")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
