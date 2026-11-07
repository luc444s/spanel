"""Resolve the WordPress database binding without touching the site.

The environment is the first source. When it does not declare the four
`WORDPRESS_DB_*` values, the caller passes the text of `wp-config.php` and the
literal `DB_*` constants are used instead. This module owns the source order and
the host validation; `wpconfig.py` owns reading and parsing.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from systutor.core.errors import AppError

from .config import env_of
from .database import resolver_alias_de_contenedor
from .wpconfig import credentials_from_wp_config, read_wp_config

# A Docker network alias: the WordPress env names its database container, and
# phpMyAdmin resolves that same name. A scheme, a path or an IP is not a name
# this plugin will send anybody to.
_DOCKER_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


def _validate_host(host: str) -> str:
    """The Docker container part of a database host, with the port removed.

    A scheme, `localhost` and an IP are rejected because phpMyAdmin cannot
    resolve them and the panel will not send a credential to something that is
    not the site's database container. `mariadb:3306` is accepted; the port is
    not part of the container name.
    """
    if "://" in host:
        raise AppError(
            f"El host de la base de datos no apunta a un contenedor ({host!r})",
            status_code=409,
            code="hosting_database_unavailable",
        )
    name = host.split(":", 1)[0].strip()
    if not _DOCKER_NAME.fullmatch(name) or name in {"localhost", "127.0.0.1", "::1"}:
        raise AppError(
            f"El host de la base de datos no apunta a un contenedor ({host!r})",
            status_code=409,
            code="hosting_database_unavailable",
        )
    return name


def database_binding(
    info: dict, *, wp_config: str | None = None
) -> tuple[str, str, str, str, str]:
    """Resolve the database from the environment, then from `wp-config.php`.

    Returns the raw host as WordPress declares it (port included), the Docker
    container name, the database, the user and the password. The environment is
    the first source: a complete one is never overridden by the file.
    """
    env = env_of(info)
    host = env.get("WORDPRESS_DB_HOST", "").strip()
    database = env.get("WORDPRESS_DB_NAME", "").strip()
    user = env.get("WORDPRESS_DB_USER", "").strip()
    password = env.get("WORDPRESS_DB_PASSWORD", "").strip()
    if all((host, database, user, password)):
        return host, _validate_host(host), database, user, password

    if wp_config is not None:
        parsed = credentials_from_wp_config(wp_config)
        if parsed is not None:
            raw_host, database, user, password = parsed
            return raw_host, _validate_host(raw_host), database, user, password
        raise AppError(
            "El sitio no declara su base de datos en el entorno ni en wp-config.php",
            status_code=409,
            code="hosting_database_unavailable",
        )

    raise AppError(
        "El sitio no declara su base de datos en el entorno",
        status_code=409,
        code="hosting_database_unavailable",
    )


def database_credentials(
    info: dict, *, wp_config: str | None = None
) -> tuple[str, str, str, str]:
    """The four values WordPress already uses, validated as a Docker target."""
    _raw_host, host, database, user, password = database_binding(
        info, wp_config=wp_config
    )
    return host, database, user, password


def resolve_database_binding(
    env: dict[str, str],
    container_name: str,
    info: dict,
    *,
    inspector: Callable[[str], dict | None],
    timeout: int = 60,
    read_config: Callable[..., str | None] = read_wp_config,
    resolve_alias: Callable[[dict[str, str], dict, str], str | None] = (
        resolver_alias_de_contenedor
    ),
) -> tuple[str, str, str, str, str]:
    """Resolve a site's database the same way the panel does.

    Environment first, `wp-config.php` second, and finally the network alias
    is mapped to the real container name. Returns the raw host (port
    included), the Docker container, database, user and password.
    """
    try:
        raw_host, host, database, user, password = database_binding(info)
    except AppError:
        wp_config = read_config(env, container_name, timeout=timeout)
        if wp_config is None:
            raise
        raw_host, host, database, user, password = database_binding(
            info, wp_config=wp_config
        )
    if inspector(host) is None:
        resolved = resolve_alias(env, info, host)
        if resolved is not None:
            host = resolved
    return raw_host, host, database, user, password


__all__ = [
    "database_binding",
    "database_credentials",
    "resolve_database_binding",
]
