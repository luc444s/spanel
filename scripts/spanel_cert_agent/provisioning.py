"""Provisioning automático para solicitudes de certificados.

Cuando el panel solicita un hostname válido, el agente puede añadirlo al
allowlist y asegurar el router HTTP-01 de Traefik antes de ejecutar Certbot.
Esto evita que una solicitud autorizada falle por configuración estática
desincronizada en el host.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from .config import AgentConfig, AgentConfigError

ACME_ROUTER_NAME = "spanel-acme-mail-http"
ACME_SERVICE_NAME = "spanel-acme-mail-challenge"
ACME_BACKEND_URL = "http://acme-webroot:80"
ACME_ROUTER_MARKER = "# managed-by: spanel-cert-agent"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def render_allowlist(hostnames: list[str]) -> str:
    body = "\n".join(f'  "{hostname}",' for hostname in hostnames)
    return f"allowlist = [\n{body}\n]"


def upsert_allowlist(text: str, hostnames: list[str]) -> str:
    pattern = re.compile(r"(?ms)^allowlist\s*=\s*\[[^\]]*\]")
    replacement = render_allowlist(sorted(hostnames))
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + "\n\n" + replacement + "\n"


def ensure_allowlist(config: AgentConfig, hostname: str) -> AgentConfig:
    if hostname in config.allowlist:
        return config
    merged_set = frozenset({*config.allowlist, hostname})
    path = Path(config.source)
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            updated = upsert_allowlist(text, sorted(merged_set))
            if updated != text:
                _atomic_write(path, updated)
    except OSError:
        # Persistencia best-effort: el request ya fue autorizado por la API.
        pass
    return replace(config, allowlist=merged_set)


def render_acme_router(hostnames: list[str]) -> str:
    hosts = sorted({h.strip().lower() for h in hostnames if h and h.strip()})
    if not hosts:
        raise AgentConfigError("no hostnames provided for ACME routing")
    host_rule = " || ".join(f"Host(`{host}`)" for host in hosts)
    return (
        f"{ACME_ROUTER_MARKER}\n"
        "http:\n"
        "  routers:\n"
        f"    {ACME_ROUTER_NAME}:\n"
        f'      rule: "PathPrefix(`/.well-known/acme-challenge/`) && ({host_rule})"\n'
        "      entryPoints:\n"
        "        - web\n"
        f"      service: {ACME_SERVICE_NAME}\n"
        "      priority: 10000\n"
        "  services:\n"
        f"    {ACME_SERVICE_NAME}:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        f'          - url: "{ACME_BACKEND_URL}"\n'
    )


def parse_acme_router_hosts(text: str) -> list[str]:
    return sorted({m.strip().lower() for m in re.findall(r"Host\(`([^`]+)`\)", text)})


def ensure_traefik_acme(
    config: AgentConfig, hostname: str, *, reload_delay: float = 2.0
) -> bool:
    config_dir = config.traefik_config_dir
    if config_dir is None:
        return False
    if not config_dir.is_dir():
        # No Traefik file-provider dir: nothing to do here; certbot may still
        # succeed if routing is handled elsewhere.
        return False
    path = config_dir / config.traefik_acme_router_file
    existing_hosts: list[str] = []
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if ACME_ROUTER_MARKER in text:
            existing_hosts = parse_acme_router_hosts(text)
    merged = sorted({*existing_hosts, hostname})
    content = render_acme_router(merged)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    _atomic_write(path, content)
    if reload_delay > 0:
        time.sleep(reload_delay)
    return True


def provision_hostname(
    config: AgentConfig, hostname: str, *, reload_delay: float = 2.0
) -> AgentConfig:
    """Asegura allowlist y routing ACME para un hostname solicitado."""

    if hostname == config.default_name:
        raise AgentConfigError("The global certificate is not regenerated")
    updated = ensure_allowlist(config, hostname)
    ensure_traefik_acme(updated, hostname, reload_delay=reload_delay)
    return updated
