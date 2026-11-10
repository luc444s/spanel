"""Rutas de Dominios: contrato estable, tenancy y reutilización del proxy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend import service as service_module
from plugins.domains.backend.models import DomainRoute
from plugins.domains.backend.proxy import CertificateInfo, PathRoute
from plugins.domains.backend.schemas import RouteCreate, RouteUpdate
from plugins.domains.backend.service import (
    DomainsService,
    refresh_application_routes,
    release_application_routes,
)
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.core.database import Base
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.context import TenantContext


class FakeRouteOps:
    def __init__(self) -> None:
        self.documents: dict[str, list[PathRoute]] = {}
        self.conflicts: set[tuple[str, str]] = set()
        self.probed: list[tuple[str, str]] = []

    def managed_domains(self):
        return []

    def write_route_document(
        self, domain, routes, *, validate_runtime=True, connect_network=True
    ):
        self.documents[domain] = list(routes)
        return None

    def remove_route_document(self, domain):
        self.documents.pop(domain, None)
        return True

    def path_conflict(self, domain, prefix):
        return (domain, prefix) in self.conflicts

    def probe_route(self, domain, path="/"):
        self.probed.append((domain, path))
        return CertificateInfo("active", issuer="CN=Test", verified=True), 200


def context(tenant: str = "t1") -> TenantContext:
    return TenantContext(
        current_tenant_id=tenant,
        current_branch_id=None,
        current_user_id="user-1",
        current_permissions=("domains.domains.read", "domains.domains.manage"),
        current_warehouse_ids=None,
    )


@pytest.fixture()
def setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'domains.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    app = FastAPI()
    owned = {"t1": {"api.example.com"}, "t2": {"other.example.com"}}

    sites = [
        OwnedSite("app-1:web", "t1", "app-container-a", None),
        OwnedSite("app-1:worker", "t1", "app-container-b", None),
        OwnedSite("app-2:web", "t2", "other-container", None),
    ]
    register_site_provider(
        app,
        "apps",
        lambda _db, tenant_id: (
            sites if tenant_id is None else [s for s in sites if s.tenant_id == tenant_id]
        ),
    )

    def owner_of(_db, domain):
        for tenant_id, domains in owned.items():
            if domain in domains:
                return tenant_id
        return None

    register_domain_providers(
        app,
        "tenant",
        list_domains=lambda _db, tenant_id: sorted(owned.get(tenant_id, set())),
        owner=owner_of,
        claim=lambda _db, tenant_id, domain: owned.setdefault(tenant_id, set()).add(domain),
        release=lambda _db, tenant_id, domain: owned.get(tenant_id, set()).discard(domain),
    )
    ops = FakeRouteOps()
    service = DomainsService(db, app)
    service._operations = lambda: ops  # type: ignore[method-assign]
    service._ready = lambda: ops  # type: ignore[method-assign]
    service._settings = lambda: SimpleNamespace(  # type: ignore[method-assign]
        profile="internal", entrypoint_http="web", entrypoint_https="websecure"
    )
    original = service_module.record_audit
    original_domains_settings = service_module.get_domains_settings
    audits: list[dict] = []
    service_module.record_audit = lambda _db, **kwargs: audits.append(kwargs)
    service_module.get_domains_settings = lambda: SimpleNamespace(
        middlewares=("rate-limit@file",)
    )
    try:
        yield service, ops, audits
    finally:
        service_module.record_audit = original
        service_module.get_domains_settings = original_domains_settings
        db.close()
        engine.dispose()


def test_create_route_resolves_stable_site_and_persists_all_fields(setup):
    service, ops, audits = setup
    item = service.create_route(
        context(),
        RouteCreate(
            domain="api.example.com",
            site_id="app-1:web",
            path="/api",
            internal_path="/",
            strip_path=True,
            port=8000,
            entrypoints=["web", "websecure"],
            https=True,
            middlewares=["rate-limit@file"],
        ),
    )

    assert item.domain == "api.example.com"
    assert item.site_id == "app-1:web"
    assert item.available is True
    assert item.strip_path is True
    published = ops.documents["api.example.com"][0]
    assert published.container == "app-container-a", "resuelve el contenedor activo"
    assert published.prefix == "/api"
    assert published.route_id == item.id
    assert published.middlewares == ("rate-limit@file",)
    assert audits[-1]["action"] == "domains.route.created"
    assert audits[-1]["details"]["route_id"] == item.id
    assert "app-container-a" not in str(audits[-1]["details"])


def test_route_requires_registered_domain_and_visible_service(setup):
    service, _, _ = setup
    with pytest.raises(NotFoundError):
        service.create_route(
            context(),
            RouteCreate(domain="not-registered.example.com", site_id="app-1:web", port=80),
        )
    with pytest.raises(NotFoundError):
        service.create_route(
            context(),
            RouteCreate(domain="api.example.com", site_id="app-2:web", port=80),
        )


def test_route_conflict_is_opaque_and_does_not_publish(setup):
    service, ops, _ = setup
    service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    with pytest.raises(AppError) as conflict:
        service.create_route(
            context(), RouteCreate(domain="api.example.com", site_id="app-1:worker", port=80)
        )
    assert conflict.value.status_code == 409
    assert conflict.value.code == "domains.route_conflict"
    assert len(ops.documents["api.example.com"]) == 1


def test_update_and_delete_rewrite_the_same_document(setup):
    service, ops, audits = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    updated = service.update_route(
        context(),
        item.id,
        RouteUpdate(path="/v1", strip_path=True, internal_path="/", port=8000),
    )
    assert updated.path == "/v1"
    assert ops.documents["api.example.com"][0].prefix == "/v1"
    assert ops.documents["api.example.com"][0].container == "app-container-a"

    service.delete_route(context(), item.id)
    assert ops.documents["api.example.com"] == []
    assert audits[-1]["action"] == "domains.route.removed"


def test_get_and_list_are_tenant_scoped_and_reconcile_container(setup):
    service, ops, _ = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    ops.documents["api.example.com"][0] = PathRoute(
        "/", "old-container", 80, route_id=item.id
    )
    service.list_routes(context())
    assert ops.documents["api.example.com"][0].container == "app-container-a"
    assert service.get_route(context(), item.id).id == item.id
    with pytest.raises(NotFoundError):
        service.get_route(context("t2"), item.id)


def test_refresh_application_routes_republishes_current_container(setup, monkeypatch):
    service, ops, _ = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    ops.documents["api.example.com"][0] = PathRoute(
        "/", "old-container", 80, route_id=item.id
    )
    monkeypatch.setattr(
        service_module, "DomainsService", lambda _db, _app: service
    )

    refresh_application_routes(service._db, service._app, "t1", "app-1")

    assert ops.documents["api.example.com"][0].container == "app-container-a"


def test_probe_reuses_existing_tls_probe(setup):
    service, ops, _ = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    result = service.probe_route(context(), item.id)
    assert result.http_status == 200
    assert result.certificate is not None and result.certificate.state == "active"
    assert ops.probed == [("api.example.com", "/")]


def test_route_database_row_is_domains_owned(setup):
    service, _, _ = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    row = service._db.get(DomainRoute, item.id)
    assert row is not None and row.tenant_id == "t1"


def test_application_delete_releases_its_routes(setup):
    service, ops, _ = setup
    service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    service.create_route(
        context(),
        RouteCreate(
            domain="api.example.com", site_id="app-1:worker", path="/jobs", port=9000
        ),
    )
    assert len(ops.documents["api.example.com"]) == 2
    release_application_routes(service._db, service._app, "t1", "app-1")
    assert service._db.query(DomainRoute).count() == 0


def test_available_exposes_only_effective_route_options(setup):
    service, _, _ = setup
    available = service.available_domains("t1")
    assert available.entrypoints == ["web", "websecure"]
    assert available.middlewares == ["rate-limit@file"]


def test_create_rejects_entrypoint_outside_profile(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.create_route(
            context(),
            RouteCreate(
                domain="api.example.com", site_id="app-1:web", port=80,
                entrypoints=["admin"],
            ),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "domains.entrypoint_not_allowed"
    assert ops.documents == {}


def test_create_rejects_middleware_outside_platform_allowlist(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.create_route(
            context(),
            RouteCreate(
                domain="api.example.com", site_id="app-1:web", port=80,
                middlewares=["untrusted@file"],
            ),
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "domains.middleware_not_allowed"
    assert ops.documents == {}


def test_update_rejects_disallowed_options_without_mutating(setup):
    service, _, _ = setup
    item = service.create_route(
        context(), RouteCreate(domain="api.example.com", site_id="app-1:web", port=80)
    )
    with pytest.raises(AppError) as entrypoint:
        service.update_route(context(), item.id, RouteUpdate(entrypoints=["admin"]))
    assert entrypoint.value.code == "domains.entrypoint_not_allowed"
    with pytest.raises(AppError) as middleware:
        service.update_route(
            context(), item.id, RouteUpdate(middlewares=["untrusted@file"])
        )
    assert middleware.value.code == "domains.middleware_not_allowed"
    assert service.get_route(context(), item.id).entrypoints == []
    assert service.get_route(context(), item.id).middlewares == []


def test_empty_platform_allowlist_is_safe_by_default(setup):
    service, _, _ = setup
    original = service_module.get_domains_settings
    service_module.get_domains_settings = lambda: SimpleNamespace(middlewares=())
    try:
        with pytest.raises(AppError) as exc:
            service.create_route(
                context(),
                RouteCreate(
                    domain="api.example.com", site_id="app-1:web", port=80,
                    middlewares=["rate-limit@file"],
                ),
            )
        assert exc.value.code == "domains.middleware_not_allowed"
        assert service.create_route(
            context(),
            RouteCreate(domain="api.example.com", site_id="app-1:web", port=80),
        ).id
    finally:
        service_module.get_domains_settings = original


def test_cross_tenant_domain_and_route_are_opaque(setup):
    service, _, _ = setup
    with pytest.raises(NotFoundError) as foreign_domain:
        service.create_route(
            context(),
            RouteCreate(domain="other.example.com", site_id="app-1:web", port=80),
        )
    assert str(foreign_domain.value) == "Dominio desconocido"
    foreign = service.create_route(
        context("t2"),
        RouteCreate(domain="other.example.com", site_id="app-2:web", port=80),
    )
    with pytest.raises(NotFoundError) as foreign_route:
        service.get_route(context(), foreign.id)
    assert str(foreign_route.value) == "Ruta desconocida"


def test_middleware_allowlist_parser_is_explicit_and_empty_by_default():
    from plugins.domains.backend.settings import _allowlist

    assert _allowlist("") == ()
    assert _allowlist(" rate-limit@file, auth@file,rate-limit@file ") == (
        "rate-limit@file",
        "auth@file",
    )
