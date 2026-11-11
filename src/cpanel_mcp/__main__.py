"""Punto de entrada: servidor MCP por stdio."""

from __future__ import annotations

import logging
import os
import sys

from .config import load_settings
from .logging_setup import configure_logging, redact_text
from .server import build_server

logger = logging.getLogger("cpanel_mcp")


def main() -> None:
    try:
        settings = load_settings()
        configure_logging((settings.token,), settings.log_level)
        server = build_server(settings)
    except Exception as exc:
        # stdout queda reservado para MCP; cualquier error de arranque va a stderr.
        token = os.environ.get("CPANEL_TOKEN", "")
        sys.stderr.write(
            f"huevohost-cpanel-mcp: {redact_text(str(exc), (token,))}\n"
        )
        raise SystemExit(1) from None

    logger.info("Servidor MCP iniciado por stdio.")
    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Servidor MCP detenido.")


if __name__ == "__main__":
    main()
