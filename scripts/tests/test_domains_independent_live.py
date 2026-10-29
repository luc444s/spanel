"""Live suite for A.SPEC 0040 (reserve without site, link later).

Opt-in: requires `SPANEL_LIVE_TESTS=1`, a running `spanel-traefik-lab` and one
running site container attached to the lab network. Only `spanel-lab-*`
resources are touched. An absent lab is a GAP, not a pass.

What it proves:
- reserving a domain writes NO `spanel-*.yml` (I1);
- linking it later produces a router `@file` enabled with the internal CA
  certificate (I2), exactly like adding it with a site;
- the hash of every pre-existing `spanel-*.yml` is unchanged except the linked
  site file (I3);
- DELETE unpublishes the route but keeps the claim; a second DELETE releases
  the claim (I7).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

os.environ.setdefault("SYSTUTOR_DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

RAIZ = Path(__file__).resolve().parents[2]
CORE = RAIZ / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

import systutor.kernel.models  # noqa: F401
from app.main import create_app
from plugins.domains.backend.proxy import ProxyOperations
from plugins.hosting.backend.models import HostingSite
from systutor.api.seed import seed_demo_data
from systutor.core.config import Settings
from systutor.core.database import Base, build_engine, build_session_factory
from systutor.kernel.plugins.persistent import (
    enable_plugin,
    sync_plugin_registry_state,
)
from systutor.kernel.plugins.runtime import PluginManifestRegistry
from systutor.sdk import PluginContext

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
SITIO = os.environ.get("SPANEL_LIVE_DOMAIN_CONTAINER", "spanel-lab-domains-wp")
PRIMARIO = os.environ.get("SPANEL_LIVE_DOMAIN_PRIMARY", "lab-domains.spanel")
RESERVADO = os.environ.get("SPANEL_LIVE_DOMAIN_RESERVED", "lab-reserved.spanel")
LAB_DYNAMIC = Path(
    os.environ.get("SPANEL_LIVE_PROXY_DIR", RAIZ / "scripts" / "traefik-lab" / "dynamic")
)
LAB_CA = Path(
    os.environ.get(
        "SPANEL_LIVE_PROXY_CA", RAIZ / "scripts" / "traefik-lab" / "ca" / "root_ca.crt"
    )
)
TRAEFIK_API = os.environ.get(
    "SPANEL_LIVE_TRAEFIK_API", "http://127.0.0.1:9081"
)
EMAIL = "admin@example.com"
PASSWORD = "ChangeMe123!"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="test live: requiere SPANEL_LIVE_TESTS=1 y el laboratorio arriba",
)


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _routers() -> list[dict[str, Any]]:
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{TRAEFIK_API}/api/http/routers", timeout=3) as response:
            return json.loads(response.read())
    except OSError:
        return []


def _router(slug: str) -> dict[str, Any] | None:
    suffix = f"spanel-{slug}@file"
    return next(
        (
            router
            for router in _routers()
            if isinstance(router.get("name"), str) and router["name"] == suffix
        ),
        None,
    )


def _slug_of(domain: str) -> str:
    return domain.split(".")[0]


@dataclass
class Live:
    client: TestClient
    app: Any
    token: str
    site_id: str


@pytest.fixture()
def live(tmp_path: Path) -> Iterator[Live]:
    if _docker("inspect", "-f", "{{.State.Running}}", SITIO).stdout.strip() != "true":
        pytest.skip(f"falta el contenedor de laboratorio {SITIO}")
    if not LAB_DYNAMIC.is_dir():
        pytest.skip(f"falta el directorio dinámico {LAB_DYNAMIC}")
    if not LAB_CA.is_file():
        pytest.skip(f"falta la CA del laboratorio {LAB_CA}")
    if not _routers():
        pytest.skip("la API de Traefik del laboratorio no responde")

    os.environ["SPANEL_DOMAINS_PROFILE"] = "internal"
    os.environ["SPANEL_PROXY_CONFIG_DIR"] = str(LAB_DYNAMIC)
    os.environ["SPANEL_PROXY_CA_FILE"] = str(LAB_CA)
    os.environ["SPANEL_PROXY_HTTPS_PORT"] = "9443"
    os.environ["SPANEL_PROXY_TRAEFIK_CONTAINER"] = "spanel-traefik-lab"
    os.environ["SPANEL_PROXY_SSH_HOST"] = ""
    os.environ["SPANEL_PROXY_PROBE_HOST"] = "127.0.0.1"
    os.environ["SPANEL_PROXY_DNS_CONTAINER"] = "spanel-lab-dns"

    settings = Settings(
        app_name="Domains API test",
        env="test",
        debug=True,
        api_prefix="/api/v1",
        log_level="WARNING",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'domains.db'}",
        redis_url="redis://127.0.0.1:6379/15",
        jwt_secret_key="domains-test-secret",
        jwt_access_token_ttl_minutes=30,
        plugins_dir=CORE / "plugins",
        seed_demo_tenant_name="Demo Tenant",
        seed_demo_tenant_slug="demo",
        seed_demo_branch_name="Main Branch",
        seed_demo_branch_code="MAIN",
        seed_admin_email=EMAIL,
        seed_admin_password=PASSWORD,
        seed_admin_full_name="System Admin",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)

    registry = PluginManifestRegistry(settings.plugins_dir)
    registry.discover()
    with Session(engine) as db:
        sync_plugin_registry_state(db, registry=registry)
        for plugin_id in ("hosting", "domains", "tenant"):
            enable_plugin(
                db,
                registry=registry,
                plugin_id=plugin_id,
                context_builder=lambda manifest: PluginContext(manifest),
            )
        db.commit()

    app = create_app(settings)
    app.state.session_factory = build_session_factory(settings)
    site_id = "site-live"

    try:
        with TestClient(app) as client:
            with app.state.session_factory() as db:
                seeded = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
                db.add(
                    HostingSite(
                        id=site_id,
                        tenant_id=seeded["tenant_id"],
                        container_name=SITIO,
                        domain=PRIMARIO,
                    )
                )
                db.commit()
            token = _login(client)
            yield Live(client, app, token, site_id)
    finally:
        # Limpieza: quitar la ruta del reservado si quedó publicada y borrar la
        # fila del sitio de laboratorio (mejor esfuerzo, solo YAML spanel).
        try:
            from plugins.domains.backend.proxy import build_runner
            from plugins.domains.backend.settings import get_domains_settings

            proxy_settings = get_domains_settings().proxy_settings()
            operations = ProxyOperations(proxy_settings, build_runner(proxy_settings))
            for managed in operations.managed_domains():
                if managed.domain == RESERVADO:
                    operations.remove(RESERVADO)
        except Exception:  # noqa: BLE001
            pass
        with Session(engine) as db:
            db.execute(HostingSite.__table__.delete().where(HostingSite.id == site_id))
            db.commit()
        engine.dispose()


def _yml_hashes() -> dict[str, bytes]:
    return {
        path.name: path.read_bytes() for path in sorted(LAB_DYNAMIC.glob("spanel-*.yml"))
    }


def test_reservar_y_vincular(live: Live):
    slug = _slug_of(RESERVADO)
    before = _yml_hashes()

    # 1) Reservar: 201, fila del tenant y NINGÚN YAML nuevo.
    response = live.client.post(
        "/api/v1/plugins/domains",
        json={"domain": RESERVADO, "www": False},
        headers=_headers(live.token),
    )
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["published"] is False
    assert item["site_id"] is None

    after_reserve = _yml_hashes()
    assert after_reserve == before, "reservar no debe crear ni tocar spanel-*.yml"

    # 2) Vincular al sitio: router habilitado y certificado de la CA interna.
    linked = live.client.post(
        f"/api/v1/plugins/domains/{RESERVADO}/link",
        json={"site_id": live.site_id, "www": False},
        headers=_headers(live.token),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["items"][0]["published"] is True

    deadline = time.monotonic() + 60
    router = None
    while time.monotonic() < deadline:
        router = _router(slug)
        if router and router.get("status") == "enabled":
            break
        time.sleep(2)
    assert router is not None and router.get("status") == "enabled", router

    certificate = None
    while time.monotonic() < deadline:
        certificate = live.client.get(
            f"/api/v1/plugins/domains/{RESERVADO}/certificate",
            headers=_headers(live.token),
        )
        if certificate.status_code == 200 and certificate.json()["state"] == "active":
            break
        time.sleep(2)
    assert certificate is not None
    assert certificate.status_code == 200, certificate.text
    assert certificate.json()["verified"] is True, certificate.text

    # 3) Solo cambia el YAML del sitio vinculado.
    after_link = _yml_hashes()
    changed = {name for name in after_link if before.get(name) != after_link[name]}
    assert changed, "publicar debe escribir el YAML del sitio"
    assert len(changed) == 1, changed

    # 4) DELETE despublica la ruta pero conserva la reserva.
    unpublished = live.client.delete(
        f"/api/v1/plugins/domains/{RESERVADO}", headers=_headers(live.token)
    )
    assert unpublished.status_code == 204, unpublished.text

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        router = _router(slug)
        if router is None or router.get("status") != "enabled":
            break
        time.sleep(1)
    assert router is None or router.get("status") != "enabled", router

    listed = live.client.get("/api/v1/plugins/domains", headers=_headers(live.token))
    assert listed.status_code == 200, listed.text
    by_domain = {row["domain"]: row for row in listed.json()["items"]}
    assert by_domain.get(RESERVADO, {}).get("published") is False, listed.json()

    # 5) Liberar la reserva por completo: la fila deja de existir.
    released = live.client.delete(
        f"/api/v1/plugins/domains/{RESERVADO}", headers=_headers(live.token)
    )
    assert released.status_code == 204, released.text

    listed = live.client.get("/api/v1/plugins/domains", headers=_headers(live.token))
    assert RESERVADO not in {row["domain"] for row in listed.json()["items"]}

    # Final: el resto de YAML volvió a su hash previo al vínculo.
    final = _yml_hashes()
    for name, digest in before.items():
        assert name not in changed or final.get(name) == digest, name
