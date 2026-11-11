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
            f"{settings.proxy_script} does not exist; run the CLI from the SPanel repo",
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
        f"Container {container} did not serve the page within {seconds}s; "
        f"check 'docker logs {container}'",
        3,
    )


def _ensure_network(docker: Docker, network: str) -> None:
    if not docker.network_exists(network):
        raise CliError(
            f"Docker network does not exist: {network!r}. "
            "In the lab: ./scripts/traefik-lab.sh up",
            3,
        )


def _ensure_image(docker: Docker, image: str) -> None:
    if docker.image_exists(image):
        return
    print(f"==> pulling {image}", file=sys.stderr)
    result = docker.run("pull", image, check=False, timeout=600)
    if not result.ok:
        raise CliError(f"Could not pull {image}: {result.text().strip()[:300]}", 3)


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
    print("# dry-run: nothing is executed")
    print(f"# profile: {settings.profile}")
    print(f"# network: {settings.network}")
    print(f"# image:   {settings.image}")
    print(f"# expected route file: scripts/traefik-lab/dynamic/{names.route_file}")
    print()
    print(" ".join(shlex.quote(part) for part in args))
    print(" ".join(shlex.quote(part) for part in run))
    print()
    print("# initial content:")
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
            f"Already exists: {names.container}. Use --reuse to reuse it or "
            f"delete it with: python3 scripts/spanel-static.py delete --name {name}",
            2,
        )
    if exists:
        owner = docker.label(names.container, LABEL_STATIC)
        if owner != "true":
            raise CliError(
                f"{names.container} exists but is not marked as a static site "
                f"({LABEL_STATIC}={owner or 'missing'})",
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
            "The container was created, but Traefik/certificate failed. "
            f"Check the detail above and the container {names.container}.",
            4,
        )

    print()
    print(f"container: {names.container}")
    print(f"volume:    {names.volume}")
    print(f"content:   {', '.join(written) if written else 'unchanged (already existed)'}")
    port = _https_port(settings)
    suffix = "" if port == 443 else f":{port}"
    print(f"page:      https://{domain}{suffix}")
    print()
    print("Verification from the Docker host:")
    print(f"  curl --cacert scripts/traefik-lab/ca/root_ca.crt https://{domain}{suffix}/")
    print()
    if port != 443:
        print(
            "From another machine, open a tunnel and use --resolve:\n"
            f"  ssh -N -L {port}:127.0.0.1:443 <user>@<host>\n"
            f"  curl --cacert root_ca.crt --resolve {domain}:{port}:127.0.0.1 "
            f"https://{domain}:{port}/\n"
        )
    print("If you open the browser on the host, add this once to /etc/hosts:")
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
        raise CliError(f"{container} does not have the label {LABEL_SITE}", 2)
    if kind != KIND:
        raise CliError(f"{container} is not a static site (kind={kind or 'missing'})", 2)
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
            raise CliError(f"Could not find a static site for {domain}", 2)
        slug, _ = _site_identity(docker, container)
        names = SiteNames(slug)
    else:
        raise CliError("Specify --name or --domain", 2)

    if not docker.container_exists(container):
        raise CliError(f"Container does not exist: {container}", 2)
    _, saved_domain = _site_identity(docker, container)
    target_domain = domain or saved_domain

    if dry_run:
        print("# dry-run: nothing is deleted")
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
                "warning: could not remove the proxy route; continuing with the container",
                file=sys.stderr,
            )

    docker.remove_container(container)
    if not keep_volume:
        docker.remove_volume(names.volume)
    print(f"deleted: {container}" + ("" if keep_volume else f" and volume {names.volume}"))
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
        raise CliError(f"docker ps failed: {result.text().strip()[:300]}", 3)
    rows = [line.split("\t") for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        print("there are no static sites")
        return 0
    print(f"{'CONTAINER':<32} {'STATUS':<24} {'SLUG':<20} DOMAIN")
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
            raise CliError(f"Container does not exist: {names.container}", 2)
        print(f"container: {names.container}")
        print(f"status:    {'running' if docker.container_running(names.container) else 'stopped'}")
        print(f"volume:    {names.volume}")
        saved_domain = docker.label(names.container, LABEL_DOMAIN)
        if saved_domain:
            domain = saved_domain
    if not domain:
        raise CliError("Specify --name or --domain", 2)
    result = _proxy(settings, "status", "--domain", domain)
    _show_proxy_result(result)
    return 0 if result.ok else 4


def doctor(settings: StaticSettings) -> int:
    docker = _docker(settings)
    checks: list[tuple[str, bool, str]] = []
    info = docker.run("info", check=False, timeout=20)
    checks.append(("docker info", info.ok, info.text().strip()[:120]))
    checks.append(
        ("network " + settings.network, docker.network_exists(settings.network), settings.network)
    )
    image = docker.image_exists(settings.image)
    checks.append(("image " + settings.image, image, "present" if image else "will be pulled"))
    proxy_local = settings.proxy_script.is_file()
    remote = settings.transport == "ssh" or settings.remote_repo is not None
    checks.append(
        (
            "proxy_domain.py",
            proxy_local or remote,
            str(settings.proxy_script) + (" (remote)" if remote and not proxy_local else ""),
        )
    )
    for label, ok, detail in checks:
        print(f"[{'ok' if ok else 'FAIL'}] {label}: {detail}")
    if proxy_local or remote:
        print()
        result = _proxy(settings, "doctor")
        _show_proxy_result(result)
        return 0 if result.ok else 4
    return 0 if all(ok for _, ok, _ in checks) else 3
