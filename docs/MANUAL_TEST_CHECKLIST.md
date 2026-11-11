# Checklist manual contra una cuenta cPanel de pruebas

No escribas el token en la terminal, en un archivo del proyecto ni en la conversación. Las comprobaciones usan `$env:CPANEL_TOKEN` sin imprimirlo.

## Preparación

- [ ] Confirmar que `CPANEL_HOST`, `CPANEL_USER` y `CPANEL_TOKEN` están definidas en las variables de usuario.
- [ ] Abrir una PowerShell nueva para que herede las variables, o cargarlas desde el Registro sin mostrarlas.
- [ ] Confirmar que el rol **Receive Mail** está activo si se van a probar correos.
- [ ] Confirmar que el rol **File Storage** está activo si se van a probar archivos.
- [ ] Confirmar que el rol **Web Server** está activo si se van a probar subdominios.
- [ ] Tener a mano un dominio de correo, un dominio padre para subdominios y un subdirectorio de `/public_html` de la cuenta de pruebas.
- [ ] Ejecutar la suite simulada:

```powershell
.venv\Scripts\python.exe -m pytest
```

Resultado esperado: todas las pruebas pasan sin contactar cPanel.

## 1. Arranque y catálogo de herramientas

```powershell
.venv\Scripts\python.exe scripts\mcp_smoke.py --domain TU_DOMINIO_DE_PRUEBA
```

- [ ] El servidor arranca y lista las herramientas de correo, subdominios y archivos.
- [ ] Aparecen `create_subdomain`, `list_files`, `download_file`, `create_file`, `upload_file` y `delete_file`.
- [ ] No aparece `publish_landing_page`.
- [ ] `list_domains` devuelve el dominio principal y los dominios de la cuenta.
- [ ] `list_email_accounts` devuelve las cuentas del dominio con su estado.
- [ ] El dry-run de correo muestra `dry_run: true`, `created: false` y no incluye contraseña.
- [ ] El dry-run no crea ninguna cuenta nueva: comparar el listado antes y después.

## 2. Creación real de correo y verificación

Usar un nombre único, por ejemplo `mcp-prueba-20261111`, y una cuota de 500 MB:

```powershell
.venv\Scripts\python.exe scripts\mcp_smoke.py --domain TU_DOMINIO_DE_PRUEBA --user mcp-prueba-20261111 --quota 500 --execute --confirm CREAR
```

- [ ] La respuesta muestra `created: true` y `verified: true`.
- [ ] La contraseña tiene 20 caracteres y se muestra una sola vez.
- [ ] La cuenta aparece en `list_email_accounts`.
- [ ] La cuenta aparece en cPanel → Correo → Cuentas de correo.
- [ ] Repetir el mismo comando sin borrar la cuenta devuelve el error «ya existe» y no modifica nada.
- [ ] Borrar la cuenta de prueba manualmente en cPanel al terminar.

## 3. Acción deshabilitada

- [ ] Hacer una copia de `allowed_actions.json`.
- [ ] Cambiar `create_email_account` a `"enabled": false`.
- [ ] Reiniciar el servidor o volver a ejecutar `mcp_smoke.py`.
- [ ] Confirmar que la herramienta ya no aparece en el listado.
- [ ] Confirmar que el dry-run falla con «Unknown tool» o un error equivalente.
- [ ] Restaurar `allowed_actions.json`.

## 4. Subdominios

Primero solo simulación:

```powershell
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain TU_DOMINIO_PADRE
```

- [ ] El dry-run devuelve `dry_run: true`, `created: false` y el FQDN esperado.
- [ ] El document root propuesto es `/public_html/<etiqueta>`.
- [ ] Sin `--confirm CREAR` no se contacta cPanel ni se modifica nada.

Después, creación y limpieza automática de un subdominio temporal:

```powershell
.venv\Scripts\python.exe scripts\mcp_subdomain_smoke.py --domain TU_DOMINIO_PADRE --confirm CREAR
```

- [ ] `create_subdomain` devuelve `created: true` y `verified: true`.
- [ ] El subdominio aparece en cPanel → Dominios → Subdominios.
- [ ] El script elimina el subdominio y mueve su document root a la papelera al terminar.
- [ ] El subdominio ya no aparece en `list_domains`.

Comprobaciones adicionales:

- [ ] Usar un dominio ajeno devuelve «no pertenece a esta cuenta».
- [ ] Usar una etiqueta existente devuelve «ya existe» sin modificar nada.
- [ ] Usar `www`, `mail` o `cpanel` como etiqueta se rechaza antes de contactar cPanel.
- [ ] Un `directory` con `..`, fuera de `/public_html`, la raíz `public_html` o dentro de `.git`/`.ssh`/`cgi-bin` se rechaza.
- [ ] Un `directory` que ya existe se rechaza sin tocar el contenido existente.
- [ ] La herramienta no permite crear addon domains ni parked domains.

## 5. Archivos en /public_html

Primero solo lectura:

```powershell
.venv\Scripts\python.exe scripts\mcp_files_smoke.py --dir public_html/TU_SUBDIRECTORIO
```

- [ ] `list_files` devuelve archivos y directorios con tipo, tamaño y MIME.
- [ ] Los enlaces simbólicos, si existen, aparecen con `symlink: true`.
- [ ] Sin `--confirm` no se crea, modifica ni borra nada.

Después, escritura controlada con un archivo temporal:

```powershell
.venv\Scripts\python.exe scripts\mcp_files_smoke.py --dir public_html/TU_SUBDIRECTORIO --confirm
```

- [ ] `create_file` devuelve `created: true`, `verified: true` y un tamaño correcto.
- [ ] `download_file` devuelve exactamente el contenido creado.
- [ ] `delete_file` devuelve `trashed: true` y `verified: true`.
- [ ] El archivo ya no aparece en `list_files`.
- [ ] El archivo aparece en cPanel → Archivos → Papelera y puede restaurarse.
- [ ] No se ha tocado ningún archivo existente del sitio.

Comprobaciones adicionales:

- [ ] Crear con `overwrite=false` sobre un archivo existente devuelve el error «ya existe» y no lo modifica.
- [ ] Crear con `overwrite=true` mueve el original a la papelera antes de escribir.
- [ ] `create_file` rechaza `shell.php` y `.htaccess`.
- [ ] `upload_file` acepta texto con `content` y binario con `content_base64` (solo archivos pequeños, ~5 KB).
- [ ] `list_local_files` muestra la carpeta `archivos/` y los archivos que dejaste en ella.
- [ ] `upload_local_file` sube un JPEG real de ~20 KB copiado a `archivos/` (pasando solo el nombre): devuelve `verified: true`, el `sha256` coincide con `Get-FileHash` y `https://TU_SUBDOMINIO/foto.jpg` responde 200 con `content-type: image/jpeg`.
- [ ] `upload_local_file` rechaza una ruta del sandbox del chat (`/mnt/user-data/uploads/...`) con un mensaje claro, un archivo fuera de `MCP_LOCAL_UPLOAD_DIRS`, un `.svg`, un destino `foto.php.jpg` y un `.jpg` cuyo contenido es un PNG.
- [ ] `upload_local_file` con `overwrite=false` sobre un destino existente devuelve «ya existe»; con `overwrite=true` el original aparece en la papelera.
- [ ] `download_file` rechaza `.png` y `.env`.
- [ ] Una ruta con `..` o `/etc/passwd` se rechaza antes de contactar cPanel.
- [ ] `delete_file` con `confirm` distinto de la ruta se rechaza sin mover nada.
- [ ] Borrar `public_html` completo se rechaza.

## 6. Errores claros

- [ ] Con un token incorrecto, `list_domains` devuelve un error de token inválido (HTTP 401), no un traceback.
- [ ] Con un dominio ajeno a la cuenta, `create_email_account` devuelve «no es un dominio de correo de esta cuenta».
- [ ] Con cuota `0` o `5121`, la validación falla antes de contactar cPanel.
- [ ] Con un usuario inválido como `cpanel` o `juan..perez`, la validación falla.
- [ ] Si el rol de correo está desactivado, el error lo indica explícitamente.
- [ ] Si el rol de archivos está desactivado, el error lo indica explícitamente.
- [ ] Si el rol Web Server está desactivado, `create_subdomain` devuelve un error claro y no un traceback.

## 7. Auditoría y secretos

```powershell
Get-Content "$env:LOCALAPPDATA\Huevohost\cpanel-mcp\audit.jsonl" -Tail 10
Select-String -Path "$env:LOCALAPPDATA\Huevohost\cpanel-mcp\audit.jsonl" -Pattern $env:CPANEL_TOKEN
.venv\Scripts\python.exe scripts\check_no_secrets.py
```

- [ ] `audit.jsonl` contiene `ts`, `action`, `args`, `outcome` y `verified`/`dry_run` cuando aplica.
- [ ] El token no aparece en `audit.jsonl`.
- [ ] La contraseña generada no aparece en `audit.jsonl`.
- [ ] El contenido de los archivos creados o subidos no aparece en `audit.jsonl`; `content` y `content_base64` figuran como `[REDACTED]`.
- [ ] `check_no_secrets.py` termina con `OK`.
- [ ] Revisar los logs de la consola: no contienen token, contraseña ni contenido de archivos.

## 8. Claude Desktop

- [ ] Añadir el servidor a `claude_desktop_config.json` con la ruta absoluta del Python del `.venv`.
- [ ] Reiniciar Claude Desktop por completo.
- [ ] Confirmar que aparecen las nueve herramientas y que una consulta de dominios funciona.
- [ ] Confirmar que una petición de creación de correo sin `execute=true` no modifica cPanel.
- [ ] Confirmar que una petición de `create_subdomain` sin `execute=true` no modifica cPanel.
- [ ] Confirmar que `list_files` funciona sobre `/public_html`.
- [ ] Confirmar que `delete_file` pide confirmación de la ruta exacta.

## 9. Cierre

- [ ] Borrar la cuenta de prueba creada.
- [ ] Confirmar que no quedan subdominios de prueba (`mcp-smoke-*`) ni sus directorios.
- [ ] Vaciar la papelera de cPanel si no se necesita conservar el archivo de prueba.
- [ ] Restaurar `allowed_actions.json` si se modificó.
- [ ] No dejar tokens en archivos temporales, capturas ni historiales del proyecto.
