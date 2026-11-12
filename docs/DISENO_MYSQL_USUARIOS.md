# Diseño: usuarios y permisos de bases de datos MySQL

**Estado: implementado, con cambios respecto a este borrador.** Se implementaron `list_database_users`, `create_database_user` (nivel por defecto `all`) y `change_database_user_password` (alto_impacto, con `confirm`), por petición del usuario para cubrir la pérdida de contraseñas. **No** se implementó `grant_database_access`. Los nombres de campo de la UAPI se verificaron con `scripts/verify_mysql_api.py` (`prefix`, `max_username_length`, `max_database_name_length`; `list_users` devuelve `user`, `shortuser`, `databases`; `create_user` pide `name` y `password`; `set_password` pide `user` y `password`). Las contraseñas viajan por POST en el cuerpo. Queda sin verificar solo el formato de `privileges` en `set_privileges_on_database`, que se comprueba con la checklist manual. Las reglas vigentes están en `CLAUDE.md`; este documento se conserva como registro del diseño original.

## Objetivo

Que un usuario final, hablando con Claude, deje una base de datos MySQL **lista para usar por su sitio**: base + usuario + contraseña + permisos sobre esa única base. Hoy `create_database` deja la base vacía y sin usuarios, y el último paso se hace a mano en cPanel.

## Estado de verificación de la UAPI (leer primero)

No se pudo consultar la documentación oficial al diseñar esto (la ruta probada dio 404 y la búsqueda no devolvió resultados). Todo lo marcado **[por verificar]** sale de memoria y **no debe implementarse sin comprobarlo**. Verificación propuesta, solo lectura y sin crear nada, con un script `scripts/verify_mysql_api.py`:

1. `Mysql::get_restrictions`: confirmar los nombres de campo del prefijo y de los largos máximos (¿`prefix`, `max_database_name_length`, `max_mysql_dbuser_name_length`?).
2. `Mysql::list_users`: confirmar la forma de `data` (nombres y bases asociadas).
3. `Mysql::get_privileges_on_database` (usuario y base existentes): confirmar el formato de la lista de privilegios.
4. Revisar en la documentación de cPanel o en una cuenta de pruebas: parámetros de `create_user` y `set_privileges_on_database`, formato de la lista de privilegios (¿`ALL PRIVILEGES` o separados por comas?), fuerza mínima de contraseña y mensajes de error de «ya existe» / «contraseña débil».

Hasta que esto esté confirmado con respuestas reales, las pruebas unitarias solo demostrarían que el código coincide con mis suposiciones.

## Herramientas propuestas

| Herramienta | Nivel | Descripción |
|---|---|---|
| `list_database_users()` | lectura | Lista los usuarios MySQL de la cuenta y las bases a las que tienen acceso. Nunca devuelve contraseñas. |
| `create_database_user(user, database, privileges="all", execute=false)` | escritura_reversible | Crea un usuario con contraseña generada **y** le da permisos sobre **una** base existente. |
| `grant_database_access(user, database, privileges, execute=false)` | alto_impacto | Da permisos a un usuario existente sobre una base existente. Sirve para completar un alta parcial o compartir una base. |

`grant_database_access` es opcional (ver decisiones abiertas). Sin ella, un alta parcial se repara desde cPanel.

### `create_database_user`

Entradas:

- `user`: solo el nombre, sin prefijo, `[a-z][a-z0-9_]*`. El prefijo de la cuenta lo pone el servidor a partir de `Mysql::get_restrictions`, igual que en `create_database`.
- `database`: nombre completo (con prefijo) de una base **existente**, o el nombre sin prefijo, que se resuelve igual que en `create_database`.
- `privileges`: uno de `all` (por defecto), `read_write`, `read_only`. El modelo **no** puede pasar listas de privilegios arbitrarias; el servidor traduce el nivel a una lista fija:
  - `all`: `ALL PRIVILEGES` sobre esa base (lo que necesitan los instaladores de WordPress y similares).
  - `read_write`: `SELECT, INSERT, UPDATE, DELETE`.
  - `read_only`: `SELECT`.
  - Nunca privilegios globales, `GRANT OPTION` ni `FILE`: los permisos son siempre por base.
- `execute`: `false` por defecto; el dry-run no hace peticiones HTTP ni genera contraseña.

Con `execute=true`, en este orden y **antes de crear nada**:

1. `Mysql::get_restrictions`: prefijo y largos máximos. Se valida el prefijo (solo `[A-Za-z0-9_]`) y se calcula `usuario_completo`. Si excede el máximo, se rechaza.
2. `Mysql::list_databases`: la base debe existir.
3. `Mysql::list_users`: el usuario **no** debe existir (un usuario existente nunca se modifica).
4. Generar la contraseña.
5. `Mysql::create_user`.
6. `Mysql::set_privileges_on_database`.
7. Verificar con `Mysql::get_privileges_on_database` (o `list_users`).

Respuesta:

```json
{
  "dry_run": false,
  "user": "usuario_app",
  "database": "usuario_blog",
  "privileges": "all",
  "host": "localhost",
  "user_created": true,
  "granted": true,
  "verified": true,
  "password": "…",
  "message": "Guarda la contraseña ahora: se muestra una sola vez…"
}
```

`host` informa de `localhost` para que el usuario configure su sitio (`DB_HOST`). El usuario se crea solo para conexiones locales; **no** se expone `Mysql::add_host` ni el acceso remoto.

### `grant_database_access`

Igual que el paso 6 pero sobre un usuario existente, sin contraseña. Regla clave: **solo concede si el usuario no tiene ningún permiso sobre esa base**. Nunca reemplaza ni reduce permisos existentes (`set_privileges_on_database` sobrescribe el conjunto, así que se comprueba antes con `get_privileges_on_database`).

## Contraseña

- La genera el servidor con `secrets`; **el modelo y el usuario no pueden proponerla**, así no viaja una contraseña elegida por una persona (suele reutilizarse) por la conversación.
- 24 caracteres de `[A-Za-z0-9]` garantizando mayúscula, minúscula y dígito. Sin símbolos: se pegará en `wp-config.php`, `.env` o una URL de conexión y los símbolos rompen esos formatos. 24 caracteres alfanuméricos superan de sobra la fuerza mínima de cPanel **[por verificar el mínimo]**.
- Se muestra **una sola vez** en la respuesta; no entra en auditoría, logs ni mensajes de error. Igual que en el correo, queda en el historial de la conversación del cliente MCP: documentarlo.
- Si `create_user` agota el timeout después de enviarse, el usuario puede existir con una contraseña perdida (mismo caso que `add_pop`). Se documenta; se repara desde cPanel (cambiar contraseña).

## Fallos parciales

No hay borrado ni reversión, así que el orden y las comprobaciones previas minimizan el riesgo:

- Todo lo verificable se comprueba **antes** de `create_user` (base existente, usuario libre, largos).
- Si `set_privileges_on_database` falla **después** de crear el usuario, **no se lanza un error** (un error no puede llevar la contraseña, y la regla prohíbe contraseñas en mensajes de error). Se devuelve un resultado con `user_created: true`, `granted: false`, la contraseña y un `warning` que indica completar el permiso con `grant_database_access` o desde cPanel.
- Si la verificación final no coincide: `verified: false` y `warning`, como en `create_email_account`.

## Auditoría

`audit.jsonl` registra acción, `user`, `database`, `privileges`, `execute` y resultado. Debe redactarse `password` (ya está en la lista de claves sensibles de `audit.py`). Prueba obligatoria: la contraseña generada no aparece en el archivo.

## Cambios de reglas (requieren aprobación)

En `CLAUDE.md`:

- Sustituir «No añadas creación de usuarios MySQL, contraseñas, `GRANT`/permisos…» por las reglas de esta función: solo permisos **por base** y con niveles fijos, nunca modificar usuarios o permisos existentes, contraseña generada y mostrada una vez, sin acceso remoto, sin borrado, sin cambio de contraseña, sin acceso a los datos.
- Actualizar la frase de «Seguridad obligatoria» sobre qué existe «por petición explícita».
- Documentar en README y en la checklist manual (incluida la limpieza manual del usuario y la base de prueba).

## Pruebas (cuando se implemente)

Siguiendo `CLAUDE.md`: validación (nombres, niveles de privilegio inválidos, largos), dry-run sin HTTP ni contraseña, ejecución simulada con orden exacto de llamadas, usuario existente rechazado sin crear nada, base inexistente rechazada, fallo de `set_privileges` con resultado parcial y contraseña, auditoría sin la contraseña, registro condicionado por `enabled`, y que no existan herramientas de borrado, cambio de contraseña ni acceso remoto.

## Fuera de alcance

Borrar usuarios o bases, cambiar contraseñas, acceso remoto (`add_host`), privilegios globales o con `GRANT OPTION`, importar/exportar datos y ejecutar SQL.

## Decisiones abiertas

1. **¿Incluir `grant_database_access`?** Recomendado para reparar altas parciales y compartir una base entre usuarios, pero añade una acción que toca permisos. Si no, solo `create_database_user`.
2. **Nivel por defecto de `privileges`.** Propuesto `all` por base, porque un sitio nuevo normalmente instala sus tablas. Alternativa más estricta: `read_write` por defecto.
3. **Nivel en `allowed_actions.json` de `create_database_user`.** Propuesto `escritura_reversible` (paralelo a `create_email_account`, que también crea credenciales), habilitado. Alternativa: `alto_impacto` desactivado por defecto.
4. **Verificación previa.** Ejecutar primero `scripts/verify_mysql_api.py` contra la cuenta real antes de escribir código.
