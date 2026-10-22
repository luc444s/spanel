"""Configuration for the hosting plugin.

The Docker transport is shared with the scripts extracted in A.SPEC 0012, so
the variable names are the same. `SPANEL_DOCKER_SSH_PASSWORD` falls back to
`MAIL_SERVER_PASSWORD` because both point at the same machine.

No secret is logged here, and this object is never returned by a route.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_BACKEND_DIR = Path(__file__).resolve().parent
_CORE_ROOT = _BACKEND_DIR.parents[2]
_ENV_CANDIDATES = (
    _CORE_ROOT / ".env",
    _CORE_ROOT.parent / ".env",
    _CORE_ROOT.parent.parent / ".env",
)


def _load_env_files() -> None:
    """Fill missing variables from the first env files that exist.

    `os.environ` wins: production and the tests set variables explicitly, and
    a file must never overwrite them.
    """
    for path in _ENV_CANDIDATES:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip())


class HostingSettings(BaseModel):
    docker_transport: Literal["ssh", "local"] = "ssh"
    docker_ssh_host: str = ""
    docker_ssh_user: str = ""
    docker_ssh_port: int = 22
    docker_ssh_strict_host_key: str = "yes"
    docker_ssh_password: str = ""
    command_timeout: int = 60
    wp_image: str = "wordpress:latest"
    db_image: str = "mariadb:11"
    network: str = "proxy"
    traefik: bool = True
    install_timeout: int = 180
    log_lines: int = 200


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def register_hosting_settings() -> HostingSettings:
    _load_env_files()
    transport = os.getenv("SPANEL_DOCKER_TRANSPORT", "ssh").strip().lower()
    settings = HostingSettings(
        docker_transport="local" if transport == "local" else "ssh",
        docker_ssh_host=os.getenv("SPANEL_DOCKER_SSH_HOST", "").strip(),
        docker_ssh_user=os.getenv("SPANEL_DOCKER_SSH_USER", "").strip(),
        docker_ssh_port=_int_env("SPANEL_DOCKER_SSH_PORT", 22),
        docker_ssh_strict_host_key=os.getenv("SPANEL_DOCKER_SSH_STRICT_HOST_KEY", "yes").strip(),
        # One secret, two names: the SSH password of the Docker machine is the
        # same one the mail plugin uses.
        docker_ssh_password=(
            os.getenv("SPANEL_DOCKER_SSH_PASSWORD", "").strip()
            or os.getenv("MAIL_SERVER_PASSWORD", "").strip()
        ),
        command_timeout=_int_env("SPANEL_HOSTING_COMMAND_TIMEOUT", 60),
        wp_image=os.getenv("SPANEL_HOSTING_WP_IMAGE", "wordpress:latest"),
        db_image=os.getenv("SPANEL_HOSTING_DB_IMAGE", "mariadb:11"),
        network=os.getenv("SPANEL_HOSTING_NETWORK", "proxy"),
        traefik=os.getenv("SPANEL_HOSTING_TRAEFIK", "true").lower() not in {"false", "0", "no"},
        install_timeout=_int_env("SPANEL_HOSTING_INSTALL_TIMEOUT", 180),
        log_lines=min(1000, max(1, _int_env("SPANEL_HOSTING_LOG_LINES", 200))),
    )
    _hosting_settings = settings
    return settings


_hosting_settings: HostingSettings | None = None


@lru_cache
def get_hosting_settings() -> HostingSettings:
    if _hosting_settings is None:
        return register_hosting_settings()
    return _hosting_settings


def docker_env(settings: HostingSettings | None = None) -> dict[str, str]:
    """The dictionary `transport.build_docker_argv` understands.

    `build_docker_argv` reads `MAIL_SERVER_PASSWORD` for `sshpass -e`; the
    password still travels in the child environment, never in the argv.
    """
    resolved = settings or get_hosting_settings()
    return {
        "SPANEL_DOCKER_TRANSPORT": resolved.docker_transport,
        "SPANEL_DOCKER_SSH_HOST": resolved.docker_ssh_host,
        "SPANEL_DOCKER_SSH_USER": resolved.docker_ssh_user,
        "SPANEL_DOCKER_SSH_PORT": str(resolved.docker_ssh_port),
        "SPANEL_DOCKER_SSH_STRICT_HOST_KEY": resolved.docker_ssh_strict_host_key,
        "SPANEL_DOCKER_SSH_PASSWORD": resolved.docker_ssh_password,
        "SPANEL_HOSTING_COMMAND_TIMEOUT": str(resolved.command_timeout),
        "MAIL_SERVER_PASSWORD": resolved.docker_ssh_password,
    }
