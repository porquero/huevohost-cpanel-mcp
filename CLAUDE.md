# CLAUDE.md — Reglas del proyecto

## Objetivo

Servidor MCP local (stdio) que expone acciones permitidas sobre una cuenta cPanel de Huevohost: listar dominios, listar y crear cuentas de correo, crear subdominios sobre dominios existentes, listar y crear bases de datos MySQL, crear usuarios MySQL con permisos sobre una base y cambiar su contraseña, preparar los archivos de WordPress en un directorio vacío, y listar, descargar, crear, subir (texto, base64 pequeño o binarios desde el disco local) y mover a la papelera archivos dentro de `/public_html`. Fase 2 está solo diseñada: publicar landings en subdominios.

## Seguridad obligatoria

- Las credenciales se leen **solo** de `CPANEL_HOST`, `CPANEL_USER` y `CPANEL_TOKEN`.
- Nunca escribas tokens, contraseñas ni cabeceras `Authorization` en el repositorio, logs, auditoría, mensajes de error, tests o respuestas.
- Los logs de depuración van **solo a stderr**. `stdout` está reservado para el protocolo JSON-RPC de MCP. No uses `print()` hacia stdout en el servidor; los errores de arranque se escriben directamente en `sys.stderr`.
- `audit.jsonl` registra fecha, acción, argumentos saneados y resultado. Nunca incluye contraseñas, tokens ni el contenido de los archivos (`content`, `content_base64`, etc. se redactan).
- No expongas herramientas de cambio de contraseñas de correo o de cPanel, borrado de bases de datos o usuarios, DNS ni SSL. Las herramientas de bases de datos y usuarios MySQL existen por petición explícita del usuario. `delete_file` existe por petición explícita y solo mueve a la papelera de cPanel.
- `allowed_actions.json` es la fuente de verdad. Solo se registran las acciones con `enabled=true`. Si se habilita una acción no implementada, el servidor debe fallar cerrado.
- Las acciones de nivel `alto_impacto` se entregan desactivadas por defecto, salvo `delete_file` (limitada a la papelera) y `change_database_user_password` y `prepare_wordpress`, habilitadas explícitamente por el usuario.
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

## Reglas de bases de datos

- Herramientas existentes: `list_databases`, `create_database`, `list_database_users`, `create_database_user` y `change_database_user_password`. No añadas borrado de bases o usuarios, `GRANT` libre, permisos globales o con `GRANT OPTION`, acceso remoto (`Mysql::add_host`), importación/exportación ni acceso a los datos: esos pasos se hacen desde cPanel.
- Las acciones que escriben exigen `execute=true`; el dry-run no hace peticiones HTTP ni genera contraseña.
- Los nombres van sin prefijo (`[a-z][a-z0-9_]*`; usuarios máx. 32 contando el prefijo). El prefijo y los largos máximos salen de `Mysql::get_restrictions` (`prefix`, `max_username_length`, `max_database_name_length`); nunca se asumen ni se aceptan del modelo, y el prefijo se valida.
- Antes de crear se comprueba con `Mysql::list_databases` / `Mysql::list_users`; lo existente nunca se modifica y las bases y usuarios nuevos se verifican con esos mismos listados.
- Los permisos son siempre por base y de tres niveles fijos (`all`, `read_write`, `read_only`); el modelo no pasa listas de privilegios.
- Las contraseñas las genera el servidor (24 caracteres alfanuméricos); nunca se aceptan del modelo ni del usuario. Se envían por **POST en el cuerpo** (nunca en la URL), se muestran **una sola vez** en la respuesta y no aparecen en auditoría, logs ni mensajes de error. Si falla el permiso tras crear el usuario, se devuelve un resultado parcial con la contraseña y un `warning` (no un error).
- `change_database_user_password` es de nivel `alto_impacto`: exige `execute=true` y `confirm` igual al usuario indicado, solo actúa sobre usuarios MySQL existentes y avisa de que los sitios que usen la contraseña anterior dejarán de conectar.
- Los nombres de campo de la UAPI de MySQL se verificaron con `scripts/verify_mysql_api.py` (solo lectura). El formato de `privileges` de `set_privileges_on_database` solo se comprueba creando un usuario de prueba real (ver la checklist).

## Reglas de WordPress

- `prepare_wordpress` es la **única** herramienta que deja archivos PHP en la cuenta, y solo los del paquete oficial de wordpress.org. Nunca escribas PHP con contenido del modelo; las demás herramientas siguen bloqueando `.php`.
- La URL de descarga es fija (`https://wordpress.org/latest.zip`); el modelo no puede indicar otra. Se verifica el SHA-1 publicado por wordpress.org, solo se siguen redirecciones HTTPS dentro de `wordpress.org` y se descarta el paquete si algo no coincide.
- El zip se valida antes de subirlo (rutas fuera de `wordpress/`, `..`, rutas absolutas, enlaces simbólicos, entradas cifradas, límites de archivos y de tamaño expandido) y se reempaqueta en memoria sin la carpeta `wordpress/`.
- El directorio debe existir, no ser `public_html`, no ser un enlace simbólico y estar **vacío** (solo se toleran `cgi-bin` y `.well-known`): nunca se mezcla ni se sobrescribe un sitio existente. Esa comprobación ocurre antes de descargar nada.
- No se crea `wp-config.php` ni se ejecuta el instalador: el usuario completa la instalación en el navegador con los datos de la base que entrega `create_database_user`.
- El zip temporal siempre se envía a la papelera de cPanel, también si la extracción falla.
- `destfiles` de `Fileman::fileop` (API 2, `op=extract`) exige una ruta **absoluta** dentro del directorio personal, que se obtiene con `Variables::get_user_information` (`name=home`).
- **No uses `WordPressSite::create` ni WP Toolkit por API**: no admite elegir el dominio y, sin parámetros, instala WordPress en el dominio principal de la cuenta (ya ocurrió una vez). Detalles en la regla de pruebas sobre sondeos.

## Reglas de archivos

- Toda ruta se normaliza y se confina a `public_html`. Se aceptan `public_html/x`, `/public_html/x` y rutas relativas como `sitio/x.html`.
- Rechaza rutas absolutas fuera de `public_html`, componentes `.`/`..`, caracteres de control, nombres con `,` o `;` y cualquier componente que sea un enlace simbólico.
- No sigas enlaces simbólicos en ninguna operación, ni siquiera para listar o borrar.
- Antes de escribir, comprueba cada componente existente y el destino. Si el destino existe y `overwrite=true`, muévelo primero a la papelera con `Fileman::trash_file`.
- `create_file` y `upload_file` bloquean extensiones ejecutables o de configuración (`.php`, `.phtml`, `.phar`, `.cgi`, `.pl`, `.py`, `.sh`, `.htaccess`, `.user.ini`, `php.ini`, etc.) y no escriben dentro de `.git`, `.svn`, `.hg` ni `.ssh`.
- `download_file` solo devuelve texto y bloquea nombres sensibles (`.env`, `.htaccess`, `wp-config.php`, `.htpasswd`, claves privadas) y directorios de control como `.git`, `.svn`, `.hg` o `.ssh`. No registres el contenido descargado.
- `delete_file` exige que `confirm` coincida exactamente con la ruta normalizada. No permitas borrar `public_html` completo.
- `upload_file` con `content_base64` se reserva para archivos pequeños (~5 KB); los binarios grandes se suben con `upload_local_file`. No añadas servicios, procesos ni archivos en la cuenta cPanel del usuario: el producto es para usuarios finales y todo corre en el MCP local.
- `upload_local_file` lee solo de `MCP_LOCAL_UPLOAD_DIRS`; la primera carpeta es la bandeja (por defecto `archivos/` junto al proyecto) y `local_path` puede ser un nombre relativo a ella. Rechaza `..`, symlinks, no-regulares y rutas de otros entornos (p. ej. `/mnt/user-data/...` del sandbox del chat), permite solo la allowlist de `binary_types.py` y verifica magic bytes contra la extensión de destino. No registres los bytes ni devuelvas la ruta local en `upload_local_file`. `list_local_files` es solo lectura y devuelve únicamente la ruta de la bandeja y sus archivos; no listes otras carpetas.
- El contenido de `archivos/` es del usuario: no lo versiones (solo `.gitkeep`) ni lo muevas, borres o modifiques desde el servidor. Los adjuntos del chat no son accesibles para el MCP; no intentes recibirlos como base64 para esquivar el límite.
- Respeta `MCP_FILE_MAX_BYTES` (1 MiB por defecto) en crear, subir y descargar. No lo desactives para acomodar una prueba.

## Arquitectura

- `src/cpanel_mcp/config.py`: validación de entorno y rutas.
- `src/cpanel_mcp/cpanel.py`: cliente UAPI sobre `httpx`, timeout de 30 s y mapeo de errores. Única excepción al timeout: subir y extraer el paquete de WordPress (300 s, solo esas dos llamadas de `prepare_wordpress`).
- `src/cpanel_mcp/operations.py`: lógica de dominios, correo y subdominios.
- `src/cpanel_mcp/file_ops.py`: normalización de rutas, comprobación de symlinks y operaciones de archivos.
- `src/cpanel_mcp/binary_types.py`: allowlist de binarios y verificación por magic bytes.
- `src/cpanel_mcp/local_files.py`: resolución y lectura segura de archivos locales.
- `src/cpanel_mcp/wordpress.py`: descarga verificada, validación y reempaquetado del zip de WordPress y su extracción en el servidor.
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
.venv\Scripts\python.exe scripts\mcp_files_smoke.py --dir public_html/sitio.example.com --confirm
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain example.com --confirm CREAR
.venv\Scripts\python.exe scripts\check_no_secrets.py
```

## Pruebas

- Las pruebas unitarias usan `httpx.MockTransport`; no deben llamar a cPanel.
- Toda nueva acción debe tener pruebas de: validación, dry-run, ejecución simulada, auditoría sin secretos y registro condicionado por `enabled`.
- **Nunca llames a funciones de escritura de la UAPI «sin parámetros» para descubrir su firma.** Algunas se ejecutan con valores por defecto: `WordPressSite::create` encoló una instalación de WordPress real en la cuenta. Para conocer una función de escritura usa la documentación oficial o una cuenta de pruebas; contra la cuenta real solo se hacen lecturas.
- No debilites validaciones, timeout, TLS ni el filtro de secretos para hacer pasar una prueba.
- La verificación contra la cuenta real se hace con `scripts/mcp_smoke.py` y la checklist manual.

## Fase 2

No implementar `publish_landing_page` sin aprobación explícita. El diseño exige respaldo previo, vista previa, confirmación por hash, allowlist de origen estático, bloqueo de PHP/CGI/symlinks y auditoría de cada paso.
