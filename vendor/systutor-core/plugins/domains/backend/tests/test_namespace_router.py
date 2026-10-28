"""La ruta de dominios delega el namespace en el contexto (V3 / I4 / I9)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.domains.backend.router import _get_service
from plugins.domains.backend.router import router as domains_router
from plugins.domains.backend.schemas import DomainCheckResponse
from systutor.api.deps import get_db_session
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext


class FakeDb:
    def get(self, model, key):
        return None

    def add(self, row) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass


class SpyService:
    def __init__(self) -> None:
        self.list_calls: list[dict] = []
        self.check_calls: list[tuple[TenantContext, str]] = []

    def list_domains(self, tenant_id: str, *, can_manage: bool):
        self.list_calls.append(
            {
                "tenant_id": tenant_id,
                "can_manage": can_manage,
            }
        )
        return [], []

    def check_domain(self, tenant_context: TenantContext, domain: str) -> DomainCheckResponse:
        self.check_calls.append((tenant_context, domain))
        return DomainCheckResponse(available=True, reason=None, suffix="spanel")


def build_client(service: SpyService, permissions: tuple[str, ...]) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(domains_router, prefix="/api/v1/plugins/domains")

    user = SimpleNamespace(id="user-1", tenant_id="t1", branch_id=None)
    context = TenantContext(
        current_tenant_id="t1",
        current_branch_id=None,
        current_user_id="user-1",
        current_permissions=permissions,
        current_warehouse_ids=None,
    )
    db = FakeDb()

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_tenant_context] = lambda: context
    app.dependency_overrides[_get_service] = lambda: service
    return TestClient(app)


def test_list_usa_el_tenant_del_contexto() -> None:
    service = SpyService()
    client = build_client(service, ("domains.domains.read",))

    response = client.get("/api/v1/plugins/domains")

    assert response.status_code == 200
    assert service.list_calls[0]["tenant_id"] == "t1"


def test_list_platform_admin_ve_el_mismo_tenant() -> None:
    service = SpyService()
    client = build_client(
        service,
        ("domains.domains.read", "core.platform.admin"),
    )

    response = client.get("/api/v1/plugins/domains")

    assert response.status_code == 200
    assert service.list_calls[0]["tenant_id"] == "t1"
    assert service.list_calls[0]["can_manage"] is False


def test_check_exige_domains_read() -> None:
    service = SpyService()
    client = build_client(service, ())

    response = client.get(
        "/api/v1/plugins/domains/check",
        params={"domain": "paginadelucas.spanel"},
    )

    assert response.status_code == 403
    assert service.check_calls == []


def test_check_resuelve_tenant_del_contexto_e_ignora_query() -> None:
    service = SpyService()
    client = build_client(service, ("domains.domains.read",))

    response = client.get(
        "/api/v1/plugins/domains/check",
        params={"domain": "paginadelucas.spanel", "tenant_id": "otro"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "reason": None,
        "suffix": "spanel",
    }
    context, domain = service.check_calls[0]
    assert context.current_tenant_id == "t1"
    assert domain == "paginadelucas.spanel"
