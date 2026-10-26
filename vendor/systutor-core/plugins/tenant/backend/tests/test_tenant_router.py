"""Superficie HTTP del tenant: sufijo en la respuesta y PUT parcial (V2)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import systutor.kernel.models  # noqa: F401
from plugins.tenant.backend.models import TenantSiteSettings
from plugins.tenant.backend.router import router as tenant_router
from systutor.api.deps import get_db_session
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Tenant(id="t1", name="Demo", slug="demo", domain="demo.com"))
        session.add(
            TenantSiteSettings(tenant_id="t1", site_suffix="demo.com")
        )
        session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(tenant_router, prefix="/api/v1/plugins/tenant")

    user = SimpleNamespace(id="user-1", tenant_id="t1", branch_id=None)
    context = TenantContext(
        current_tenant_id="t1",
        current_branch_id=None,
        current_user_id="user-1",
        current_permissions=(
            "tenant.tenants.read",
            "tenant.tenants.manage",
            "core.platform.admin",
        ),
        current_warehouse_ids=None,
    )

    def fake_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = fake_session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_tenant_context] = lambda: context

    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def test_listado_incluye_site_suffix(client: TestClient) -> None:
    response = client.get("/api/v1/plugins/tenant/tenants")

    assert response.status_code == 200
    assert response.json()["tenants"][0]["site_suffix"] == "demo.com"


def test_put_solo_sufijo_conserva_dominio(client: TestClient) -> None:
    response = client.put(
        "/api/v1/plugins/tenant/tenants/t1",
        json={"site_suffix": "spanel"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["domain"] == "demo.com"
    assert payload["site_suffix"] == "spanel"


def test_put_vacio_es_422(client: TestClient) -> None:
    response = client.put("/api/v1/plugins/tenant/tenants/t1", json={})

    assert response.status_code == 422


def test_put_sufijo_invalido_es_422(client: TestClient) -> None:
    response = client.put(
        "/api/v1/plugins/tenant/tenants/t1",
        json={"site_suffix": "mal sufijo"},
    )

    assert response.status_code == 422
