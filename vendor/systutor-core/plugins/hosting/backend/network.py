"""Pure Docker network decisions for the multi-database phpMyAdmin hub.

This module only reads an `inspect` payload and builds the arguments of a
`docker network connect`. It never runs a command, never imports the transport
and never holds a credential, so the "should we connect?" rule is testable
without a Docker daemon.

The caller owns the side effect: `service.py` for the login path and
`provisioner.py` for a new site. Both connect only the database container that
was already validated as a Docker name by `database_binding`.
"""

from __future__ import annotations

import re

# Docker accepts letters, digits, `_`, `.` and `-`; a leading `-` would be read
# as an option by the CLI, so it is not allowed either.
_NETWORK_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")


def validate_network_name(value: str) -> str:
    """The configured network name, or `ValueError` when it is not a name.

    A URL (`tcp://...`), a name with spaces or an empty value never becomes an
    argument of `docker network connect`.
    """
    name = value.strip()
    if not _NETWORK_NAME.fullmatch(name):
        raise ValueError(f"Nombre de red Docker inválido: {value!r}")
    return name


def validate_container_name(value: str) -> str:
    """The container part of a database host, with no scheme or port."""
    name = value.strip()
    if not _CONTAINER_NAME.fullmatch(name):
        raise ValueError(f"Nombre de contenedor inválido: {value!r}")
    return name


def networks_of(info: dict | None) -> dict[str, dict]:
    """The networks an inspect payload reports, keyed by name.

    Missing or malformed payloads are an empty mapping: a container that is not
    reachable reports no network, which is exactly what the decision needs.
    """
    if not isinstance(info, dict):
        return {}
    settings = info.get("NetworkSettings")
    networks = settings.get("Networks") if isinstance(settings, dict) else None
    if not isinstance(networks, dict):
        return {}
    return {
        str(name): endpoint if isinstance(endpoint, dict) else {}
        for name, endpoint in networks.items()
    }


def needs_connection(info: dict | None, network: str) -> bool:
    """True only while the container is not attached to `network`."""
    return network.strip() not in networks_of(info)


def multi_database_enabled(arbitrary: bool, network: str) -> bool:
    """Both settings are required; one alone keeps the fixed-server behavior."""
    return bool(arbitrary and network.strip())


def connect_args(network: str, container: str) -> tuple[str, ...]:
    """The Docker argv tail that attaches one database container to the network."""
    return (
        "network",
        "connect",
        validate_network_name(network),
        validate_container_name(container),
    )


__all__ = [
    "connect_args",
    "multi_database_enabled",
    "needs_connection",
    "networks_of",
    "validate_container_name",
    "validate_network_name",
]
