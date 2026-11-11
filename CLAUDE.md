# CLAUDE.md — Reglas del proyecto

## Objetivo

Servidor MCP local (stdio) que expone acciones permitidas sobre una cuenta cPanel de Huevohost: listar dominios, listar y crear cuentas de correo, crear subdominios sobre dominios existentes, y listar, descargar, crear, subir y mover a la papelera archivos dentro de `/public_html`. Fase 2 está solo diseñada: publicar landings en subdominios.

## Seguridad obligatoria

- Las credenciales se leen **solo** de `CPANEL_HOST`, `CPANEL_USER` y `CPANEL_TOKEN`.
- Nunca escribas tokens, contraseñas ni cabeceras `Authorization` en el repositorio, logs, auditoría, mensajes de error, tests o respuestas.
- Los logs de depuración van **solo a stderr**. `stdout` está reservado para el protocolo JSON-RPC de MCP. No uses `print()` hacia stdout en el servidor; los errores de arranque se escriben directamente en `sys.stderr`.
- `audit.jsonl` registra fecha, acción, argumentos saneados y resultado. Nunca incluye contraseñas, tokens ni el contenido de los archivos (`content`, `content_base64`, etc. se redactan).
- No expongas herramientas de cambio de contraseñas, bases de datos, DNS ni SSL. `delete_file` existe por petición explícita y solo mueve a la papelera de cPanel.
- `allowed_actions.json` es la fuente de verdad. Solo se registran las acciones con `enabled=true`. Si se habilita una acción no implementada, el servidor debe fallar cerrado.
- Las acciones de nivel `alto_impacto` se entregan desactivadas por defecto, salvo `delete_file`, habilitada explícitamente por el usuario y limitada a la papelera.
- La creación real de correo exige `execute=true`. El dry-run no debe hacer peticiones HTTP ni generar contraseña.
- La contraseña generada se muestra una sola vez en la respuesta de creación y no se vuelve a registrar.

## Reglas de subdominios

- `create_subdomain` crea **solo subdominios**. No implementes addon domains ni parked domains desde esta acción.
- El dominio padre debe aparecer en `main_domain`, `addon_domains` o `parked_domains` de `DomainInfo::list_domains`; nunca aceptes un dominio ajeno.
- `subdomain` es solo una etiqueta: sin puntos, rutas, esquema ni nombres reservados (`www`, `mail`, `webmail`, `cpanel`, `whm`, `cgi-bin`, etc.).
- El `directory` se normaliza y se confina a `/public_html`; por defecto `public_html/<subdominio>`. Rechaza la raíz `public_html`, rutas fuera de ella y componentes `.git`, `.svn`, `.hg`, `.ssh` o `cgi-bin`.
- El document root no puede existir antes de crear el subdominio: evita adoptar o sobrescribir el contenido de un sitio existente.
- Comprueba cada componente con `Fileman::get_file_information` y rechaza enlaces simbólicos antes de llamar a `SubDomain::addsubdomain`.
- La creación real exige `execute=true` y se verifica con `SubDomain::listsubdomains`. No expongas borrado de subdominios: se elimina manualmente desde cPanel.

## Reglas de archivos

- Toda ruta se normaliza y se confina a `public_html`. Se aceptan `public_html/x`, `/public_html/x` y rutas relativas como `sitio/x.html`.
- Rechaza rutas absolutas fuera de `public_html`, componentes `.`/`..`, caracteres de control, nombres con `,` o `;` y cualquier componente que sea un enlace simbólico.
- No sigas enlaces simbólicos en ninguna operación, ni siquiera para listar o borrar.
- Antes de escribir, comprueba cada componente existente y el destino. Si el destino existe y `overwrite=true`, muévelo primero a la papelera con `Fileman::trash_file`.
- `create_file` y `upload_file` bloquean extensiones ejecutables o de configuración (`.php`, `.phtml`, `.phar`, `.cgi`, `.pl`, `.py`, `.sh`, `.htaccess`, `.user.ini`, `php.ini`, etc.) y no escriben dentro de `.git`, `.svn`, `.hg` ni `.ssh`.
- `download_file` solo devuelve texto y bloquea nombres sensibles (`.env`, `.htaccess`, `wp-config.php`, `.htpasswd`, claves privadas) y directorios de control como `.git`, `.svn`, `.hg` o `.ssh`. No registres el contenido descargado.
- `delete_file` exige que `confirm` coincida exactamente con la ruta normalizada. No permitas borrar `public_html` completo.
- Respeta `MCP_FILE_MAX_BYTES` (1 MiB por defecto) en crear, subir y descargar. No lo desactives para acomodar una prueba.

## Arquitectura

- `src/cpanel_mcp/config.py`: validación de entorno y rutas.
- `src/cpanel_mcp/cpanel.py`: cliente UAPI sobre `httpx`, timeout de 30 s y mapeo de errores.
- `src/cpanel_mcp/operations.py`: lógica de dominios, correo y subdominios.
- `src/cpanel_mcp/file_ops.py`: normalización de rutas, comprobación de symlinks y operaciones de archivos.
- `src/cpanel_mcp/server.py`: FastMCP y registro filtrado de herramientas.
- `src/cpanel_mcp/audit.py`: auditoría JSONL.
- `src/cpanel_mcp/validators.py`: validaciones estrictas.
- `src/cpanel_mcp/logging_setup.py`: stderr y redacción de secretos.

## Comandos

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\mcp_smoke.py
.venv\Scripts\python.exe scripts\mcp_files_smoke.py --dir public_html/ait.lowcode.cl --confirm
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain lowcode.cl --confirm CREAR
.venv\Scripts\python.exe scripts\check_no_secrets.py
```

## Pruebas

- Las pruebas unitarias usan `httpx.MockTransport`; no deben llamar a cPanel.
- Toda nueva acción debe tener pruebas de: validación, dry-run, ejecución simulada, auditoría sin secretos y registro condicionado por `enabled`.
- No debilites validaciones, timeout, TLS ni el filtro de secretos para hacer pasar una prueba.
- La verificación contra la cuenta real se hace con `scripts/mcp_smoke.py` y la checklist manual.

## Fase 2

No implementar `publish_landing_page` sin aprobación explícita. El diseño exige respaldo previo, vista previa, confirmación por hash, allowlist de origen estático, bloqueo de PHP/CGI/symlinks y auditoría de cada paso.
