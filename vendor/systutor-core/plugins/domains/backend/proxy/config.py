"""Configuración compartida por el CLI y la API del plugin de dominios."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def _detect_repo_root() -> Path:
    """Raíz del repo cuando el plugin se usa desde el árbol de desarrollo."""
    for parent in _PACKAGE_DIR.parents:
        if (parent / "scripts" / "traefik-lab.sh").is_file():
            return parent
    return Path.cwd()


REPO_ROOT = (
    Path(os.environ["SPANEL_PROXY_REPO_ROOT"]).expanduser()
    if os.environ.get("SPANEL_PROXY_REPO_ROOT")
    else _detect_repo_root()
)
SCRIPTS_DIR = REPO_ROOT / "scripts"
LAB_ROOT = SCRIPTS_DIR / "traefik-lab"
PROFILES = ("internal", "public")
TRANSPORTS = ("auto", "local", "ssh", "docker")


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
    # Dónde se sondea el certificado cuando el proceso corre en un contenedor:
    # el host Docker o el nombre del Traefik en la red `proxy`.
    probe_address: str | None = None
    probe_timeout: float = 5.0
    # HOSTNAME del contenedor propio, capturado del env efectivo. La detección
    # no debe leer os.environ por su cuenta: eso vuelve los tests dependientes
    # del shell que los ejecuta.
    own_hostname: str | None = None
    # A.SPEC 0029: selección de transporte y overrides crudos. `config_dir`
    # sigue siendo el directorio local/remoto de 0027; `config_dir_override`
    # recuerda si el operador lo definió (para el transporte docker es la ruta
    # dentro de Traefik).
    transport: str = "auto"
    config_dir_override: str | None = None
    cert_resolver_override: str | None = None
    traefik_container_override: str | None = None

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
        config_dir_override = _env(env, "SPANEL_PROXY_CONFIG_DIR")
        transport = (_env(env, "SPANEL_PROXY_TRANSPORT", default="auto") or "auto").lower()
        if transport not in TRANSPORTS:
            raise ValueError(
                f"transporte desconocido: {transport} (usá {' o '.join(TRANSPORTS)})"
            )
        config_dir = _path(config_dir_override)
        if config_dir is None:
            config_dir = LAB_ROOT / "dynamic" if internal else Path("/opt/stacks/traefik/config")
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
                env,
                "SPANEL_PROXY_SSH_PASSWORD",
                "SPANEL_DOCKER_SSH_PASSWORD",
                # Mismo criterio que Hosting: es la misma máquina y el mismo
                # secreto; si no, el transporte SSH falla en silencio.
                "MAIL_SERVER_PASSWORD",
            ),
            probe_address=_env(env, "SPANEL_PROXY_PROBE_HOST"),
            probe_timeout=float(_env(env, "SPANEL_PROXY_PROBE_TIMEOUT", default="5") or "5"),
            own_hostname=_env(env, "SPANEL_PROXY_HOSTNAME", "HOSTNAME"),
            transport=transport,
            config_dir_override=config_dir_override,
            cert_resolver_override=_env(env, "SPANEL_PROXY_CERT_RESOLVER"),
            traefik_container_override=_env(env, "SPANEL_PROXY_TRAEFIK_CONTAINER"),
            wp_image=_env(env, "SPANEL_PROXY_WP_IMAGE", default="wordpress:cli") or "wordpress:cli",
            wp_root=_env(env, "SPANEL_PROXY_WP_ROOT", default="/var/www/html")
            or "/var/www/html",
            wait_seconds=int(_env(env, "SPANEL_PROXY_WAIT", default="60") or "60"),
        )
