"""Casos de uso del CLI: crear, listar, consultar y borrar un sitio estático.

El ruteo y el certificado NO se reimplementan: se delega en
`scripts/proxy_domain.py`, que ya es la autoridad sobre el file provider de
Traefik y la CA del laboratorio.
"""

from __future__ import annotations

import os
import shlex
import sys
import time

from .config import CommandResult, StaticSettings
from .content import MARKER, seed_content
from .docker import Docker, DockerError
from .names import KIND, LABEL_DOMAIN, LABEL_KIND, LABEL_SITE, LABEL_STATIC, SiteNames


class CliError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _runner(settings: StaticSettings):
    return settings.runner()


def _docker(settings: StaticSettings) -> Docker:
    return Docker(_runner(settings), timeout=settings.command_timeout)


def _proxy(settings: StaticSettings, *args: str) -> CommandResult:
    remote = settings.transport == "ssh" or settings.remote_repo is not None
    if not remote and not settings.proxy_script.is_file():
        raise CliError(
            f"No existe {settings.proxy_script}; ejecutá el CLI desde el repo de SPanel",
            2,
        )
    argv = [
        settings.python,
        str(settings.proxy_script),
        "--profile",
        settings.profile,
        *args,
    ]
    return settings.runner().run(
        argv,
        timeout=settings.command_timeout + settings.wait_seconds + 30,
    )


def _show_proxy_result(result: CommandResult) -> None:
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)


def _wait_http(docker: Docker, container: str, seconds: int) -> None:
    deadline = time.monotonic() + max(1, seconds)
    while time.monotonic() < deadline:
        if docker.container_running(container) and docker.http_ok(container, MARKER):
            return
        time.sleep(1)
    raise CliError(
        f"El contenedor {container} no sirvió la página en {seconds}s; "
        f"revisá 'docker logs {container}'",
        3,
    )


def _ensure_network(docker: Docker, network: str) -> None:
    if not docker.network_exists(network):
        raise CliError(
            f"No existe la red Docker {network!r}. "
            "En el laboratorio: ./scripts/traefik-lab.sh up",
            3,
        )


def _ensure_image(docker: Docker, image: str) -> None:
    if docker.image_exists(image):
        return
    print(f"==> descargando {image}", file=sys.stderr)
    result = docker.run("pull", image, check=False, timeout=600)
    if not result.ok:
        raise CliError(f"No se pudo descargar {image}: {result.text().strip()[:300]}", 3)


def _create_dry_run(settings: StaticSettings, names: SiteNames, domain: str, www: bool) -> None:
    args = [
        "docker",
        "volume",
        "create",
        *names.label_args(domain),
        names.volume,
    ]
    run = [
        "docker",
        "run",
        "-d",
        "--name",
        names.container,
        "--network",
        settings.network,
        "--restart",
        "unless-stopped",
        *names.label_args(domain),
        "-v",
        f"{names.volume}:/usr/share/nginx/html",
        settings.image,
    ]
    print("# dry-run: no se ejecuta nada")
    print(f"# perfil:  {settings.profile}")
    print(f"# red:     {settings.network}")
    print(f"# imagen:  {settings.image}")
    print(f"# archivo de ruta esperado: scripts/traefik-lab/dynamic/{names.route_file}")
    print()
    print(" ".join(shlex.quote(part) for part in args))
    print(" ".join(shlex.quote(part) for part in run))
    print()
    print("# contenido inicial:")
    for name in ("index.html", "styles.css", "app.js"):
        print(f"#   {name} -> /usr/share/nginx/html/{name}")
    print()
    domain_args = ["--domain", domain, "--wait", str(settings.wait_seconds)]
    if www:
        domain_args.insert(0, "--www")
    print(
        " ".join(
            shlex.quote(part)
            for part in [
                settings.python,
                str(settings.proxy_script),
                "--profile",
                settings.profile,
                "add",
                "--site",
                names.container,
                *domain_args,
            ]
        )
    )


def create_site(
    settings: StaticSettings,
    *,
    name: str,
    domain: str,
    force: bool = False,
    reuse: bool = False,
    www: bool = False,
    dry_run: bool = False,
) -> int:
    names = SiteNames(name)
    docker = _docker(settings)

    if dry_run:
        _create_dry_run(settings, names, domain, www)
        return 0

    _ensure_network(docker, settings.network)
    _ensure_image(docker, settings.image)

    exists = docker.container_exists(names.container)
    if exists and not reuse:
        raise CliError(
            f"Ya existe {names.container}. Usá --reuse para reutilizarlo o "
            f"borralo con: python3 scripts/spanel-static.py delete --name {name}",
            2,
        )
    if exists:
        owner = docker.label(names.container, LABEL_STATIC)
        if owner != "true":
            raise CliError(
                f"{names.container} existe pero no está marcado como sitio estático "
                f"({LABEL_STATIC}={owner or 'ausente'})",
                2,
            )
        if docker.container_running(names.container) is False:
            docker.run("start", names.container)

    created_volume = False
    created_container = False
    try:
        if not docker.volume_exists(names.volume):
            docker.create_volume(names.volume, names, domain)
            created_volume = True
        if not exists:
            docker.create_container(names, domain, settings.network, settings.image)
            created_container = True

        written = seed_content(docker, names.container, force=force)
        _wait_http(docker, names.container, settings.wait_seconds)
    except Exception:
        if created_container:
            docker.remove_container(names.container)
        if created_volume:
            docker.remove_volume(names.volume)
        raise

    route_args = [
        "add",
        "--site",
        names.container,
        "--domain",
        domain,
        "--wait",
        str(settings.wait_seconds),
    ]
    if www:
        route_args.insert(0, "--www")
    result = _proxy(settings, *route_args)
    _show_proxy_result(result)
    if not result.ok:
        raise CliError(
            "El contenedor quedó creado, pero Traefik/certificado falló. "
            f"Revisá el detalle de arriba y el contenedor {names.container}.",
            4,
        )

    print()
    print(f"contenedor: {names.container}")
    print(f"volumen:    {names.volume}")
    print(f"contenido:  {', '.join(written) if written else 'sin cambios (ya existía)'}")
    port = _https_port(settings)
    suffix = "" if port == 443 else f":{port}"
    print(f"página:     https://{domain}{suffix}")
    print()
    print("Verificación desde el host Docker:")
    print(f"  curl --cacert scripts/traefik-lab/ca/root_ca.crt https://{domain}{suffix}/")
    print()
    if port != 443:
        print(
            "Desde otra máquina, abrí un túnel y usá --resolve:\n"
            f"  ssh -N -L {port}:127.0.0.1:443 <usuario>@<host>\n"
            f"  curl --cacert root_ca.crt --resolve {domain}:{port}:127.0.0.1 "
            f"https://{domain}:{port}/\n"
        )
    print("Si abrís el navegador en el host, agregá una vez en /etc/hosts:")
    print(f"  127.0.0.1 {domain}")
    return 0


def _https_port(settings: StaticSettings) -> int:
    """El puerto HTTPS publicado por el Traefik del laboratorio, o 443."""
    container = (
        os.environ.get("SPANEL_PROXY_TRAEFIK_CONTAINER", "").strip()
        or ("spanel-traefik-lab" if settings.profile == "internal" else "traefik")
    )
    result = settings.runner().run(
        ["docker", "port", container, "443"],
        timeout=20,
    )
    if not result.ok:
        return 443
    text = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if ":" in text:
        try:
            return int(text.rsplit(":", 1)[1])
        except ValueError:
            return 443
    return 443


def _resolve_container_by_domain(docker: Docker, domain: str) -> str | None:
    result = docker.run(
        "ps",
        "-a",
        "--filter",
        f"label={LABEL_DOMAIN}={domain}",
        "--format",
        "{{.Names}}",
        check=False,
    )
    if not result.ok:
        return None
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names[0] if names else None


def _site_identity(docker: Docker, container: str) -> tuple[str, str]:
    slug = docker.label(container, LABEL_SITE)
    kind = docker.label(container, LABEL_KIND)
    domain = docker.label(container, LABEL_DOMAIN)
    if not slug:
        raise CliError(f"{container} no tiene la etiqueta {LABEL_SITE}", 2)
    if kind != KIND:
        raise CliError(f"{container} no es un sitio estático (kind={kind or 'ausente'})", 2)
    return slug, domain


def delete_site(
    settings: StaticSettings,
    *,
    name: str | None = None,
    domain: str | None = None,
    keep_volume: bool = False,
    dry_run: bool = False,
) -> int:
    docker = _docker(settings)
    if name:
        names = SiteNames(name)
        container = names.container
    elif domain:
        container = _resolve_container_by_domain(docker, domain) or ""
        if not container:
            raise CliError(f"No encontré un sitio estático para {domain}", 2)
        slug, _ = _site_identity(docker, container)
        names = SiteNames(slug)
    else:
        raise CliError("Indicá --name o --domain", 2)

    if not docker.container_exists(container):
        raise CliError(f"No existe el contenedor {container}", 2)
    _, saved_domain = _site_identity(docker, container)
    target_domain = domain or saved_domain

    if dry_run:
        print("# dry-run: no se borra nada")
        print(f"docker rm -f {container}")
        if not keep_volume:
            print(f"docker volume rm {names.volume}")
        if target_domain:
            print(
                f"{settings.python} {settings.proxy_script} --profile {settings.profile} "
                f"remove --domain {target_domain}"
            )
        return 0

    if target_domain:
        result = _proxy(settings, "remove", "--domain", target_domain)
        _show_proxy_result(result)
        if not result.ok:
            print(
                "aviso: no se pudo quitar la ruta del proxy; se continúa con el contenedor",
                file=sys.stderr,
            )

    docker.remove_container(container)
    if not keep_volume:
        docker.remove_volume(names.volume)
    print(f"eliminado: {container}" + ("" if keep_volume else f" y volumen {names.volume}"))
    return 0


def list_sites(settings: StaticSettings) -> int:
    docker = _docker(settings)
    result = docker.run(
        "ps",
        "-a",
        "--filter",
        f"label={LABEL_STATIC}=true",
        "--format",
        '{{.Names}}\t{{.Status}}\t{{.Label "spanel.site"}}\t{{.Label "com.spanel.static.domain"}}',
        check=False,
    )
    if not result.ok:
        raise CliError(f"docker ps falló: {result.text().strip()[:300]}", 3)
    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        print("no hay sitios estáticos")
        return 0
    print(f"{'CONTENEDOR':<32} {'ESTADO':<24} {'SLUG':<20} DOMINIO")
    for row in rows:
        row += [""] * (4 - len(row))
        print(f"{row[0]:<32} {row[1][:23]:<24} {row[2]:<20} {row[3]}")
    return 0


def status_site(
    settings: StaticSettings,
    *,
    name: str | None = None,
    domain: str | None = None,
) -> int:
    docker = _docker(settings)
    if name:
        names = SiteNames(name)
        if not docker.container_exists(names.container):
            raise CliError(f"No existe el contenedor {names.container}", 2)
        print(f"contenedor: {names.container}")
        print(f"estado:     {'running' if docker.container_running(names.container) else 'stopped'}")
        print(f"volumen:    {names.volume}")
        saved_domain = docker.label(names.container, LABEL_DOMAIN)
        if saved_domain:
            domain = saved_domain
    if not domain:
        raise CliError("Indicá --name o --domain", 2)
    result = _proxy(settings, "status", "--domain", domain)
    _show_proxy_result(result)
    return 0 if result.ok else 4


def doctor(settings: StaticSettings) -> int:
    docker = _docker(settings)
    checks: list[tuple[str, bool, str]] = []
    info = docker.run("info", check=False, timeout=20)
    checks.append(("docker info", info.ok, info.text().strip()[:120]))
    checks.append(
        ("red " + settings.network, docker.network_exists(settings.network), settings.network)
    )
    image = docker.image_exists(settings.image)
    checks.append(("imagen " + settings.image, image, "presente" if image else "se descargará"))
    proxy_local = settings.proxy_script.is_file()
    remote = settings.transport == "ssh" or settings.remote_repo is not None
    checks.append(
        (
            "proxy_domain.py",
            proxy_local or remote,
            str(settings.proxy_script) + (" (remoto)" if remote and not proxy_local else ""),
        )
    )
    for label, ok, detail in checks:
        print(f"[{'ok' if ok else 'FALLA'}] {label}: {detail}")
    if proxy_local or remote:
        print()
        result = _proxy(settings, "doctor")
        _show_proxy_result(result)
        return 0 if result.ok else 4
    return 0 if all(ok for _, ok, _ in checks) else 3
