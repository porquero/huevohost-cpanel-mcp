#!/usr/bin/env python
"""Verificación opcional con el cliente oficial cpanel-api.

No forma parte de la ruta crítica del servidor MCP. Sirve para comprobar que
las mismas credenciales y endpoints responden con el cliente cpanel-api.
"""

from __future__ import annotations

import logging
import sys

from cpanel_api import CPanelApi

from cpanel_mcp.config import load_settings

logging.getLogger("cpanel_api").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    try:
        settings = load_settings()
        client = CPanelApi(
            settings.host,
            settings.user,
            settings.token,
            port=settings.port,
            auth_type="utoken",
            timeout=30,
            verify=settings.verify_ssl,
        )

        domains = client.uapi.DomainInfo.list_domains(hide_temporary_domains=1)
        data = domains.data or {}
        main_domain = data.get("main_domain") if isinstance(data, dict) else None
        print(f"cpanel-api OK. Dominio principal: {main_domain}")

        pops = client.uapi.Email.list_pops()
        accounts = pops.data or []
        print(f"cpanel-api OK. Cuentas de correo visibles: {len(accounts)}")
        return 0
    except Exception as exc:
        print(f"No se pudo verificar cpanel-api: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
