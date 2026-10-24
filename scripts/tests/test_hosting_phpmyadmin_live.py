"""Opt-in read-only phpMyAdmin smoke test against an existing WordPress site."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

from plugins.hosting.backend.inspection import inspect_container
from plugins.hosting.backend.phpmyadmin_proxy import PmaClient, proxy_response
from plugins.hosting.backend.service import database_credentials
from plugins.hosting.backend.settings import docker_env, get_hosting_settings
from plugins.hosting.backend.transport import build_docker_argv, run
from plugins.hosting.backend.wpconfig import read_wp_config


def test_existing_phpmyadmin_without_mutating_docker() -> None:
    if os.getenv("SPANEL_LIVE_TESTS") != "1":
        pytest.skip("SPANEL_LIVE_TESTS=1 requerido")
    container = os.getenv("SPANEL_PHPMYADMIN_TEST_CONTAINER", "").strip()
    if not container:
        pytest.skip("SPANEL_PHPMYADMIN_TEST_CONTAINER requerido")
    settings = get_hosting_settings()
    env = docker_env(settings)

    def snapshot() -> tuple[str, str, str]:
        results = []
        for command in ("docker ps -aq", "docker volume ls -q", "docker network ls -q"):
            result = run(build_docker_argv(command, env=env), env=env)
            assert result.returncode == 0
            results.append(result.stdout)
        return tuple(results)  # type: ignore[return-value]

    before = snapshot()
    try:
        info = inspect_container(env, container, timeout=settings.command_timeout)
        assert info is not None
        wp_config = read_wp_config(env, container, timeout=settings.command_timeout)
        _, database, username, password = database_credentials(
            info, wp_config=wp_config
        )
        client = PmaClient(
            settings.phpmyadmin_internal_url, settings.phpmyadmin_timeout
        )
        cookies = client.login(username, password)
        response, _, expired = proxy_response(
            client, "GET", "", "", b"", {}, cookies, "", "/proxy"
        )
        assert not expired and response.status_code == 200
        body = response.body.decode("utf-8", errors="replace")
        assert database in body
        assert "wp_lucas" not in body
        # phpMyAdmin displays its configured origin as plain text in <title>;
        # that isn't a navigable URL. Links and scripts must remain proxied.
        navigable = re.sub(
            r"<title\b[^>]*>.*?</title>", "", body, flags=re.IGNORECASE | re.DOTALL
        )
        assert not re.search(
            r"https?://(?:127\.0\.0\.1|localhost|spanel-phpmyadmin)(?::\d+)?/",
            navigable,
            flags=re.IGNORECASE,
        )
        asset = re.search(
            r'(?:src|href)="(?:\./)?([^"?]+\.(?:css|js))(?:\?[^\"]*)?"', body
        )
        assert asset is not None, "el dashboard no enlaza un CSS/JS comprobable"
        _, upstream = client.request("GET", asset.group(1), cookies=cookies)
        assert upstream.status == 200
        assert upstream.getheader("Content-Type", "").split(";", 1)[0] in (
            "text/css",
            "application/javascript",
            "text/javascript",
        )
        upstream.read()
        assert 'href="index.php?route=/server/status"' in body
        status, _, expired = proxy_response(
            client, "GET", "index.php", "route=/server/status", b"", {}, cookies, "", "/proxy"
        )
        assert not expired and status.status_code == 200
    finally:
        assert snapshot() == before
