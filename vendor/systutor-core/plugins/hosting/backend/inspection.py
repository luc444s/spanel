"""The narrow Docker operations the hosting API needs.

Only `inspect` is used here. Listing sites reads the ownership rows first and
then inspects exactly those containers: a container that is not in the tenant's
list is never touched.

The WordPress verdict itself is NOT reimplemented: it is the extracted
`discovery.detectar_capa2` from A.SPEC 0007/0012.
"""

from __future__ import annotations

import json
import shlex

from .discovery import Candidato, detectar_capa2
from .transport import Fallo, build_docker_argv, run


def inspect_container(
    env: dict[str, str], container_name: str, *, timeout: int = 60
) -> dict | None:
    """`docker inspect` as data, or None when the container does not exist.

    A missing container is a normal state for the site list, not an exception.
    Any other failure is still reported through the same None: the caller
    decides whether that means "missing" (list) or 409 (users/reset).
    """
    command = f"docker inspect --format '{{{{json .}}}}' {shlex.quote(container_name)}"
    result = run(build_docker_argv(command, env=env), env=env, timeout=timeout)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    return payload if isinstance(payload, dict) else None


def candidato_de(info: dict) -> Candidato:
    """Run the extracted detector against an inspect payload.

    `discovery` expects the short `docker ps` shape (`Image`, `Labels` as a
    string). The labels are rebuilt from inspect's dictionary without changing
    the detector: `es_operable` still reads `spanel.managed` from them.
    """
    config = info.get("Config")
    config = config if isinstance(config, dict) else {}
    labels = config.get("Labels")
    labels = labels if isinstance(labels, dict) else {}
    labels_text = ",".join(f"{key}={value}" for key, value in labels.items())
    container = {
        "Image": config.get("Image") or info.get("Image") or "",
        "Labels": labels_text,
    }
    return detectar_capa2(container, info)


def database_container(info: dict) -> str:
    """The database container named by WORDPRESS_DB_HOST.

    The official WordPress image declares `db:3306` or just `db`. The host part
    is the container name used by `docker exec`; a bare IP or `localhost` is not
    a container we can exec into.
    """
    variables: dict[str, str] = {}
    config = info.get("Config")
    config = config if isinstance(config, dict) else {}
    for pair in config.get("Env") or []:
        if not isinstance(pair, str):
            continue
        key, _, value = pair.partition("=")
        if key:
            variables[key.strip()] = value.strip()

    host = variables.get("WORDPRESS_DB_HOST", "").strip()
    if not host:
        raise Fallo("el contenedor de WordPress no declara WORDPRESS_DB_HOST")
    name = host.split(":", 1)[0].strip()
    if not name or name in {"localhost", "127.0.0.1", "::1"}:
        raise Fallo(
            "WORDPRESS_DB_HOST no apunta a un contenedor de Docker "
            f"({host!r}); no se puede ejecutar SQL ahi"
        )
    return name
