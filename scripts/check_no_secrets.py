#!/usr/bin/env python
"""Comprueba que el token de cPanel no esté escrito en archivos del proyecto."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
EXCLUDED_FILES = {"audit.jsonl"}


def main() -> int:
    token = os.environ.get("CPANEL_TOKEN", "").strip()
    if not token:
        print(
            "CPANEL_TOKEN no está definido; no se puede comprobar.",
            file=sys.stderr,
        )
        return 2

    checked = 0
    matches: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDED_FILES or path.suffix in {".pyc", ".pyo"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        checked += 1
        if token in content:
            matches.append(path)

    if matches:
        print("El token aparece en estos archivos:", file=sys.stderr)
        for path in matches:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
        return 1

    print(f"OK: token no encontrado en {checked} archivos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
