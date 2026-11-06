"""Casos de uso: `up`, `verify` y `down` de los perfiles de estáticos.

El ruteo y el certificado NO se reimplementan: se delega en
`scripts/proxy_domain.py` (perfil internal = laboratorio), igual que hace
`scripts/spanel_static`. Todo lo demás es Docker puro por perfil.
"""

from __future__ import annotations

import os
import time

from spanel_static.config import CommandResult, StaticSettings
from spanel_static.provision import CliError

from .docker import DockerError
from .docker import ProfileDocker as Docker
from .profiles import KIND, LABEL_PROFILE, MARKER_ATTR, ProfileSpec, get_profiles

# Los 3 motores de estáticos de vérificación + Astro completo. `astro`
# tarda mucho por npm install; wait propio para él.
DEFAULT_WAIT = 60
ASTRO_WAIT = 420


def _docker(settings: StaticSettings) -> Docker:
    return Docker(settings.runner(), timeout=settings.command_timeout)


def _proxy(settings: StaticSettings, *args: str) -> CommandResult:
    argv = [settings.python, str(settings.proxy_script), "--profile", settings.profile, *args]
    return settings.runner().run(argv, timeout=300)


def _wait_seconds(spec: ProfileSpec) -> int:
    return ASTRO_WAIT if spec.slug == "astro" else DEFAULT_WAIT


def _seed_files(docker: Docker, spec: ProfileSpec, force: bool) -> list[str]:
    """Escribe los archivos del perfil que falten; devuelve los escritos.

    Regla del `spanel_static` original: si el archivo existe YA CON la marca
    del perfil se respeta (aunque no sea -force); si es el de bienvenida de la
    imagen (`index.html` sin marca) se reemplaza siempre; el resto se respeta
    salvo `--force`.
    """
    written: list[str] = []
    for name, content in spec.files.items():
        destination = f"{spec.web_root}/{name}"
        existing = None if force else docker.read_file(spec.container, destination)
        skip = False
        if existing is not None:
            if name == "index.html":
                skip = MARKER_ATTR in existing
            else:
                skip = True
        if skip:
            continue
        docker.write_file(spec.container, destination, content)
        written.append(name)
    if spec.chown_to:
        docker.chown(spec.container, spec.chown_to, spec.web_root)
    return written


def _profiles_with_astro(only: list[str] | None) -> dict[str, ProfileSpec]:
    return get_profiles(only)


def up(
    settings: StaticSettings,
    only: list[str] | None = None,
    *,
    force: bool = False,
) -> int:
    """Provisiona cada perfil: volumen + contenedor + seed + health + ruta."""
    docker = _docker(settings)
    specs = _profiles_with_astro(only)

    if not docker.network_exists(settings.network):
        raise CliError(
            f"No existe la red Docker {settings.network!r}. "
            "En el laboratorio: ./scripts/traefik-lab.sh up",
            3,
        )

    results: list[str] = []
    for spec in specs.values():
        print(f"==> {spec.slug}: {spec.domain} ({spec.image})")
        wait = _wait_seconds(spec)

        created = False
        try:
            # --- contenedor actual, con autocuración de labels --------------
            existing = docker.container_exists(spec.container)
            if existing and docker.label(spec.container, LABEL_PROFILE) != spec.slug:
                docker.remove_container(spec.container)
                existing = False
                print("    contenedor sin labels del perfil; recreado")
            if existing and force:
                docker.remove_container(spec.container)
                # El build del comando arranca al ver el seed listo: con el
                # volumen viejo compilaría con la config anterior. Recrear el
                # volumen también garantiza seed+config+build coherentes.
                docker.remove_volume(spec.volume)
                existing = False
                print("    --force: recreado contenedor y volumen para re-compilar")

            if not existing:
                if not docker.volume_exists(spec.volume):
                    docker.create_volume(spec)
                docker.create_container(spec, settings.network)
                created = True
                # El `run -d` regresa antes de que el daemon registre el
                # contenedor para exec; sin esta espera el seed corre contra
                # "No such container".
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not docker.container_running(
                    spec.container
                ):
                    time.sleep(0.5)
                if not docker.container_exists(spec.container):
                    raise DockerError(
                        f"{spec.container} no existe tras run -d; revisá docker info"
                    )
            elif not docker.container_running(spec.container):
                docker.run("start", spec.container)

            healthy = docker.container_running(spec.container) and docker.http_mark(
                spec.container, 80, spec.marker
            )
            if healthy and not force:
                print("    ya levantado y healthy; sin re-seed")
            else:
                written = _seed_files(docker, spec, force)
                print(f"    seed: {', '.join(written) if written else 'sin cambios'}")
                docker.wait_http(spec, wait)
        except (DockerError, CliError) as exc:
            if created:
                docker.remove_container(spec.container)
            raise CliError(
                f"falla en el perfil {spec.slug}: {exc} (opcional: docker logs {spec.container})",
                3,
            )

        # --- ruta: se asegura siempre (idempotente) --------------------------
        if not _route_ok(docker.runner, spec):
            route = _proxy(settings, "add", "--site", spec.container, "--domain", spec.domain)
            print(route.text().strip()[-500:])
            if not route.ok or not _route_ok(docker.runner, spec):
                raise CliError(f"No se pudo publicar la ruta de {spec.slug}", 3)
        results.append(f"{spec.slug}: {spec.domain} (imagen {spec.image})")

    for line in results:
        print(f"UP   ✓ {line}")
    return 0


def _route_ok(runner, spec: ProfileSpec) -> bool:
    """HTTPS real por Traefik sirve el HTML del perfil (200 + marker)."""
    port = _https_port()
    argv = [
        "curl",
        "-sk",
        "--resolve",
        f"{spec.domain}:{port}:127.0.0.1",
        f"https://{spec.domain}:{port}/",
    ]
    r = runner.run(argv, timeout=30)
    return r.ok and spec.marker in r.stdout


def _https_port() -> int:
    """Puerto HTTPS del lab publicado en 127.0.0.1 (env del proxy; default 443)."""
    raw = os.environ.get("SPANEL_PROXY_HTTPS_PORT", "").strip()
    try:
        return int(raw) if raw else 443
    except ValueError:
        return 443



def verify(settings: StaticSettings, only: list[str] | None = None) -> int:
    """Ocho checks por perfil: labels, health interno y HTTPS externo real."""
    docker = _docker(settings)
    specs = _profiles_with_astro(only)
    failures: list[str] = []

    for spec in specs.values():
        checks: list[tuple[str, bool, str]] = []

        existe = docker.container_exists(spec.container)
        running = existe and docker.container_running(spec.container)
        checks.append(("contenedor running", bool(running), spec.container))

        imagen = ""
        if existe:
            imagen = docker.runner.run(  # type: ignore[attr-defined]
                ["docker", "inspect", "-f", "{{.Config.Image}}", spec.container],
                timeout=15,
            ).stdout.strip()
        checks.append(("imagen del perfil", imagen == spec.image, f"{imagen!r} != {spec.image!r}"))

        profile_label = docker.label(spec.container, LABEL_PROFILE)
        kind_label = docker.label(spec.container, "spanel.kind")
        managed_label = docker.label(spec.container, "spanel.managed")
        checks.append(("labels de propiedad", kind_label == KIND and managed_label == "true",
                       f"kind={kind_label} managed={managed_label}"))
        checks.append(("label de perfil", profile_label == spec.slug, f"profile={profile_label}"))

        checks.append(
            ("health interno (wget + marker)", bool(existe and docker.http_mark(
                spec.container, 80, spec.marker)), "marker no servido en 127.0.0.1:80")
        )

        curl = docker.runner.run(
            [
                "curl",
                "-sk",
                "--resolve",
                f"{spec.domain}:{_https_port()}:127.0.0.1",
                f"https://{spec.domain}:{_https_port()}/",
            ],
            timeout=30,
        )
        curl_ok = curl.ok and spec.marker in curl.stdout
        checks.append(("HTTPS por Traefik (marker)", curl_ok,
                       (curl.text().strip()[:120] if not curl_ok else "")))

        for label, ok, detail in checks:
            if ok:
                print(f"PASS {spec.slug}: {label}")
            else:
                failures.append(f"{spec.slug}: {label} ({detail})")
                print(f"FAIL {spec.slug}: {label} — {detail}")

    print()
    if failures:
        print(f"VERIFICACIÓN: {len(failures)} fallo(s)")
        return 1
    print("VERIFICACIÓN: todos los perfiles en verde.")
    return 0


def down(settings: StaticSettings, only: list[str] | None = None, *, keep_routes: bool) -> int:
    """Quita contenedores y volúmenes de los perfiles; deja las imágenes."""
    docker = _docker(settings)
    specs = _profiles_with_astro(only)
    for spec in specs.values():
        if not keep_routes:
            route = _proxy(settings, "remove", "--domain", spec.domain)
            if not route.ok and "no existe" not in route.text().lower():
                print(f"aviso: no se pudo quitar la ruta {spec.domain}: {route.text().strip()[:200]}")
            else:
                print(f"RUTA removida: {spec.domain}")
        docker.remove_container(spec.container)
        docker.remove_volume(spec.volume)
        print(f"DOWN ✓ {spec.slug} ({spec.container})")
    return 0


def status(settings: StaticSettings, only: list[str] | None = None) -> int:
    docker = _docker(settings)
    for spec in get_profiles(only).values():
        existe = docker.container_exists(spec.container)
        if not existe:
            print(f"{spec.slug:8} ausente")
            continue
        running = docker.container_running(spec.container)
        healthy = docker.http_mark(spec.container, 80, spec.marker)
        label = docker.label(spec.container, LABEL_PROFILE)
        estado = "healthy" if running and healthy else ("running sin marker" if running else "detenido")
        print(f"{spec.slug:8} {estado:18} imagen={spec.image} label={label}")
    return 0
