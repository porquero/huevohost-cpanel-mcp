# Instalar WordPress desde el MCP (sin WP Toolkit)

El MCP deja **todo preparado** (subdominio, base de datos, usuario y archivos de WordPress) y el usuario final **termina la instalación en el navegador**. No usa WP Toolkit ni Softaculous.

> **Por qué no WP Toolkit:** su única función de usuario en la UAPI, `WordPressSite::create`, no admite elegir el dominio: sin parámetros instala WordPress en el dominio principal de la cuenta. No debe usarse. Ver `CLAUDE.md`.

## Pasos (los sigue Claude)

1. **Subdominio** (si hace falta uno nuevo): `create_subdomain(subdomain, domain, execute=true)`. El directorio por defecto es `public_html/<subdominio>`.
2. **Base de datos**: `create_database(name, execute=true)`. Anota el nombre completo que devuelve (con prefijo de cuenta).
3. **Usuario de la base**: `create_database_user(user, database, privileges="all", execute=true)`. Devuelve la contraseña **una sola vez**.
4. **Archivos de WordPress**: `prepare_wordpress(directory="<subdominio>", execute=true)`. Descarga el paquete oficial de wordpress.org en el equipo del usuario, verifica su SHA-1, lo sube a cPanel, lo extrae y envía el zip a la papelera. El directorio debe estar vacío.
5. **Entrega al usuario**, en ese momento y una sola vez:
   - La URL del sitio (`site_url`).
   - Datos de la base: servidor `localhost`, nombre de la base, usuario y contraseña.
   - Los `next_steps` de la respuesta.

## Lo que hace el usuario en el navegador

1. Abrir la URL del sitio. En un subdominio recién creado el certificado HTTPS puede tardar unos minutos (AutoSSL); si el navegador avisa de un certificado no válido, esperar y reintentar.
2. Elegir el idioma en el asistente de WordPress.
3. Introducir los datos de la base: nombre, usuario, contraseña, servidor `localhost` y dejar el prefijo `wp_`.
4. Indicar el título del sitio y crear el usuario administrador con **una contraseña propia**.

WordPress crea `wp-config.php` por sí mismo en ese asistente; el MCP nunca escribe PHP propio.

## Garantías y límites

- **Directorio vacío.** `prepare_wordpress` rechaza cualquier directorio con contenido antes de descargar nada: nunca mezcla ni sobrescribe un sitio. Solo tolera, y conserva sin cambios, la configuración que el hosting crea en cada subdominio nuevo (`.htaccess`, `.user.ini`, `php.ini`, `cgi-bin`, `.well-known`); un paquete que intentara incluir esos archivos se descarta. Se comprobó en un subdominio real: un docroot recién creado ya trae `.htaccess`, `.user.ini` y `php.ini`.
- **Paquete verificado.** URL fija de wordpress.org, SHA-1 oficial comprobado y zip validado (rutas, enlaces simbólicos, tamaños). Si algo falla, no se sube nada.
- **Sin borrado.** Si la extracción falla a medias, el directorio puede quedar con archivos parciales: hay que vaciarlo desde cPanel antes de reintentar (el MCP no borra directorios).
- **Después de instalar**, las actualizaciones y la seguridad del sitio son responsabilidad del usuario (WordPress las ofrece desde `/wp-admin`). Conviene decírselo al entregar los datos.
- **Si se pierde la contraseña de la base**, `change_database_user_password` genera una nueva; hay que actualizar `DB_PASSWORD` en `wp-config.php` (el MCP no edita archivos PHP).

## Qué hace `prepare_wordpress` por dentro

1. Valida el directorio (existe, no es un enlace simbólico, no es `public_html`, está vacío).
2. Descarga `latest.zip.sha1` y `latest.zip` desde `wordpress.org` (redirecciones solo HTTPS dentro de `wordpress.org`), con un tope de tamaño.
3. Comprueba el SHA-1 y valida el zip; lo reempaqueta en memoria **sin** la carpeta `wordpress/`.
4. Sube el zip como un solo archivo (`Fileman::upload_files`, 300 s de timeout).
5. Obtiene el directorio personal (`Variables::get_user_information`) y extrae con la API 2 (`Fileman::fileop`, `op=extract`), con destino **absoluto** (cPanel rechaza rutas relativas).
6. Mueve el zip a la papelera (también si la extracción falló) y verifica que existan `wp-admin`, `wp-content`, `wp-includes`, `index.php`, `wp-load.php` y `wp-config-sample.php`.
7. Calcula `site_url` a partir del subdominio que apunta a ese directorio.

Verificado contra una cuenta cPanel real: la subida de un zip, la extracción síncrona con destino absoluto (que conserva las subcarpetas) y el envío a la papelera.
