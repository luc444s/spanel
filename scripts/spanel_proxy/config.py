"""Configuración del CLI de dominios y de la integración con el proxy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
LAB_ROOT = SCRIPTS_DIR / "traefik-lab"
PROFILES = ("internal", "public")


def _env(env: Mapping[str, str], *names: str, default: str | None = None) -> str | None:
    for name in names:
        value = env.get(name)
        if value not in (None, ""):
            return value
    return default


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


@dataclass(frozen=True)
class Settings:
    """Configuración efectiva de una ejecución del CLI."""

    profile: str
    config_dir: Path
    traefik_container: str
    traefik_static: Path | None
    network: str
    entrypoint_http: str
    entrypoint_https: str
    cert_resolver: str
    ca_file: Path | None
    dns_container: str
    dns_hosts_file: Path | None
    fallback_https_port: int
    fallback_http_port: int
    backups_dir: Path
    log_file: Path
    ssh_host: str | None
    ssh_user: str | None
    ssh_port: int
    ssh_password: str | None
    wp_image: str
    wp_root: str
    wait_seconds: int

    @property
    def is_internal(self) -> bool:
        return self.profile == "internal"

    @property
    def is_remote(self) -> bool:
        return bool(self.ssh_host)

    @classmethod
    def from_env(
        cls,
        profile: str = "internal",
        env: Mapping[str, str] | None = None,
    ) -> Settings:
        env = os.environ if env is None else env
        if profile not in PROFILES:
            raise ValueError(f"perfil desconocido: {profile} (usá {' o '.join(PROFILES)})")

        internal = profile == "internal"
        config_dir = _path(_env(env, "SPANEL_PROXY_CONFIG_DIR")) or (
            LAB_ROOT / "dynamic" if internal else Path("/opt/stacks/traefik/config")
        )
        traefik_static = _path(_env(env, "SPANEL_PROXY_STATIC_CONFIG")) or (
            LAB_ROOT / "traefik.yml" if internal else config_dir.parent / "traefik.yml"
        )
        ca_file = _path(_env(env, "SPANEL_PROXY_CA_FILE")) or (
            LAB_ROOT / "ca" / "root_ca.crt" if internal else None
        )

        return cls(
            profile=profile,
            config_dir=config_dir,
            traefik_container=_env(
                env,
                "SPANEL_PROXY_TRAEFIK_CONTAINER",
                default="spanel-traefik-lab" if internal else "traefik",
            )
            or "",
            traefik_static=traefik_static,
            network=_env(
                env, "SPANEL_PROXY_NETWORK", default="spanel-traefik-lab" if internal else "proxy"
            )
            or "",
            entrypoint_http=_env(env, "SPANEL_PROXY_ENTRYPOINT_HTTP", default="web") or "web",
            entrypoint_https=_env(env, "SPANEL_PROXY_ENTRYPOINT_HTTPS", default="websecure")
            or "websecure",
            cert_resolver=_env(
                env,
                "SPANEL_PROXY_CERT_RESOLVER",
                default="internal" if internal else "letsencrypt",
            )
            or "",
            ca_file=ca_file,
            dns_container=_env(env, "SPANEL_PROXY_DNS_CONTAINER", default="spanel-lab-dns")
            or "spanel-lab-dns",
            dns_hosts_file=_path(_env(env, "SPANEL_PROXY_DNS_HOSTS"))
            or (LAB_ROOT / "dns" / "hosts" if internal else None),
            fallback_https_port=int(
                _env(
                    env,
                    "SPANEL_PROXY_HTTPS_PORT",
                    default="9443" if internal else "443",
                )
                or ("9443" if internal else "443")
            ),
            fallback_http_port=int(
                _env(env, "SPANEL_PROXY_HTTP_PORT", default="9080" if internal else "80")
                or ("9080" if internal else "80")
            ),
            backups_dir=_path(_env(env, "SPANEL_PROXY_BACKUPS_DIR"))
            or (LAB_ROOT / "backups" if internal else Path("/opt/stacks/traefik/backups")),
            log_file=_path(_env(env, "SPANEL_PROXY_LOG"))
            or (REPO_ROOT / "logs" / "proxy-domain.log"),
            ssh_host=_env(env, "SPANEL_PROXY_SSH_HOST", "SPANEL_DOCKER_SSH_HOST"),
            ssh_user=_env(env, "SPANEL_PROXY_SSH_USER", "SPANEL_DOCKER_SSH_USER"),
            ssh_port=int(
                _env(env, "SPANEL_PROXY_SSH_PORT", "SPANEL_DOCKER_SSH_PORT", default="22") or "22"
            ),
            ssh_password=_env(
                env, "SPANEL_PROXY_SSH_PASSWORD", "SPANEL_DOCKER_SSH_PASSWORD"
            ),
            wp_image=_env(env, "SPANEL_PROXY_WP_IMAGE", default="wordpress:cli") or "wordpress:cli",
            wp_root=_env(env, "SPANEL_PROXY_WP_ROOT", default="/var/www/html")
            or "/var/www/html",
            wait_seconds=int(_env(env, "SPANEL_PROXY_WAIT", default="60") or "60"),
        )
