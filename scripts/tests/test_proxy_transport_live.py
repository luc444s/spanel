"""Live suite for A.SPEC 0029: transporte Docker tipo producción.

Opt-in: `SPANEL_LIVE_TESTS=1`. Simula el escenario real: el proceso no ve el
directorio del file provider; sólo tiene el socket de Docker y le pide al
contenedor de Traefik que escriba en su propio volumen.

Por defecto crea un Traefik aislado (`spanel-lab-traefik-0029`) con un volumen
de prueba escribible, porque el Traefik del laboratorio existente monta su
directorio en solo lectura. `SPANEL_LIVE_TRAEFIK=<nombre>` permite usar uno ya
existente y escribible. No toca el Traefik del laboratorio ni producción.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CORE = RAIZ / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE))

from plugins.domains.backend.proxy import (  # noqa: E402
    ProxyError,
    ProxyOperations,
    Settings,
    build_runner,
    render_site_yaml,
)
from plugins.domains.backend.proxy.detect import detect_docker  # noqa: E402

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
EXISTING = os.environ.get("SPANEL_LIVE_TRAEFIK", "")
TEST_CONTAINER = os.environ.get("SPANEL_LIVE_TRAEFIK_TEST", "spanel-lab-traefik-0029")
TEST_VOLUME = os.environ.get("SPANEL_LIVE_TRAEFIK_VOLUME", "spanel-lab-0029-dynamic")
IMAGE = os.environ.get("SPANEL_LIVE_TRAEFIK_IMAGE", "traefik:v3.7.8")
NETWORK = os.environ.get("SPANEL_LIVE_TRAEFIK_NETWORK", "spanel-traefik-lab")
EXTRA = os.environ.get("SPANEL_LIVE_DOMAIN_EXTRA", "lab-extra.spanel")

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="test live: requiere SPANEL_LIVE_TESTS=1 y el laboratorio arriba",
)


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _live_settings(container: str) -> Settings:
    # Como la app de producción: sin config dir visible, sólo Docker.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SPANEL_PROXY_")
    }
    env.update(
        {
            "SPANEL_PROXY_TRANSPORT": "docker",
            "SPANEL_PROXY_TRAEFIK_CONTAINER": container,
            "SPANEL_PROXY_LOG": str(RAIZ / "logs" / "proxy-transport-live.log"),
            "SPANEL_PROXY_WAIT": "0",
        }
    )
    return Settings.from_env("internal", env=env)


def _running(name: str) -> bool:
    state = _docker("inspect", "-f", "{{.State.Running}}", name)
    return state.returncode == 0 and state.stdout.strip() == "true"


@pytest.fixture()
def lab():
    if not LIVE:
        pytest.skip("live")
    info = _docker("info")
    if not info.returncode == 0:
        pytest.skip("docker no disponible")

    if EXISTING:
        if not _running(EXISTING):
            pytest.skip(f"{EXISTING} no está running")
        yield EXISTING
        return

    _docker("rm", "-f", TEST_CONTAINER)
    _docker("volume", "rm", TEST_VOLUME)
    volume = _docker("volume", "create", TEST_VOLUME)
    if volume.returncode != 0:
        pytest.skip(f"no se pudo crear el volumen: {volume.stderr.strip()}")
    started = _docker(
        "run",
        "-d",
        "--name",
        TEST_CONTAINER,
        "--network",
        NETWORK,
        "--entrypoint",
        "traefik",
        "-v",
        f"{TEST_VOLUME}:/config",
        IMAGE,
        "--providers.file.directory=/config",
        "--providers.file.watch=true",
        "--entryPoints.websecure.address=:443",
        "--log.level=ERROR",
    )
    if started.returncode != 0:
        _docker("volume", "rm", TEST_VOLUME)
        pytest.skip(f"no se pudo iniciar Traefik de prueba: {started.stderr.strip()}")
    try:
        yield TEST_CONTAINER
    finally:
        _docker("rm", "-f", TEST_CONTAINER)
        _docker("volume", "rm", TEST_VOLUME)


def test_detects_docker_without_visible_config_dir(lab: str) -> None:
    settings = _live_settings(lab)
    assert not settings.config_dir.exists(), "el escenario exige no ver el bind mount"
    detection = detect_docker(settings, build_runner(settings))
    assert detection.transport == "docker"
    assert detection.traefik_container == lab
    assert detection.config_dir.is_absolute()


def test_write_and_delete_reach_the_traefik_bind_mount(lab: str) -> None:
    settings = _live_settings(lab)
    operations = ProxyOperations(settings, build_runner(settings))
    slug = "lab-extra"
    content = render_site_yaml("spanel-lab-domains-wp", [EXTRA], "internal")

    detection = operations.selection
    target = detection.config_dir / f"spanel-{slug}.yml"

    before = _docker("inspect", "-f", "{{.State.StartedAt}}", lab).stdout.strip()
    operations._write_text(target, content)
    try:
        inside = _docker("exec", lab, "cat", str(target))
        assert inside.returncode == 0, inside.stderr
        assert inside.stdout == content
        assert operations.files.read_text(target) == content
    finally:
        operations._delete_file(target)
    assert not operations.files.exists(target)
    after = _docker("inspect", "-f", "{{.State.StartedAt}}", lab).stdout.strip()
    assert before == after, "el transporte nunca reinicia Traefik"


def test_probe_reports_pending_instead_of_insecure_active(lab: str) -> None:
    settings = _live_settings(lab)
    operations = ProxyOperations(settings, build_runner(settings))
    try:
        info = operations.certificate(EXTRA)
    except ProxyError:
        return
    if info.state == "active":
        assert info.verified is True
    else:
        assert info.state in ("pending_cert", "pending_dns")
        assert info.verified is False
