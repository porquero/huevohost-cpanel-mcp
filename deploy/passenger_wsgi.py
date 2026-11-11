"""Entrada de Passenger para "Setup Python App" de cPanel.

Colócalo en la raíz de la aplicación (junto a la carpeta ``src``). Passenger
busca el objeto ``application``. Toda la configuración sale de variables de
entorno definidas en la propia pantalla de la app Python (ver
docs/UPLOAD_SERVICE.md); nunca se guardan secretos en este archivo.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from cpanel_mcp.uploads.service import create_app  # noqa: E402

application = create_app()
