"""Detección pura del transporte del proxy (A.SPEC 0029).

Nada de este módulo escribe ni muta contenedores: sólo ejecuta lecturas a
través del `Runner` inyectado. La detección se resuelve una vez y el resultado
es inmutable, para que una operación nunca cambie de contenedor ni de host a
mitad de camino.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import PROFILES, Settings
from .core import EXIT_TRANSPORT, ProxyError

DEFAULT_CONFIG_DIR = "/config"
STATIC_CONFIG_CANDIDATES = ("/traefik.yml", "/etc/traefik/traefik.yml")
CONTAINER_PREFERENCES = ("traefik", "spanel-traefik-lab")
DIRECTORY_ARG = "--providers.file.directory"


@dataclass(frozen=True)
class Detection:
    """Resultado inmutable de la detección de transporte."""

    transport: str
    reason: str
    config_dir: Path
    traefik_container: str | None = None
    probe_host: str | None = None
    probe_port: int | None = None
    profile: str | None = None
    cert_resolver: str | None = None
    resolvers: tuple[str, ...] = ()
    checks: tuple[str, ...] = field(default_factory=tuple)


def normalize_dir(value: str | Path) -> str:
    """Ruta normalizada sin `/` final (el mount y el destino deben coincidir)."""
    text = str(value).strip()
    while len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text


def _run(runner, argv: Sequence[str]):
    """Ejecuta el runner tratando un binario ausente como fallo de lectura."""
    try:
        return runner.run(list(argv))
    except OSError as exc:
        raise ProxyError(f"docker is not available: {exc}", EXIT_TRANSPORT) from exc


# ---- lectura del daemon ----------------------------------------------------


def docker_available(runner) -> bool:
    try:
        return _run(runner, ["docker", "info"]).ok
    except ProxyError:
        return False


def parse_ps(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        name, _, image = line.partition("\t")
        name = name.strip()
        if name:
            rows.append((name, image.strip()))
    return rows


def choose_container(rows: Sequence[tuple[str, str]], override: str | None) -> str:
    """Nombre del Traefik: override explícito, único, o preferencia conocida."""
    if override:
        return override
    candidates = list(
        dict.fromkeys(name for name, image in rows if "traefik" in image.lower())
    )
    if not candidates:
        raise ProxyError(
            "no Traefik container found; set SPANEL_PROXY_TRAEFIK_CONTAINER",
            EXIT_TRANSPORT,
        )
    if len(candidates) == 1:
        return candidates[0]
    for preference in CONTAINER_PREFERENCES:
        if preference in candidates:
            return preference
    raise ProxyError(
        "ambiguous Traefik containers: "
        f"{', '.join(candidates)}; set SPANEL_PROXY_TRAEFIK_CONTAINER",
        EXIT_TRANSPORT,
    )


def discover_container(runner, override: str | None) -> str:
    if override:
        return override
    result = _run(runner, ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    if not result.ok:
        raise ProxyError(
            f"docker ps failed: {result.text().strip() or 'no details'}", EXIT_TRANSPORT
        )
    return choose_container(parse_ps(result.stdout), None)


def container_running(runner, container: str) -> bool:
    result = _run(
        runner, ["docker", "inspect", "-f", "{{.State.Running}}", container]
    )
    return result.ok and result.stdout.strip() == "true"


def read_static_config(runner, container: str) -> str | None:
    """Config estática leída dentro del contenedor; nunca del filesystem host."""
    for path in STATIC_CONFIG_CANDIDATES:
        result = _run(runner, ["docker", "exec", container, "cat", path])
        if result.ok and result.stdout.strip():
            return result.stdout
    return None


def parse_static_config(text: str | None) -> tuple[str | None, tuple[str, ...]]:
    if not text:
        return None, ()
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None, ()
    if not isinstance(data, dict):
        return None, ()
    provider = (data.get("providers") or {}).get("file") or {}
    directory = provider.get("directory") if isinstance(provider, dict) else None
    resolvers = data.get("certificatesResolvers") or {}
    names = tuple(str(name) for name in resolvers) if isinstance(resolvers, dict) else ()
    return (str(directory) if directory else None), names


def container_args(runner, container: str) -> list[str]:
    result = _run(
        runner, ["docker", "inspect", "--format", "{{json .Args}}", container]
    )
    if not result.ok:
        return []
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def directory_from_args(args: Sequence[str]) -> str | None:
    for index, arg in enumerate(args):
        if arg.startswith(f"{DIRECTORY_ARG}="):
            return arg.split("=", 1)[1] or None
        if arg == DIRECTORY_ARG and index + 1 < len(args):
            return args[index + 1] or None
    return None


def container_mounts(runner, container: str) -> list[dict]:
    result = _run(
        runner, ["docker", "inspect", "--format", "{{json .Mounts}}", container]
    )
    if not result.ok:
        raise ProxyError(
            f"could not read mounts for {container}: {result.text().strip()}",
            EXIT_TRANSPORT,
        )
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ProxyError(
            f"unreadable mounts for {container}: {exc}", EXIT_TRANSPORT
        ) from exc
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def destination_mount(mounts: Sequence[dict], destination: str) -> dict:
    target = normalize_dir(destination)
    matches = [
        mount
        for mount in mounts
        if normalize_dir(mount.get("Destination") or "") == target
    ]
    if not matches:
        raise ProxyError(
            f"no mount at {target} inside the container; "
            "set SPANEL_PROXY_CONFIG_DIR to the path inside Traefik",
            EXIT_TRANSPORT,
        )
    if len(matches) > 1:
        raise ProxyError(
            f"more than one mount at {target}; "
            "set SPANEL_PROXY_CONFIG_DIR to the path inside Traefik",
            EXIT_TRANSPORT,
        )
    mount = matches[0]
    if mount.get("RW") is False:
        raise ProxyError(f"the mount at {target} is read-only", EXIT_TRANSPORT)
    return mount


def writable_dir(runner, container: str, destination: str) -> bool:
    result = _run(runner, ["docker", "exec", container, "test", "-w", destination])
    return result.ok


def container_networks(runner, container: str) -> set[str]:
    result = _run(
        runner,
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container],
    )
    if not result.ok:
        return set()
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return set()
    return set(data) if isinstance(data, dict) else set()


def container_gateway(runner, container: str) -> str | None:
    """Gateway del contenedor propio: la dirección del host desde su red."""
    result = _run(
        runner,
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.Gateway}}{{println}}{{end}}",
            container,
        ],
    )
    if result is None or not result.ok:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return None


def published_port(runner, container: str, port: int = 443) -> int | None:
    result = _run(runner, ["docker", "port", container, f"{port}/tcp"])
    if not result.ok:
        return None
    for line in result.stdout.splitlines():
        _, _, host_side = line.strip().rpartition(":")
        if host_side.isdigit():
            return int(host_side)
    return None


def parse_published(text: str) -> int | None:
    for line in (text or "").splitlines():
        _, _, host_side = line.strip().rpartition(":")
        if host_side.isdigit():
            return int(host_side)
    return None


# ---- perfil y resolver -----------------------------------------------------


def detect_profile(
    settings: Settings,
    resolvers: Sequence[str],
    env: Mapping[str, str],
) -> str:
    override = env.get("SPANEL_PROXY_PROFILE") or env.get("SPANEL_DOMAINS_PROFILE")
    if override:
        if override not in PROFILES:
            raise ProxyError(
                f"invalid profile: {override} (use {' or '.join(PROFILES)})",
                EXIT_VALIDATION,
            )
        return override
    if "internal" in resolvers and "letsencrypt" not in resolvers:
        return "internal"
    return "public"


def detect_resolver(profile: str, env: Mapping[str, str]) -> str:
    override = env.get("SPANEL_PROXY_CERT_RESOLVER")
    if override:
        return override
    return "internal" if profile == "internal" else "letsencrypt"


# ---- selección -------------------------------------------------------------


def _local_viable(settings: Settings) -> tuple[bool, str]:
    if not settings.config_dir_override:
        return False, "SPANEL_PROXY_CONFIG_DIR is not set"
    target = settings.config_dir
    if target.is_dir() and os.access(target, os.W_OK):
        return True, f"{target} is writable"
    parent = target.parent
    if parent.is_dir() and os.access(parent, os.W_OK):
        # atomic_write crea el directorio; el padre escribible alcanza.
        return True, f"{target} will be created in {parent}"
    return False, f"{target} does not exist and is not writable"


def _local(settings: Settings, reason: str) -> Detection:
    return Detection(
        transport="local",
        reason=reason,
        config_dir=settings.config_dir,
        probe_host=settings.probe_address or settings.ssh_host or "127.0.0.1",
        profile=settings.profile,
        cert_resolver=settings.cert_resolver,
    )


def _ssh(settings: Settings, reason: str) -> Detection:
    return Detection(
        transport="ssh",
        reason=reason,
        config_dir=settings.config_dir,
        probe_host=settings.probe_address or settings.ssh_host or "127.0.0.1",
        profile=settings.profile,
        cert_resolver=settings.cert_resolver,
    )


def detect_docker(
    settings: Settings,
    runner,
    env: Mapping[str, str] | None = None,
) -> Detection:
    """Detecta el Traefik, su file provider y cómo sondearlo. Sólo lecturas."""
    env = os.environ if env is None else env
    if not docker_available(runner):
        raise ProxyError("docker is not responding", EXIT_TRANSPORT)

    container = discover_container(runner, settings.traefik_container_override)
    if not container_running(runner, container):
        raise ProxyError(
            f"container {container} is not running", EXIT_TRANSPORT
        )

    static_text = read_static_config(runner, container)
    static_dir, resolvers = parse_static_config(static_text)
    if static_dir is None:
        static_dir = directory_from_args(container_args(runner, container))
    destination = settings.config_dir_override or static_dir or DEFAULT_CONFIG_DIR

    mount = destination_mount(container_mounts(runner, container), destination)
    destination = normalize_dir(mount.get("Destination") or destination)
    if not writable_dir(runner, container, destination):
        raise ProxyError(
            f"directory {destination} inside {container} is not writable",
            EXIT_TRANSPORT,
        )

    profile = detect_profile(settings, resolvers, env)
    resolver = detect_resolver(profile, env)

    networks = container_networks(runner, container)
    shared: set[str] = set()
    own = settings.own_hostname or env.get("HOSTNAME", "")
    if networks and own:
        shared = networks & container_networks(runner, own)

    if shared:
        probe_host, probe_port = container, 443
        probe_reason = f"shared network with {container}"
    elif settings.probe_address:
        probe_host = settings.probe_address
        probe_port = published_port(runner, container) or settings.fallback_https_port
        probe_reason = "SPANEL_PROXY_PROBE_HOST"
    else:
        published = published_port(runner, container)
        if not published:
            probe_host, probe_port = None, None
            probe_reason = "no shared network and no port 443"
        elif own and (gateway := container_gateway(runner, own)):
            # El proceso vive en un contenedor sin red compartida: 127.0.0.1
            # sería él mismo. El gateway del propio contenedor es el host.
            probe_host, probe_port = gateway, published
            probe_reason = f"gateway {gateway} and published port {published}"
        elif own:
            probe_host, probe_port = None, None
            probe_reason = "container with no shared network and no reachable gateway"
        else:
            probe_host, probe_port = "127.0.0.1", published
            probe_reason = f"published port {published}"

    reason = f"container {container}, {destination} is writable"
    if not shared:
        reason = f"{reason}; probing via {probe_reason}"
    return Detection(
        transport="docker",
        reason=reason,
        config_dir=Path(destination),
        traefik_container=container,
        probe_host=probe_host,
        probe_port=probe_port,
        profile=profile,
        cert_resolver=resolver,
        resolvers=resolvers,
        checks=(probe_reason,),
    )


def select_transport(
    settings: Settings,
    runner,
    env: Mapping[str, str] | None = None,
) -> Detection:
    """Resuelve el transporte según `SPANEL_PROXY_TRANSPORT`.

    `auto` conserva el orden documentado: SSH configurado, Docker, local.
    Un modo explícito falla con su propio error y nunca cae a otro.
    """
    env = os.environ if env is None else env
    mode = settings.transport

    if mode == "ssh":
        if not settings.ssh_host:
            raise ProxyError(
                "ssh transport forced but SPANEL_PROXY_SSH_HOST is missing",
                EXIT_TRANSPORT,
            )
        return _ssh(settings, "SPANEL_PROXY_SSH_HOST is set")
    if mode == "local":
        return _local(settings, f"local mode forced ({settings.config_dir})")
    if mode == "docker":
        return detect_docker(settings, runner, env)

    checks: list[str] = []
    if settings.ssh_host:
        return _ssh(settings, "SPANEL_PROXY_SSH_HOST is set")
    checks.append("ssh: SPANEL_PROXY_SSH_HOST is not set")

    try:
        return detect_docker(settings, runner, env)
    except ProxyError as exc:
        checks.append(f"docker: {exc}")

    viable, detail = _local_viable(settings)
    if viable:
        return _local(settings, detail)
    checks.append(f"local: {detail}")

    raise ProxyError(
        "no proxy transport is available (" + "; ".join(checks) + ")",
        EXIT_TRANSPORT,
    )
