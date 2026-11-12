#!/usr/bin/env python
"""Comprueba la forma real de la UAPI de MySQL de tu cuenta, sin crear nada.

Solo hace LECTURAS (``get_restrictions``, ``list_users``, ``list_databases``).
No llama a ninguna función de escritura: ni siquiera «sin parámetros», porque
algunas funciones de la UAPI se ejecutan con valores por defecto (p. ej.
``WordPressSite::create``). Los parámetros de las funciones de escritura se
consultan en la documentación oficial y se prueban con la checklist manual.

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
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
