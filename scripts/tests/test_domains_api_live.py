"""Live API suite for A.SPEC 0027 (domains plugin).

Opt-in: requires `SPANEL_LIVE_TESTS=1`, a running `spanel-traefik-lab` and one
running site container attached to the lab network. Only `spanel-lab-*`
resources are touched. An absent lab is a GAP, not a pass.

What it proves:
- the API lists the Hosting primary as read-only and the extra as editable;
- adding an extra writes `spanel-<slug>.yml`, Traefik enables the router and
  the internal CA serves it;
- deleting the extra leaves the primary and every other file untouched;
- Hosting refuses to create a site with a domain already published as extra.
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
from plugins.hosting.backend.models import HostingSite
from plugins.tenant.backend.service import create_tenant, create_user_for_tenant
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
EXTRA = os.environ.get("SPANEL_LIVE_DOMAIN_EXTRA", "lab-extra.spanel")
LAB_DYNAMIC = RAIZ / "scripts" / "traefik-lab" / "dynamic"
LAB_CA = RAIZ / "scripts" / "traefik-lab" / "ca" / "root_ca.crt"
TRAEFIK_API = os.environ.get("SPANEL_LIVE_TRAEFIK_API", "http://127.0.0.1:9081")
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
    return next((router for router in _routers() if router.get("name") == suffix), None)


@dataclass
class Live:
    client: TestClient
    app: Any
    token: str
    site_id: str
    tenant_id: str


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
        for plugin_id in ("hosting", "domains"):
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
    tenant_id = ""

    try:
        with TestClient(app) as client:
            with app.state.session_factory() as db:
                seeded = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
                tenant_id = seeded["tenant_id"]
                db.add(
                    HostingSite(
                        id=site_id,
                        tenant_id=tenant_id,
                        container_name=SITIO,
                        domain=PRIMARIO,
                    )
                )
                db.commit()
            token = _login(client)
            yield Live(client, app, token, site_id, tenant_id)
    finally:
        # Limpieza: quitar el extra si quedó y borrar la fila de laboratorio.
        with Session(engine) as db:
            db.execute(
                HostingSite.__table__.delete().where(HostingSite.id == site_id)
            )
            db.commit()
        engine.dispose()


def test_flujo_completo_del_dominio_extra(live: Live):
    before = sorted(path.name for path in LAB_DYNAMIC.glob("spanel-*.yml"))
    slug = SITIO.removeprefix("spanel-").removesuffix("-wp")

    try:
        response = live.client.post(
            "/api/v1/plugins/domains",
            json={"site_id": live.site_id, "domain": EXTRA, "www": False},
            headers=_headers(live.token),
        )
        assert response.status_code == 201, response.text

        listed = live.client.get(
            "/api/v1/plugins/domains", headers=_headers(live.token)
        )
        assert listed.status_code == 200, listed.text
        items = {item["domain"]: item for item in listed.json()["items"]}
        assert items[PRIMARIO]["owner"] == "hosting"
        assert items[PRIMARIO]["editable"] is False
        assert items[EXTRA]["owner"] == "domains"
        assert items[EXTRA]["editable"] is True

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
                f"/api/v1/plugins/domains/{EXTRA}/certificate",
                headers=_headers(live.token),
            )
            if certificate.status_code == 200 and certificate.json()["state"] == "active":
                break
            time.sleep(2)
        assert certificate is not None
        assert certificate.status_code == 200, certificate.text
        assert certificate.json()["verified"] is True, certificate.text
    finally:
        live.client.delete(
            f"/api/v1/plugins/domains/{EXTRA}", headers=_headers(live.token)
        )

    after = sorted(path.name for path in LAB_DYNAMIC.glob("spanel-*.yml"))
    assert after == before, "quitar el extra no debe tocar otros archivos"
    router = _router(slug)
    assert router is None or router.get("status") != "enabled"
    assert EXTRA not in "".join(
        (LAB_DYNAMIC / name).read_text() for name in after
    )


def test_hosting_no_crea_sitio_sobre_un_extra(live: Live):
    response = live.client.post(
        "/api/v1/plugins/domains",
        json={"site_id": live.site_id, "domain": "lab-claim.spanel", "www": False},
        headers=_headers(live.token),
    )
    assert response.status_code == 201, response.text
    try:
        response = live.client.post(
            "/api/v1/plugins/hosting/sites",
            json={
                "name": "lab-claim",
                "domain": "lab-claim.spanel",
                "admin_email": "admin@example.com",
            },
            headers=_headers(live.token),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "hosting_site_conflict"
    finally:
        live.client.delete(
            "/api/v1/plugins/domains/lab-claim.spanel", headers=_headers(live.token)
        )


def test_primario_no_se_puede_borrar(live: Live):
    response = live.client.delete(
        f"/api/v1/plugins/domains/{PRIMARIO}", headers=_headers(live.token)
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "domains.primary_owned"
