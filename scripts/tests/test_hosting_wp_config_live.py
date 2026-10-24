"""A.SPEC 0018: read a real `wp-config.php` without changing the site.

The test is read-only. It selects an existing container with
`SPANEL_WP_CONFIG_CONTAINER`, reads its file with the production reader and
parses the four constants. It never starts, stops, recreates or deletes the
container, never executes PHP and never writes to the site volume.

Run with:

    SPANEL_LIVE_TESTS=1 \
    SPANEL_WP_CONFIG_CONTAINER=<container> \
    python -m pytest scripts/tests/test_hosting_wp_config_live.py -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_core = Path(__file__).resolve().parents[2] / "vendor" / "systutor-core"
sys.path.insert(0, str(_core / "src"))
sys.path.insert(0, str(_core))

import pytest
from plugins.hosting.backend.config import env_of
from plugins.hosting.backend.inspection import inspect_container
from plugins.hosting.backend.service import database_credentials
from plugins.hosting.backend.settings import (
    docker_env,
    get_hosting_settings,
)
from plugins.hosting.backend.wpconfig import (
    credentials_from_wp_config,
    read_wp_config,
)

_DB_KEYS = (
    "WORDPRESS_DB_HOST",
    "WORDPRESS_DB_NAME",
    "WORDPRESS_DB_USER",
    "WORDPRESS_DB_PASSWORD",
)


def _container() -> str:
    if os.getenv("SPANEL_LIVE_TESTS") != "1":
        pytest.skip("GAP: SPANEL_LIVE_TESTS=1 requerido")
    container = os.getenv("SPANEL_WP_CONFIG_CONTAINER", "").strip()
    if not container:
        pytest.skip("GAP: SPANEL_WP_CONFIG_CONTAINER requerido")
    return container


def test_wp_config_fallback_reads_a_real_site() -> None:
    container = _container()
    settings = get_hosting_settings()
    env = docker_env(settings)

    info = inspect_container(env, container, timeout=settings.command_timeout)
    assert info is not None, f"el contenedor {container} no existe o no responde"

    # The point of this test is the fallback: a container that already declares
    # the four variables would not exercise it.
    if all(env_of(info).get(key) for key in _DB_KEYS):
        pytest.skip("el contenedor declara WORDPRESS_DB_*; no prueba el fallback")

    text = read_wp_config(env, container, timeout=settings.command_timeout)
    assert text is not None

    values = credentials_from_wp_config(text)
    assert values is not None, "wp-config.php no declara los cuatro DB_* literales"
    host, database, user, password = values
    assert all((host, database, user, password))

    # The same resolution the routes use: environment first, file second.
    assert database_credentials(info, wp_config=text) == (
        host.split(":", 1)[0],
        database,
        user,
        password,
    )
