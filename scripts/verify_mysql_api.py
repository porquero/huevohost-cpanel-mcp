#!/usr/bin/env python
"""Comprueba la forma real de la UAPI de MySQL de tu cuenta, sin crear nada.

Solo hace lecturas (``get_restrictions``, ``list_users``, ``list_databases``) y
llama a las funciones de escritura SIN parámetros para que cPanel responda con
un error de validación que revela los nombres de parámetro obligatorios. Ninguna
de esas llamadas puede modificar la cuenta.

Lee CPANEL_HOST, CPANEL_USER y CPANEL_TOKEN del entorno y nunca los imprime.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cpanel_mcp.config import load_settings  # noqa: E402
from cpanel_mcp.cpanel import CPanelClient  # noqa: E402
from cpanel_mcp.logging_setup import redact_text  # noqa: E402

READS = ["get_restrictions", "list_users", "list_databases"]
# Sin parámetros: cPanel las rechaza por validación y no ejecuta nada.
PARAM_DISCOVERY = [
    "create_user",
    "set_password",
    "set_privileges_on_database",
    "get_privileges_on_database",
]


def shape(value: object, depth: int = 0) -> object:
    """Resume la estructura: tipos y claves, con un ejemplo de cada lista."""

    if isinstance(value, dict):
        return {key: shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__ if depth else value


async def main() -> int:
    settings = load_settings()
    secrets = (settings.token,)
    async with CPanelClient(settings) as client:
        for function in READS:
            print(f"\n=== Mysql::{function} (lectura) ===")
            try:
                result = await client.call("Mysql", function, {})
            except Exception as exc:  # noqa: BLE001
                print("ERROR:", redact_text(str(exc), secrets))
                continue
            print("estructura:", json.dumps(shape(result.data), ensure_ascii=False, indent=2))
            if function == "get_restrictions":
                print("valores:", json.dumps(result.data, ensure_ascii=False))

        for function in PARAM_DISCOVERY:
            print(f"\n=== Mysql::{function} (sin parámetros) ===")
            try:
                await client.call("Mysql", function, {})
                print("AVISO: respondió OK sin parámetros; revisa la documentación.")
            except Exception as exc:  # noqa: BLE001
                print("respuesta:", redact_text(str(exc), secrets))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
