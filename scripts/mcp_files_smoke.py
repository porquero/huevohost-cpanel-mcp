#!/usr/bin/env python
"""Prueba de humo de las herramientas de archivos.

Crea un archivo temporal, lo descarga, comprueba su contenido y lo mueve a la
papelera de cPanel. Sin --confirm solo lista el directorio y no escribe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

FILE_TOOLS = {"list_files", "download_file", "create_file", "upload_file", "delete_file"}


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

    directory = args.dir.strip().strip("/")
    if directory.startswith("public_html/"):
        api_dir = directory
    else:
        api_dir = f"public_html/{directory}"

    stamp = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y%m%d-%H%M%S")
    filename = f"mcp-smoke-{stamp}.txt"
    api_path = f"{api_dir}/{filename}"
    content = f"Prueba MCP {stamp}\n"

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
            read_timeout_seconds=timedelta(seconds=60),
        ) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            missing = FILE_TOOLS - tools
            if missing:
                print(
                    "Faltan herramientas de archivos: "
                    + ", ".join(sorted(missing))
                    + ". Revisa allowed_actions.json.",
                    file=sys.stderr,
                )
                return 1
            print("Herramientas de archivos disponibles: " + ", ".join(sorted(FILE_TOOLS)))

            listing = await session.call_tool("list_files", {"path": api_dir})
            if listing.isError:
                print("ERROR en list_files:", extract_result(listing))
                return 1
            listing_data = extract_result(listing)
            print(f"\nListado de /{api_dir}:")
            print(json.dumps(listing_data, indent=2, ensure_ascii=False)[:2000])

            if not args.confirm:
                print(
                    "\nSin --confirm no se escribe nada. Para crear, descargar y "
                    "mover a la papelera un archivo temporal, añade --confirm.",
                    file=sys.stderr,
                )
                return 0

            created = await session.call_tool(
                "create_file",
                {
                    "path": api_path,
                    "content": content,
                    "overwrite": False,
                },
            )
            if created.isError:
                print("ERROR en create_file:", extract_result(created))
                return 1
            print("\ncreate_file:")
            print(json.dumps(extract_result(created), indent=2, ensure_ascii=False))

            downloaded = await session.call_tool(
                "download_file",
                {"path": api_path},
            )
            if downloaded.isError:
                print("ERROR en download_file:", extract_result(downloaded))
                return 1
            downloaded_data = extract_result(downloaded)
            print("\ndownload_file:")
            print(json.dumps(downloaded_data, indent=2, ensure_ascii=False))
            if not isinstance(downloaded_data, dict):
                print("Respuesta inesperada de download_file.", file=sys.stderr)
                return 1
            if downloaded_data.get("content") != content:
                print("ERROR: el contenido descargado no coincide.", file=sys.stderr)
                return 1
            print("Contenido verificado: coincide con lo creado.")

            if args.keep:
                print(
                    f"\nSe conserva /{api_path} por --keep. Bórralo manualmente "
                    "o vuelve a ejecutar sin --keep.",
                    file=sys.stderr,
                )
                return 0

            deleted = await session.call_tool(
                "delete_file",
                {"path": api_path, "confirm": "/" + api_path},
            )
            if deleted.isError:
                print("ERROR en delete_file:", extract_result(deleted))
                return 1
            print("\ndelete_file (movido a la papelera):")
            print(json.dumps(extract_result(deleted), indent=2, ensure_ascii=False))

            after = await session.call_tool(
                "list_files",
                {"path": api_dir, "show_hidden": True},
            )
            if after.isError:
                print("ERROR al verificar el borrado:", extract_result(after))
                return 1
            after_data = extract_result(after)
            names = []
            if isinstance(after_data, dict):
                names = [
                    entry.get("name")
                    for entry in after_data.get("entries", [])
                    if isinstance(entry, dict)
                ]
            if filename in names:
                print(
                    "\nADVERTENCIA: el archivo sigue apareciendo en el listado "
                    "después del borrado. Revisa la papelera y cPanel.",
                    file=sys.stderr,
                )
            else:
                print("\nVerificación: el archivo ya no aparece en su ruta original.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default="public_html/sitio.example.com",
        help="Directorio dentro de public_html (por defecto sitio.example.com)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Crea, descarga y mueve a la papelera un archivo temporal",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="No mover el archivo a la papelera al terminar",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
