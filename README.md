# Huevohost cPanel MCP

Servidor MCP local por **stdio** para ejecutar acciones permitidas sobre una cuenta cPanel con token de UAPI. Está pensado para conectarse desde Claude Desktop u otro cliente MCP.

Incluye:

- Lectura de dominios.
- Lectura de cuentas de correo por dominio.
- Creación de cuentas de correo con dry-run por defecto, contraseña aleatoria de 20 caracteres y verificación posterior.
- Creación de **subdominios** sobre dominios existentes de la cuenta (nunca addon domains ni parked domains), con dry-run por defecto y document root confinado a `/public_html`.
- Operaciones de archivos confinadas a `/public_html`: listar, descargar texto, crear, subir y mover a la papelera.

No incluye cambio de contraseñas, bases de datos, DNS ni SSL. La publicación de landings (Fase 2) está solo diseñada. `delete_file` mueve a la papelera de cPanel, no borra de forma permanente.

## Requisitos

- Python 3.11 o superior.
- Token de API de cPanel con permisos sobre `Email`, `DomainInfo`, `SubDomain` y `Fileman`.
- Rol **Receive Mail** activo en la cuenta si se van a usar las herramientas de correo.
- Rol **File Storage** activo si se van a usar las herramientas de archivos.
- Rol **Web Server** activo si se van a crear subdominios.

## Instalación

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

El paquete `cpanel-api` se instala como dependencia de desarrollo y se usa solo en `scripts/verify_cpanel_api_client.py` para contrastar credenciales. El servidor MCP usa `httpx` directamente.

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---:|---|
| `CPANEL_HOST` | Sí | Hostname o IP. Acepta `cpanel.example.com`, `cpanel.example.com:2083` o `https://cpanel.example.com:2083`. |
| `CPANEL_USER` | Sí | Usuario de la cuenta cPanel. |
| `CPANEL_TOKEN` | Sí | Token de API. Nunca se imprime ni se registra. |
| `CPANEL_PORT` | No | Puerto, por defecto `2083`. Tiene prioridad sobre el puerto incluido en `CPANEL_HOST`. |
| `CPANEL_VERIFY_SSL` | No | `true` por defecto. Usa `false` solo en pruebas con certificado autofirmado. |
| `AUDIT_LOG_PATH` | No | Ruta de auditoría. Por defecto `%LOCALAPPDATA%\Huevohost\cpanel-mcp\audit.jsonl` en Windows. |
| `ALLOWED_ACTIONS_PATH` | No | Ruta de `allowed_actions.json`. Por defecto el archivo del proyecto. |
| `MCP_FILE_MAX_BYTES` | No | Tamaño máximo por archivo para crear, subir o descargar. Por defecto `1048576` (1 MiB); mínimo `1024`, máximo `10485760`. |
| `CPANEL_MCP_LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL`; por defecto `WARNING`. Los logs van a stderr. |

Configura las credenciales en las variables de usuario del sistema, no en el repositorio. `.env` está ignorado por Git y el servidor no lo carga automáticamente.

## Acciones permitidas

`allowed_actions.json` controla qué herramientas existen para el modelo. Una acción con `enabled=false` no se registra en MCP y no puede invocarse.

```json
{
  "version": 1,
  "actions": [
    { "name": "list_domains", "level": "lectura", "enabled": true },
    { "name": "list_email_accounts", "level": "lectura", "enabled": true },
    { "name": "create_email_account", "level": "escritura_reversible", "enabled": true },
    { "name": "create_subdomain", "level": "escritura_reversible", "enabled": true },
    { "name": "list_files", "level": "lectura", "enabled": true },
    { "name": "download_file", "level": "lectura", "enabled": true },
    { "name": "create_file", "level": "escritura_reversible", "enabled": true },
    { "name": "upload_file", "level": "escritura_reversible", "enabled": true },
    { "name": "delete_file", "level": "alto_impacto", "enabled": true },
    { "name": "publish_landing_page", "level": "alto_impacto", "enabled": false }
  ]
}
```

Si se habilita una acción de alto impacto que aún no está implementada, el servidor falla al arrancar con un error en stderr.

## Herramientas

### `list_domains()`

Devuelve el dominio principal, addon domains, parked domains, subdominios y una lista normalizada con el tipo de cada dominio.

### `list_email_accounts(domain)`

Comprueba que el dominio sea un dominio de correo de la cuenta y lista sus cuentas con el estado de suspensión.

### `create_email_account(user, domain, quota_mb, execute=false)`

- `execute=false`: no contacta cPanel, no genera contraseña y devuelve un resumen del cambio propuesto.
- `execute=true`: comprueba el dominio, comprueba que la cuenta no exista, crea la cuenta con una contraseña aleatoria de 20 caracteres, la verifica en `list_pops` y muestra la contraseña **una sola vez**.

Validaciones: usuario de 1 a 64 caracteres, dominio FQDN en minúsculas, cuota entera entre 1 y 5120 MB.

### `create_subdomain(subdomain, domain, directory=null, execute=false)`

Crea **solo subdominios** sobre un dominio que ya pertenece a la cuenta (principal, addon o parked). No crea addon domains, parked domains ni cambios de DNS.

- `subdomain`: solo la etiqueta, por ejemplo `blog` para `blog.lowcode.cl`. No admite puntos, rutas ni nombres reservados (`www`, `mail`, `cpanel`, `webmail`, `cgi-bin`, etc.).
- `domain`: dominio padre existente en la cuenta.
- `directory`: document root opcional. Se normaliza y se confina a `/public_html`; si se omite, se usa `public_html/<subdominio>`. No puede existir ya (para no adoptar el contenido de un sitio existente) ni atravesar enlaces simbólicos.
- `execute=false`: no contacta cPanel y devuelve el plan (FQDN y document root).
- `execute=true`: comprueba el dominio padre y que el subdominio no exista, crea el subdominio, lo verifica en `SubDomain::listsubdomains` y devuelve `created`/`verified`.

Ejemplo:

```json
{ "subdomain": "blog", "domain": "lowcode.cl", "directory": null, "execute": true }
```

Al terminar, sube el contenido con `upload_file` o `create_file` dentro de `public_html/blog`. El servidor no expone el borrado de subdominios: si te equivocas, elimínalo manualmente desde cPanel.

### Operaciones de archivos

Todas las herramientas de archivos están confinadas a `public_html`. Aceptan:

- `public_html/ait.lowcode.cl/index.html`
- `/public_html/ait.lowcode.cl/index.html`
- `ait.lowcode.cl/index.html` (relativa a `/public_html`)

Se rechazan rutas absolutas fuera de `/public_html`, componentes `.` o `..`, caracteres de control, nombres con `,` o `;` y cualquier componente que sea un enlace simbólico. Las escrituras comprueban cada directorio existente antes de tocar el archivo.

### `list_files(path="public_html", show_hidden=false)`

Lista archivos y directorios. Devuelve nombre, ruta relativa, tipo, tamaño, fecha de modificación, MIME y permisos. Los enlaces simbólicos se muestran marcados con `symlink: true`, pero no se siguen.

### `download_file(path)`

Descarga el contenido de un archivo de texto (HTML, CSS, JS, JSON, TXT, XML, PHP fuente, etc.). Rechaza binarios, archivos que superen `MCP_FILE_MAX_BYTES` y nombres sensibles como `.env`, `.htaccess`, `wp-config.php`, `.htpasswd`, claves privadas y rutas dentro de `.git`, `.svn`, `.hg` o `.ssh`. El contenido no se escribe en la auditoría.

### `create_file(path, content="", overwrite=false)`

Crea un archivo de texto. Si ya existe y `overwrite=false`, falla sin modificar nada. Si `overwrite=true`, primero mueve el original a la papelera de cPanel y después escribe el nuevo contenido. Bloquea extensiones ejecutables o de configuración (`.php`, `.phtml`, `.cgi`, `.pl`, `.py`, `.sh`, `.htaccess`, `.user.ini`, `php.ini`, etc.) y tampoco escribe dentro de `.git`, `.svn`, `.hg` o `.ssh`.

### `upload_file(path, content=null, content_base64=null, overwrite=false)`

Sube un archivo usando exactamente uno de estos campos:

- `content`: texto plano, codificado en UTF-8.
- `content_base64`: contenido binario codificado en base64 (imágenes, fuentes, PDFs, etc.).

Aplica el mismo límite de tamaño, la misma política de extensiones bloqueadas y el mismo respaldo a papelera que `create_file`.

### `delete_file(path, confirm)`

Mueve un archivo o directorio a la papelera de cPanel, donde puede restaurarse desde **Archivos → Papelera**. Exige que `confirm` repita la ruta exacta; por ejemplo:

```json
{ "path": "public_html/ait.lowcode.cl/viejo.html", "confirm": "/public_html/ait.lowcode.cl/viejo.html" }
```

No permite borrar `/public_html` completo ni seguir enlaces simbólicos. El borrado es recuperable, pero se trata como acción de alto impacto: revisa la ruta antes de confirmar.

## Uso con Claude Desktop

Edita `claude_desktop_config.json` y añade el servidor. Ajusta las rutas absolutas a tu instalación:

```json
{
  "mcpServers": {
    "huevohost-cpanel": {
      "command": "C:\\ruta\\a\\cpanel-api-open\\.venv\\Scripts\\python.exe",
      "args": ["-m", "cpanel_mcp"],
      "env": {
        "CPANEL_HOST": "cpanel.example.com:2083",
        "CPANEL_USER": "usuario",
        "CPANEL_TOKEN": "TU_TOKEN",
        "CPANEL_VERIFY_SSL": "true",
        "MCP_FILE_MAX_BYTES": "1048576",
        "ALLOWED_ACTIONS_PATH": "C:\\ruta\\a\\cpanel-api-open\\allowed_actions.json",
        "AUDIT_LOG_PATH": "C:\\Users\\TU_USUARIO\\AppData\\Local\\Huevohost\\cpanel-mcp\\audit.jsonl"
      }
    }
  }
}
```

Si Claude Desktop ya hereda las variables de usuario, puedes omitir el bloque `env` de las credenciales. Si las incluyes ahí, ese archivo pasa a contener una copia del token: protégelo y no lo subas a ningún repositorio.

Reinicia Claude Desktop por completo después de cambiar la configuración. En Windows, cierra también el proceso de la bandeja del sistema.

## Pruebas locales

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe scripts\mcp_smoke.py
```

`mcp_smoke.py` lista las herramientas, consulta dominios y cuentas, y hace un dry-run. Para una creación real:

```powershell
.venv\Scripts\python.exe scripts\mcp_smoke.py --domain example.com --user mcp-prueba --quota 500 --execute --confirm CREAR
```

La contraseña se imprime en la terminal una sola vez. No se guarda en logs ni auditoría. Bórrala o rota la cuenta manualmente en cPanel cuando termines la prueba.

Prueba de humo de archivos (crea, descarga, verifica y mueve a la papelera un archivo temporal):

```powershell
.venv\Scripts\python.exe scripts\mcp_files_smoke.py --dir public_html/ait.lowcode.cl --confirm
```

Prueba de humo de subdominios (primero dry-run; con `--confirm CREAR` crea un subdominio temporal y lo elimina al terminar):

```powershell
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain lowcode.cl
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain lowcode.cl --confirm CREAR
```

Comprobación del cliente `cpanel-api` y de que el token no está escrito en el proyecto:

```powershell
.venv\Scripts\python.exe scripts\verify_cpanel_api_client.py
.venv\Scripts\python.exe scripts\check_no_secrets.py
```

La checklist manual completa está en `docs/MANUAL_TEST_CHECKLIST.md`.

## Seguridad

- Timeout de 30 segundos en todas las peticiones.
- TLS verificado por defecto.
- Errores claros para token inválido, permisos, rol de correo o de archivos desactivado, timeout, cuenta existente, archivo existente y respuestas inválidas.
- Auditoría append-only en JSONL, con argumentos saneados. Nunca incluye contraseñas, tokens ni el contenido de los archivos.
- stdout reservado para MCP; cualquier log o error de arranque va a stderr.
- `.gitignore` excluye `.venv`, `.env`, `audit.jsonl`, logs y cachés.
- Las rutas de archivos se confinan a `/public_html`, no se siguen enlaces simbólicos y no se permite sobrescribir sin respaldo en la papelera.
- `create_subdomain` solo acepta dominios padre de la cuenta, rechaza etiquetas reservadas y document roots fuera de `/public_html`, existentes o que atraviesen enlaces simbólicos. No crea addon ni parked domains y no expone el borrado de subdominios.
- `delete_file` es una acción de alto impacto, pero mueve a la papelera y exige confirmar la ruta exacta. Está habilitada por petición explícita del usuario.

### Nota sobre la contraseña

La contraseña se muestra una vez en la respuesta de la herramienta y queda en el historial de la conversación del cliente MCP. El servidor no la registra, pero el cliente puede guardar el historial. Si se filtra, cámbiala manualmente en cPanel; el cambio de contraseña no está expuesto por este servidor.

### Nota sobre timeout en la creación

Si `add_pop` agota los 30 segundos después de enviar la petición, cPanel podría haber creado la cuenta aunque el servidor no reciba la confirmación. En ese caso la contraseña generada se pierde. Revisa el listado en cPanel y restablece la contraseña manualmente si la cuenta existe.

## Fase 2 (diseño, no implementado)

Publicar una landing estática en un subdominio existente:

1. `stage_landing_page` sube los archivos a una vista previa aleatoria dentro del docroot, sin tocar la raíz pública.
2. `promote_landing_page` exige confirmación por subdominio y hash, respalda el docroot actual y promueve la vista previa.
3. `rollback_landing_page` restaura un respaldo concreto.

Reglas previstas: allowlist de directorios de origen, solo archivos estáticos, sin PHP/CGI/symlinks, límites de tamaño, subdominio ya existente, respaldo verificado, confirmación explícita y auditoría de cada paso. Las acciones de alto impacto seguirán desactivadas por defecto.
