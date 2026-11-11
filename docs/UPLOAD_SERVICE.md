# Subida de binarios por ticket

`upload_file` con `content_base64` obliga al modelo a escribir los bytes en su respuesta; por encima de ~5-10 KB el base64 llega corrupto. La solución es que el modelo **no vea los bytes**:

1. Claude llama a `create_upload_ticket(path, ...)` (herramienta MCP, stdio local).
2. El MCP pide el ticket al **servicio de subida** (`POST /admin/tickets`, con secreto de administración).
3. Claude ejecuta en el sandbox `curl -T archivo "<upload_url>"`. Los bytes viajan de sandbox a servicio por HTTPS, sin pasar por el modelo.
4. El servicio valida y escribe el archivo en `/public_html` de la cuenta.

```
Claude Desktop ──stdio──▶ cpanel_mcp ──HTTPS + Bearer──▶ upload.acopios.cl /admin/tickets
sandbox claude.ai ───────────curl -T────────────────────▶ upload.acopios.cl /upload/<token>
                                                                  │ (corre como el usuario de la cuenta)
                                                                  ▼
                                              /home/<cuenta>/public_html/<ruta>   (+ papelera vía UAPI)
```

## Componentes

| Archivo | Función |
|---|---|
| `src/cpanel_mcp/uploads/service.py` | App WSGI (solo biblioteca estándar). Rutas `/admin/tickets` y `/upload/<token>`. |
| `src/cpanel_mcp/uploads/tickets.py` | Tickets: 256 bits CSPRNG, solo se guarda el sha256, 5 min, un solo uso, SQLite. |
| `src/cpanel_mcp/uploads/validate.py` | Allowlist de extensiones, dobles extensiones, magic bytes en Python puro. |
| `src/cpanel_mcp/uploads/storage.py` | Rutas sin symlinks, temporal en la misma partición, publicación atómica sin pisar. |
| `src/cpanel_mcp/uploads/ratelimit.py` | Ventana deslizante por cuenta e IP (SQLite). |
| `src/cpanel_mcp/uploads/client.py` | Herramienta MCP `create_upload_ticket`. |
| `deploy/passenger_wsgi.py` | Entrada para Passenger. |

## Despliegue en cPanel (Setup Python App)

No toques `/public_html/mcp` ni el vhost de `mcp.acopios.cl`: el servicio vive en un subdominio propio.

1. **Subdominio**: crea `upload.acopios.cl` (cPanel → Subdominios, document root `public_html/upload`, o usa `create_subdomain`). Passenger sirve la app desde aquí; el docroot solo contendrá lo que cPanel genere.
2. **Subir el código**: copia `src/` y `deploy/passenger_wsgi.py` a `~/upload-app/` (fuera de `public_html`, para que nadie descargue el código ni la base de datos).
3. **Setup Python App** → *Create Application*: la versión de Python más reciente que ofrezca cPanel (3.11+ recomendado; el servicio solo se ha probado en 3.14, así que valida la versión de tu hosting en la prueba de extremo a extremo), *Application root* `upload-app`, *Application URL* `upload.acopios.cl`, *Application startup file* `passenger_wsgi.py`, *entry point* `application`.
4. En esa pantalla instala la dependencia: `pip install httpx` (el servicio solo la usa para mover a la papelera con `overwrite=true`).
5. **Variables de entorno** (en la misma pantalla):

   | Variable | Obligatoria | Descripción |
   |---|:-:|---|
   | `UPLOAD_ADMIN_SECRET` | Sí | Secreto compartido con el MCP, ≥ 32 caracteres aleatorios. |
   | `UPLOAD_PUBLIC_BASE_URL` | Sí | `https://upload.acopios.cl` |
   | `CPANEL_USER` / `CPANEL_TOKEN` | Sí | Cuenta y token UAPI (solo para `Fileman::trash_file`). Con varias cuentas: `UPLOAD_ACCOUNTS=a=/home/a,b=/home/b` y `CPANEL_TOKEN_A`, `CPANEL_TOKEN_B`. |
   | `CPANEL_HOST` | No | Hostname de cPanel (por defecto `localhost`; usa el hostname del certificado, p. ej. `acopios.cl`). |
   | `UPLOAD_MAX_BYTES_CAP` | No | Tope global por archivo. Por defecto 5 MiB; máximo 25 MiB. |
   | `UPLOAD_TICKET_TTL` | No | Vigencia en segundos (30–900, por defecto 300). |
   | `UPLOAD_ALLOW_ZIP` | No | `true` para permitir `.zip` (desactivado por defecto). |
   | `UPLOAD_DB_PATH` / `UPLOAD_AUDIT_PATH` | No | Por defecto `~/.huevohost-upload/`. |

6. **Reiniciar** la app y comprobar `https://upload.acopios.cl/` → 404 JSON (`not_found`); es la señal de que arrancó.
7. **En el MCP local** (variables de usuario o bloque `env` de Claude Desktop): `UPLOAD_SERVICE_URL=https://upload.acopios.cl` y el mismo `UPLOAD_ADMIN_SECRET`.

Genera el secreto con: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

> Si Apache/Passenger no entrega la IP real en `REMOTE_ADDR`, el rate limit por IP se aplicaría al proxy. En cPanel estándar sí llega la IP del cliente.

## Contrato HTTP

`POST /admin/tickets` — `Authorization: Bearer <UPLOAD_ADMIN_SECRET>`, JSON `{account, path, max_bytes?, overwrite?, expected_sha256?}` → `201 {upload_url, method:"PUT", expires_at, path, max_bytes, overwrite}`.

`PUT /upload/<token>` — cuerpo = bytes del archivo (`curl -T`). También `POST` con cuerpo crudo o con `multipart/form-data` con **una sola** parte (el nombre de archivo del formulario se ignora: el destino lo fija el ticket) → `201 {path, bytes, sha256, mime}`.

| Código | Causa |
|---|---|
| 400 | Ruta insegura, cuerpo vacío, multipart con varios archivos |
| 401 | Credencial de administración incorrecta |
| 403 | HTTP sin TLS, ticket vencido o ya usado, symlink, cuenta no gestionada |
| 404 | Ticket inexistente, directorio destino inexistente |
| 409 | El destino existe y `overwrite=false` |
| 413 | Superó `max_bytes` (se corta mientras se lee, sin confiar en `Content-Length`) |
| 415 | Extensión no permitida / doble extensión / el contenido real no coincide con la extensión |
| 422 | `expected_sha256` no coincide (no se guarda nada) |
| 429 | Rate limit (por cuenta e IP; intentos con token inválido: 10 / 10 min por IP) |

Garantías de escritura: el cuerpo se guarda en `~/.mcp-upload-tmp/` (misma partición, modo 0700), se valida entero (tamaño, magic bytes, sha256) y solo entonces se publica con `link()` (atómico, no pisa) con permisos `0644`. Con `overwrite=true`, el archivo anterior se mueve a la papelera de cPanel **después** de validar el nuevo y justo antes de publicar; si la papelera falla, no se escribe nada. Un ticket se consume al presentarse: si la subida falla por 413/415/422 hay que pedir uno nuevo.

No hay cabeceras CORS. SVG está rechazado (puede contener scripts). El servicio rechaza HTTP plano.

## Auditoría

`UPLOAD_AUDIT_PATH` (JSONL): cuenta, ruta, bytes, sha256, IP, resultado y código de error. Nunca contiene tokens, secretos ni contenido.

## Prueba de extremo a extremo

Tras desplegar y configurar el MCP:

1. En Claude: «Crea un ticket para `criffoh/foto.jpg`». Devuelve `upload_url` y `curl_example`.
2. Desde el sandbox, con un JPEG real de ~20 KB: `curl -sS -T foto.jpg "<upload_url>"`.
3. La respuesta debe traer `bytes`, `mime: "image/jpeg"` y el `sha256`; compáralo con `sha256sum foto.jpg`.
4. `curl -sI https://criffoh.acopios.cl/foto.jpg` → `200` y `content-type: image/jpeg`.
5. Repite el mismo `curl` → `403 ticket_used`. Un ticket de más de 5 minutos → `403 ticket_expired`. Ninguno escribe nada.

## Fase 2 (diseñada, no implementada)

**`source_url`**: el servidor descarga una URL pública en lugar de recibir el cuerpo. Requisitos:

- Solo `https://`, puertos 443 (y 80 → rechazar), sin credenciales en la URL.
- Anti-SSRF: resolver el DNS **una vez**, rechazar IPs privadas, loopback, link-local, CGNAT, multicast y metadatos (`169.254.169.254`, IPv6 equivalentes), y conectar a la IP ya resuelta (DNS fijado) enviando `Host`/SNI originales. Revalidar en cada redirección.
- Máximo 3 redirecciones, tope de tamaño aplicado en streaming, timeouts de conexión y de lectura totales, `Content-Type` ignorado a favor de magic bytes.
- Pasa por el mismo pipeline de validación que `PUT` (allowlist, magic bytes, sha256, overwrite/papelera). Habilitable con `UPLOAD_ALLOW_SOURCE_URL` y una allowlist opcional de dominios.

**`local_path`** (MCP local por stdio con Claude Desktop): una herramienta que lee un archivo del equipo del usuario y lo sube por UAPI `Fileman::upload_files` en streaming, sin base64 en el modelo. Requisitos: allowlist de directorios locales configurada por el usuario (`MCP_LOCAL_UPLOAD_DIRS`), rechazo de symlinks y de rutas fuera de la allowlist, mismas extensiones y magic bytes, y confirmación explícita del usuario por archivo.
