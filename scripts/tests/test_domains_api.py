"""API suite for A.SPEC 0027 without Docker.

Proves routing, permissions and the primary-domain guard with a throwaway
SQLite database. The proxy filesystem is not touched: every request that would
write fails earlier at the ownership guard, which is exactly what this suite
checks. Mutations against real YAML are covered by `test_domains_api_live.py`
and by `test_proxy_domain.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

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
from plugins.domains.backend.settings import get_domains_settings
from plugins.hosting.backend.models import HostingSite
from plugins.tenant.backend.service import create_tenant, create_user_for_tenant
from systutor.api.seed import seed_demo_data
from systutor.core.config import Settings
from systutor.core.database import Base, build_engine, build_session_factory
from systutor.kernel.plugins.persistent import enable_plugin, sync_plugin_registry_state
from systutor.kernel.plugins.runtime import PluginManifestRegistry
from systutor.sdk import PluginContext

A_EMAIL = "admin@example.com"
A_PASSWORD = "ChangeMe123!"
B_EMAIL = "admin@tenant-b.test"
B_PASSWORD = "TenantB123!"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    # Laboratorio local: no Docker, el proxy solo lee un directorio vacío.
    dynamic = tmp_path / "dynamic"
    dynamic.mkdir()
    monkeypatch.setenv("SPANEL_DOMAINS_PROFILE", "internal")
    monkeypatch.setenv("SPANEL_PROXY_CONFIG_DIR", str(dynamic))
    get_domains_settings.cache_clear()
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
        seed_admin_email=A_EMAIL,
        seed_admin_password=A_PASSWORD,
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
    try:
        with TestClient(app) as client:
            with app.state.session_factory() as db:
                seeded = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
                tenant_b = create_tenant(db, name="Tenant B", slug="tenant-b")
                create_user_for_tenant(
                    db,
                    tenant_id=tenant_b.id,
                    email=B_EMAIL,
                    password=B_PASSWORD,
                    full_name="Tenant B Admin",
                )
                db.add(
                    HostingSite(
                        id="site-a",
                        tenant_id=seeded["tenant_id"],
                        container_name="spanel-a-wp",
                        domain="a.example.com",
                    )
                )
                db.add(
                    HostingSite(
                        id="site-b",
                        tenant_id=tenant_b.id,
                        container_name="spanel-b-wp",
                        domain="b.example.com",
                    )
                )
                db.commit()

            yield {
                "client": client,
                "token_a": _login(client, A_EMAIL, A_PASSWORD),
                "token_b": _login(client, B_EMAIL, B_PASSWORD),
                "dynamic": dynamic,
            }
    finally:
        engine.dispose()
        get_domains_settings.cache_clear()


def test_listado_solo_del_tenant(api):
    client = api["client"]
    response = client.get("/api/v1/plugins/domains", headers=_headers(api["token_a"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["domain"] for item in body["items"]] == ["a.example.com"]
    assert body["items"][0]["owner"] == "hosting"
    assert body["items"][0]["editable"] is False

    response = client.get("/api/v1/plugins/domains", headers=_headers(api["token_b"]))
    assert response.status_code == 200, response.text
    assert [item["domain"] for item in response.json()["items"]] == ["b.example.com"]


def test_primario_del_otro_tenant_no_es_visible(api):
    client = api["client"]
    response = client.get(
        "/api/v1/plugins/domains/b.example.com/certificate",
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 404, response.text


def test_no_se_puede_agregar_un_primario(api):
    client = api["client"]
    response = client.post(
        "/api/v1/plugins/domains",
        json={"site_id": "site-a", "domain": "b.example.com", "www": False},
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "domains.primary_owned"


def test_sitio_ajeno_responde_404(api):
    client = api["client"]
    response = client.post(
        "/api/v1/plugins/domains",
        json={"site_id": "site-b", "domain": "x.example.com", "www": False},
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 404, response.text


def test_borrar_primario_es_409(api):
    client = api["client"]
    response = client.delete(
        "/api/v1/plugins/domains/a.example.com", headers=_headers(api["token_a"])
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "domains.primary_owned"


def test_certificado_de_dominio_desconocido_es_404(api):
    client = api["client"]
    response = client.get(
        "/api/v1/plugins/domains/nope.example.com/certificate",
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 404, response.text


def test_sin_token_es_401(api):
    response = api["client"].get("/api/v1/plugins/domains")
    assert response.status_code == 401, response.text


# ---- A.SPEC 0040: reservar sin sitio y vincular después ----------------------


def test_reservar_sin_sitio_no_publica(api):
    """I1: la reserva no escribe ningún spanel-*.yml."""
    client = api["client"]
    dynamic: Path = api["dynamic"]
    before = sorted(path.name for path in dynamic.glob("spanel-*.yml"))

    response = client.post(
        "/api/v1/plugins/domains",
        json={"domain": "reservado.example.com", "www": False},
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 201, response.text
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["published"] is False
    assert item["site_id"] is None
    assert item["container"] is None
    assert item["owner"] == "domains"
    assert item["editable"] is True

    after = sorted(path.name for path in dynamic.glob("spanel-*.yml"))
    assert after == before, "reservar no debe crear ni modificar YAML"

    listed = client.get("/api/v1/plugins/domains", headers=_headers(api["token_a"]))
    assert listed.status_code == 200, listed.text
    by_domain = {row["domain"]: row for row in listed.json()["items"]}
    assert by_domain["reservado.example.com"]["published"] is False
    assert "a.example.com" in by_domain  # el primario sigue ahí

    cert = client.get(
        "/api/v1/plugins/domains/reservado.example.com/certificate",
        headers=_headers(api["token_a"]),
    )
    assert cert.status_code == 200, cert.text
    assert cert.json()["state"] == "unpublished"

    # Idempotente para el mismo tenant.
    repeat = client.post(
        "/api/v1/plugins/domains",
        json={"domain": "reservado.example.com"},
        headers=_headers(api["token_a"]),
    )
    assert repeat.status_code == 201, repeat.text

    # I4: otroe tenant recibe 409; un dueño por dominio.
    foreign = client.post(
        "/api/v1/plugins/domains",
        json={"domain": "reservado.example.com", "www": False},
        headers=_headers(api["token_b"]),
    )
    assert foreign.status_code == 409, foreign.text
    assert foreign.json()["error"]["code"] == "domains.already_owned"


def test_borrar_reservado_libera(api):
    """I7: DELETE de un reservado responde 204 y libera la fila."""
    client = api["client"]
    response = client.post(
        "/api/v1/plugins/domains",
        json={"domain": "temporal.example.com"},
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 201, response.text

    released = client.delete(
        "/api/v1/plugins/domains/temporal.example.com", headers=_headers(api["token_a"])
    )
    assert released.status_code == 204, released.text

    listed = client.get("/api/v1/plugins/domains", headers=_headers(api["token_a"]))
    assert "temporal.example.com" not in {
        row["domain"] for row in listed.json()["items"]
    }
    # Sigue liberado: borrar dos veces es 404.
    again = client.delete(
        "/api/v1/plugins/domains/temporal.example.com", headers=_headers(api["token_a"])
    )
    assert again.status_code == 404, again.text


def test_sitio_ajeno_en_reserva_es_404(api):
    client = api["client"]
    response = client.post(
        "/api/v1/plugins/domains",
        json={"site_id": "nope", "domain": "x.example.com", "www": False},
        headers=_headers(api["token_a"]),
    )
    assert response.status_code == 404, response.text
