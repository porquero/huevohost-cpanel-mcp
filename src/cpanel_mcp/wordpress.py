"""Preparación de WordPress: sube y extrae el paquete oficial en un directorio.

Flujo de ``prepare_wordpress``:

1. Comprueba que el directorio existe, no es un enlace simbólico y está vacío.
2. Descarga ``latest.zip`` de wordpress.org **desde el equipo del usuario** y
   verifica su SHA-1 oficial. La URL es fija: el modelo no puede indicar otra.
3. Valida el zip, quita la carpeta ``wordpress/`` y lo reempaqueta en memoria.
4. Lo sube como un único archivo, lo extrae en el servidor y lo envía a la
   papelera de cPanel.

No crea ``wp-config.php`` ni ejecuta el instalador: el usuario completa la
instalación en el navegador. Este es el único flujo que deja archivos PHP en la
cuenta, y solo del paquete oficial verificado; nunca contenido del modelo.
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
import stat
import zipfile
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .cpanel import CPanelClient
from .errors import (
    CPanelError,
    FileAlreadyExistsError,
    FileOperationError,
    RemoteFileNotFoundError,
    UnsafePathError,
    WordPressPackageError,
)
from .file_ops import (
    display_path,
    ensure_safe_remote_path,
    normalize_public_path,
)
from .validators import validate_execute

PACKAGE_URL = "https://wordpress.org/latest.zip"
CHECKSUM_URL = "https://wordpress.org/latest.zip.sha1"
ALLOWED_HOSTS = ("wordpress.org",)  # y subdominios (downloads.wordpress.org)
MAX_REDIRECTS = 3
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_MEMBERS = 20_000
DOWNLOAD_TIMEOUT_SECONDS = 120.0
TRANSFER_TIMEOUT_SECONDS = 300.0  # subir y extraer ~25 MB; el resto usa 30 s

TOP_LEVEL = "wordpress/"
# Contenido que puede existir en un docroot recién creado sin ser un sitio.
HARMLESS_ENTRIES = {"cgi-bin", ".well-known"}
REQUIRED_AFTER_INSTALL = {
    "index.php",
    "wp-admin",
    "wp-content",
    "wp-includes",
    "wp-load.php",
    "wp-config-sample.php",
}
_RESERVED_COMPONENTS = {".git", ".hg", ".ssh", ".svn", "cgi-bin"}
_SHA1 = re.compile(r"\b[0-9a-fA-F]{40}\b")
_VERSION = re.compile(r"\$wp_version\s*=\s*'([^']+)'")


def _host_allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS
    )


async def _get(http: httpx.AsyncClient, url: str) -> bytes:
    """GET con redirecciones manuales limitadas a wordpress.org por HTTPS."""

    if not _host_allowed(url):
        raise WordPressPackageError("URL de descarga no permitida.")
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            async with http.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    current = urljoin(current, location)
                    if not _host_allowed(current):
                        raise WordPressPackageError(
                            "wordpress.org redirigió a un destino no permitido; "
                            "no se descargó nada."
                        )
                    continue
                if response.status_code != 200:
                    raise WordPressPackageError(
                        f"wordpress.org respondió HTTP {response.status_code}. "
                        "Inténtalo de nuevo más tarde."
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOWNLOAD_BYTES:
                        raise WordPressPackageError(
                            "El paquete de WordPress supera el tamaño máximo "
                            "esperado; se descartó."
                        )
                return bytes(body)
        except httpx.TimeoutException:
            raise WordPressPackageError(
                "wordpress.org no respondió a tiempo. Inténtalo de nuevo."
            ) from None
        except httpx.HTTPError:
            raise WordPressPackageError(
                "No se pudo descargar WordPress desde wordpress.org. Revisa la "
                "conexión a internet del equipo donde corre el MCP."
            ) from None
    raise WordPressPackageError("Demasiadas redirecciones al descargar WordPress.")


async def download_package(http: httpx.AsyncClient) -> bytes:
    """Descarga el zip oficial y verifica su SHA-1 publicado por wordpress.org."""

    checksum_text = (await _get(http, CHECKSUM_URL)).decode("ascii", "ignore")
    match = _SHA1.search(checksum_text)
    if not match:
        raise WordPressPackageError(
            "No se pudo leer el SHA-1 oficial de WordPress; no se instala nada."
        )
    package = await _get(http, PACKAGE_URL)
    # SHA-1 solo se usa para comprobar la integridad contra el valor publicado
    # por wordpress.org (el único que ofrece); el transporte es HTTPS.
    actual = hashlib.sha1(package).hexdigest()  # noqa: S324
    if actual.lower() != match.group(0).lower():
        raise WordPressPackageError(
            "El SHA-1 del paquete descargado no coincide con el oficial de "
            "wordpress.org. Se descartó; no se subió nada."
        )
    return package


def repack_without_top_level(package: bytes) -> tuple[bytes, str | None, int]:
    """Valida el zip y lo reempaqueta sin la carpeta ``wordpress/``.

    Devuelve ``(zip_nuevo, versión, nº de archivos)``. Rechaza rutas absolutas,
    ``..``, enlaces simbólicos, entradas cifradas, miembros fuera de
    ``wordpress/`` y paquetes con demasiados archivos o demasiado grandes.
    """

    try:
        source = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile:
        raise WordPressPackageError("El paquete descargado no es un zip válido.") from None

    output = io.BytesIO()
    version: str | None = None
    files = 0
    total = 0
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        members = source.infolist()
        if not members or len(members) > MAX_MEMBERS:
            raise WordPressPackageError("El paquete de WordPress tiene un tamaño inesperado.")
        for info in members:
            name = info.filename
            if (
                not name.startswith(TOP_LEVEL)
                or name.startswith("/")
                or "\\" in name
                or "\x00" in name
                or any(part in {"..", "."} for part in name.split("/"))
            ):
                raise WordPressPackageError(
                    "El paquete contiene rutas no permitidas; se descartó."
                )
            if info.flag_bits & 0x1:
                raise WordPressPackageError("El paquete contiene entradas cifradas.")
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise WordPressPackageError(
                    "El paquete contiene enlaces simbólicos; se descartó."
                )
            relative = name[len(TOP_LEVEL):]
            if not relative:
                continue
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise WordPressPackageError(
                    "El paquete se expande por encima del límite permitido."
                )
            new_info = zipfile.ZipInfo(relative, date_time=info.date_time)
            new_info.external_attr = info.external_attr
            new_info.compress_type = zipfile.ZIP_DEFLATED
            if info.is_dir():
                target.writestr(new_info, b"")
                continue
            data = source.read(info)
            files += 1
            if relative == "wp-includes/version.php":
                found = _VERSION.search(data.decode("utf-8", "ignore"))
                version = found.group(1) if found else None
            target.writestr(new_info, data)
    if files == 0:
        raise WordPressPackageError("El paquete de WordPress está vacío.")
    return output.getvalue(), version, files


class WordPressInstaller:
    def __init__(
        self,
        client: CPanelClient,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.client = client
        self._http = http

    async def prepare_wordpress(
        self,
        directory: str,
        execute: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_public_path(directory, allow_root=False)
        if any(part.lower() in _RESERVED_COMPONENTS for part in normalized.split("/")[1:]):
            raise UnsafePathError(
                "El directorio no puede estar dentro de .git, .svn, .hg, .ssh ni cgi-bin."
            )
        execute = validate_execute(execute)

        if not execute:
            return {
                "dry_run": True,
                "directory": normalized,
                "directory_display": display_path(normalized),
                "prepared": False,
                "verified": False,
                "message": (
                    "Simulación: no se contactó cPanel ni wordpress.org. Al "
                    "ejecutar se comprobará que el directorio existe y está "
                    "vacío, se descargará WordPress de wordpress.org en este "
                    "equipo verificando su SHA-1, se subirá y extraerá en el "
                    "directorio, y se eliminará el zip. No se crea wp-config.php "
                    "ni se instala: el usuario completa la instalación en el "
                    "navegador."
                ),
            }

        await ensure_safe_remote_path(
            self.client, normalized, must_exist=False, expect_dir=True
        )
        if await self._inspect_dir(normalized) is None:
            raise RemoteFileNotFoundError(
                f"El directorio '{display_path(normalized)}' no existe. Crea "
                "antes el subdominio (create_subdomain) o el directorio."
            )
        await self._ensure_empty(normalized)

        owns_http = self._http is None
        http = self._http or httpx.AsyncClient(
            timeout=httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": "huevohost-cpanel-mcp"},
        )
        try:
            package = await download_package(http)
        finally:
            if owns_http:
                await http.aclose()
        archive, version, file_count = repack_without_top_level(package)

        zip_name = f"wordpress-install-{secrets.token_hex(6)}.zip"
        zip_path = f"{normalized}/{zip_name}"
        await self.client.upload_file(
            normalized,
            zip_name,
            archive,
            overwrite=False,
            timeout=TRANSFER_TIMEOUT_SECONDS,
        )

        extract_error: Exception | None = None
        try:
            home = await self.client.get_home_directory()
            await self.client.extract_archive(
                zip_path,
                f"{home}/{normalized}",
                timeout=TRANSFER_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - se limpia el zip y se informa
            extract_error = exc

        zip_removed = True
        try:
            await self.client.trash_file(zip_path)
        except CPanelError:
            zip_removed = False

        if extract_error is not None:
            leftover = (
                ""
                if zip_removed
                else f" Quedó el zip '{zip_name}' en el directorio: bórralo desde cPanel."
            )
            raise FileOperationError(
                f"No se pudo extraer WordPress en '{display_path(normalized)}'. "
                "El directorio puede haber quedado con archivos a medias: "
                f"vacíalo desde cPanel antes de reintentar.{leftover} "
                f"Detalle: {extract_error}"
            ) from extract_error

        names = await self._names(normalized)
        missing = sorted(REQUIRED_AFTER_INSTALL - names)
        verified = not missing
        site_url = await self._site_url(normalized)

        result: dict[str, Any] = {
            "dry_run": False,
            "directory": normalized,
            "directory_display": display_path(normalized),
            "prepared": True,
            "version": version,
            "files": file_count,
            "zip_removed": zip_removed,
            "verified": verified,
            "site_url": site_url,
            "next_steps": self._next_steps(site_url),
            "message": (
                "Archivos de WordPress"
                + (f" {version}" if version else "")
                + " subidos y extraídos. WordPress aún NO está instalado: "
                "el usuario debe completar la instalación en el navegador."
            ),
        }
        warnings: list[str] = []
        if not verified:
            warnings.append(
                "La extracción terminó, pero faltan archivos esperados de "
                f"WordPress: {', '.join(missing)}. Revisa el directorio en cPanel."
            )
        if not zip_removed:
            warnings.append(
                f"No se pudo mover a la papelera el zip '{zip_name}'; bórralo "
                "desde cPanel."
            )
        if warnings:
            result["warning"] = " ".join(warnings)
        return result

    # ------------------------------------------------------------ auxiliares
    async def _inspect_dir(self, normalized: str) -> dict[str, Any] | None:
        try:
            info = await self.client.get_file_information(normalized)
        except RemoteFileNotFoundError:
            return None
        if info.get("exists") in {0, "0", False}:
            return None
        return info

    async def _names(self, normalized: str) -> set[str]:
        entries = await self.client.list_files(normalized, show_hidden=True)
        return {
            entry["file"]
            for entry in entries
            if isinstance(entry.get("file"), str) and entry["file"]
        }

    async def _ensure_empty(self, normalized: str) -> None:
        unexpected = sorted(await self._names(normalized) - HARMLESS_ENTRIES)
        if unexpected:
            shown = ", ".join(unexpected[:5]) + ("…" if len(unexpected) > 5 else "")
            raise FileAlreadyExistsError(
                f"'{display_path(normalized)}' no está vacío (contiene: {shown}). "
                "Para no sobrescribir ni mezclar con un sitio existente, WordPress "
                "solo se prepara en un directorio vacío. No se modificó nada."
            )

    async def _site_url(self, normalized: str) -> str | None:
        try:
            subdomains = await self.client.list_subdomains()
        except CPanelError:
            return None
        for entry in subdomains:
            base = entry.get("basedir")
            domain = entry.get("domain")
            if not isinstance(base, str) or not isinstance(domain, str):
                continue
            base = base.strip("/")
            if base == normalized or base.endswith("/" + normalized):
                return f"https://{domain.strip().lower()}"
        return None

    @staticmethod
    def _next_steps(site_url: str | None) -> list[str]:
        where = site_url or "la dirección del sitio (el dominio que apunta a ese directorio)"
        return [
            f"Abre {where} en el navegador.",
            "En el asistente de WordPress elige el idioma y pulsa «¡Vamos a ello!».",
            "Introduce los datos de la base de datos: nombre de la base, usuario, "
            "contraseña, servidor «localhost» y deja el prefijo «wp_».",
            "En la pantalla siguiente indica el título del sitio y crea el usuario "
            "administrador con una contraseña propia.",
        ]
