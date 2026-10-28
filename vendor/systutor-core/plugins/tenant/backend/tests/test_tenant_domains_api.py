"""API de dominios administrados por tenant (A.SPEC 0037 / V2)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import HostingSite
from plugins.tenant.backend.models import TenantDomain
from plugins.tenant.backend.router import router as tenant_router
from systutor.api.deps import get_db_session
from systutor.contracts.domain_namespace import DomainUsage, register_domain_usage
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant

BASE = "/api/v1/plugins/tenant/tenants"


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all(
            [
                Tenant(id="t1", name="Demo", slug="demo", domain="spanel.site"),
                Tenant(id="t2", name="Otro", slug="otro", domain="otro.com"),
                Tenant(id="t3", name="Sin fila", slug="sinfila", domain="sinfila.com"),
            ]
        )
        session.add_all(
            [
                TenantDomain(domain="spanel.site", tenant_id="t1"),
                TenantDomain(domain="debug.com", tenant_id="t1"),
                TenantDomain(domain="otro.com", tenant_id="t2"),
                HostingSite(
                    id="s1",
                    tenant_id="t1",
                    container_name="spanel-demo-wp",
                    domain="spanel.site",
                ),
                HostingSite(
                    id="s2",
                    tenant_id="t2",
                    container_name="spanel-otro-wp",
                    domain="sitioajeno.com",
                ),
            ]
        )
        session.commit()
    yield session_factory
    engine.dispose()


def _app(factory: sessionmaker[Session], *, platform: bool) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(tenant_router, prefix="/api/v1/plugins/tenant")

    def site_provider(db: Session, tenant_id: str | None):
        stmt = select(HostingSite)
        if tenant_id is not None:
            stmt = stmt.where(HostingSite.tenant_id == tenant_id)
        return [
            OwnedSite(
                site_id=row.id,
                tenant_id=row.tenant_id,
                container=row.container_name,
                primary_domain=row.domain,
            )
            for row in db.scalars(stmt)
        ]

    register_site_provider(app, "hosting", site_provider)
    register_domain_usage(
        app,
        "domains",
        lambda db, domain: (
            DomainUsage(container="spanel-demo-wp", kind="extra", tenant_id="t1")
            if domain == "extra.spanel.site"
            else None
        ),
    )

    def fake_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    user = SimpleNamespace(id="user-1", tenant_id="t1", branch_id=None)
    permissions = ["tenant.tenants.read", "tenant.tenants.manage"]
    if platform:
        permissions.append("core.platform.admin")
    context = TenantContext(
        current_tenant_id="t1",
        current_branch_id=None,
        current_user_id="user-1",
        current_permissions=tuple(permissions),
        current_warehouse_ids=None,
    )
    app.dependency_overrides[get_db_session] = fake_session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_tenant_context] = lambda: context
    return app


@pytest.fixture()
def client(factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(_app(factory, platform=True)) as test_client:
        yield test_client


@pytest.fixture()
def tenant_only_client(factory: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(_app(factory, platform=False)) as test_client:
        yield test_client


def test_listar_dominios_propios_ordenados(client: TestClient) -> None:
    response = client.get(f"{BASE}/t1/domains")

    assert response.status_code == 200
    assert response.json() == {"domains": [{"domain": "debug.com"}, {"domain": "spanel.site"}]}


def test_anadir_dominio_libre_es_idempotente(client: TestClient) -> None:
    first = client.post(f"{BASE}/t1/domains", json={"domain": " Test2.COM. "})
    second = client.post(f"{BASE}/t1/domains", json={"domain": "test2.com"})

    assert first.status_code == 201
    assert first.json() == {"domain": "test2.com"}
    assert second.status_code == 201
    assert second.json() == {"domain": "test2.com"}


def test_anadir_dominio_de_otro_tenant_es_409(client: TestClient) -> None:
    response = client.post(f"{BASE}/t1/domains", json={"domain": "otro.com"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tenant_domain_conflict"


def test_anadir_el_primario_propio_es_409(client: TestClient) -> None:
    response = client.post(f"{BASE}/t1/domains", json={"domain": "spanel.site"})

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "tenant_domain_conflict"
    assert "primario" in body["message"]


def test_promover_secundario_a_primario_borra_su_fila(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = client.put(f"{BASE}/t1", json={"domain": "debug.com"})

    assert response.status_code == 200
    with factory() as session:
        assert session.get(TenantDomain, "debug.com") is None
        tenant = session.get(Tenant, "t1")
        assert tenant is not None and tenant.domain == "debug.com"


def test_anadir_primario_de_sitio_ajeno_es_409(client: TestClient) -> None:
    response = client.post(f"{BASE}/t1/domains", json={"domain": "sitioajeno.com"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tenant_domain_conflict"


def test_anadir_dominio_invalido_es_422(client: TestClient) -> None:
    response = client.post(f"{BASE}/t1/domains", json={"domain": "https://x"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tenant_domain_invalid"


def test_quitar_primario_de_sitio_es_409_con_contenedor(client: TestClient) -> None:
    response = client.delete(f"{BASE}/t1/domains/spanel.site")

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "tenant_domain_in_use"
    assert "spanel-demo-wp" in body["message"]


def test_quitar_extra_de_contenedor_es_409(client: TestClient) -> None:
    client.post(f"{BASE}/t1/domains", json={"domain": "extra.spanel.site"})

    response = client.delete(f"{BASE}/t1/domains/extra.spanel.site")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tenant_domain_in_use"


def test_quitar_dominio_de_correo_es_409(client: TestClient) -> None:
    # El dominio de correo del tenant no se quita: se cambia en el tenant.
    response = client.delete(f"{BASE}/t2/domains/otro.com")

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "tenant_domain_in_use"
    assert "dominio de correo" in body["message"]


def test_list_incluye_dominio_de_correo_aunque_falte_la_fila(
    client: TestClient,
) -> None:
    response = client.get(f"{BASE}/t3/domains")

    assert response.status_code == 200
    assert [item["domain"] for item in response.json()["domains"]] == ["sinfila.com"]


def test_anadir_dominio_de_correo_ajeno_menciona_el_motivo(
    client: TestClient,
) -> None:
    response = client.post(f"{BASE}/t1/domains", json={"domain": "sinfila.com"})

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "tenant_domain_conflict"
    assert "dominio de correo del tenant" in body["message"]


def test_quitar_dominio_libre_es_204(client: TestClient, factory) -> None:
    response = client.delete(f"{BASE}/t1/domains/debug.com")

    assert response.status_code == 204
    with factory() as session:
        assert session.get(TenantDomain, "debug.com") is None


def test_quitar_inexistente_es_404(client: TestClient) -> None:
    response = client.delete(f"{BASE}/t1/domains/nope.com")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tenant_domain_not_found"


def test_tenant_domain_no_cambia_al_gestionar_dominios(
    client: TestClient, factory
) -> None:
    client.post(f"{BASE}/t1/domains", json={"domain": "test2.com"})
    client.delete(f"{BASE}/t1/domains/debug.com")

    with factory() as session:
        assert session.get(Tenant, "t1").domain == "spanel.site"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", f"{BASE}/t1/domains", None),
        ("post", f"{BASE}/t1/domains", {"domain": "libre.com"}),
        ("delete", f"{BASE}/t1/domains/debug.com", None),
        ("get", BASE, None),
    ],
)
def test_sin_platform_admin_es_403(
    tenant_only_client: TestClient, method: str, path: str, body: dict | None
) -> None:
    response = tenant_only_client.request(method, path, json=body)

    assert response.status_code == 403
