"""Propiedad explícita de dominios en lista y check (A.SPEC 0037 / V3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend import service as service_module
from plugins.domains.backend.proxy import CertificateInfo, ManagedDomain
from plugins.domains.backend.schemas import DomainCheckResponse
from plugins.domains.backend.service import DomainsService
from plugins.hosting.backend.models import HostingSite
from plugins.tenant.backend.models import TenantSiteSettings  # noqa: F401
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.domain_ownership import (
    OwnedSite,
    register_extra_claims,
    register_site_provider,
)
from systutor.contracts.site_namespace import register_site_suffix_provider
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant

TENANT = "t1"
SITE_A = OwnedSite("site-a", TENANT, "spanel-a-wp", "a.spanel")
SITE_B = OwnedSite("site-b", TENANT, "spanel-b-wp", None)
SITE_C = OwnedSite("site-c", "t2", "spanel-c-wp", "c.spanel")


class FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0
        self.gets: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def get(self, model, key):
        self.gets.append((model, key))
        return None


class FakeOps:
    def __init__(self) -> None:
        self.extras = [
            ManagedDomain("b.spanel", "spanel-a-wp", "internal", "spanel-a.yml"),
            ManagedDomain("lucas.spanel", "spanel-a-wp", "internal", "spanel-a.yml"),
        ]
        self.added: list[tuple[str, str, bool]] = []
        self.removed: list[str] = []

    def config_writable(self):
        return True, ""

    def managed_domains(self):
        return list(self.extras)

    def add(self, container, domain, *, www=False, wait=0, set_wordpress_url=False):
        self.added.append((container, domain, www))
        return 0

    def remove(self, domain):
        self.removed.append(domain)
        return 0

    def certificate(self, domain):
        return CertificateInfo("active", issuer="CN=Test", not_after="2030", verified=True)


def _context(permissions=("domains.domains.read",)) -> TenantContext:
    return TenantContext(TENANT, None, "user-1", permissions, None)


def build_service(
    suffix: str | None,
    *,
    extras: list[ManagedDomain] | None = None,
    owned: dict[str, set[str]] | None = None,
):
    app = FastAPI()
    rows = [SITE_A, SITE_B, SITE_C]

    def provider(db: Session, tenant_id: str | None):
        return rows if tenant_id is None else [row for row in rows if row.tenant_id == tenant_id]

    register_site_provider(app, "hosting", provider)
    register_extra_claims(app, "domains", lambda db, domain: False)
    register_site_suffix_provider(app, "tenant", lambda db, tenant_id: suffix)

    ownership = owned if owned is not None else {
        "t1": {"a.spanel", "b.spanel"},
        "t2": {"c.spanel"},
    }

    def list_owned(db, tenant_id):  # noqa: ANN001
        return sorted(ownership.get(tenant_id, set()))

    def owner_of(db, domain):  # noqa: ANN001
        for tenant_id, names in ownership.items():
            if domain in names:
                return tenant_id
        return None

    register_domain_providers(app, "tenant", list_domains=list_owned, owner=owner_of)

    ops = FakeOps()
    if extras is not None:
        ops.extras = extras
    db = FakeDb()
    service = DomainsService(db, app)
    service._operations = lambda: ops  # type: ignore[method-assign]
    service._ready = lambda: ops  # type: ignore[method-assign]
    service._settings = lambda: SimpleNamespace(profile="internal")  # type: ignore[method-assign]
    return service, ops, db, app


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    calls: list[dict] = []

    def fake_audit(db, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(service_module, "record_audit", fake_audit)
    return calls


def test_list_keeps_published_domain_visible_even_without_explicit_row() -> None:
    service, _, _, _ = build_service("spanel.site")

    items, _ = service.list_domains(TENANT, can_manage=True)
    by_domain = {item.domain: item for item in items}

    # Un extra publicado por un contenedor del tenant es parte de su
    # namespace aunque no tenga fila en tenant_domains (YAML heredado).
    assert set(by_domain) == {"a.spanel", "b.spanel", "lucas.spanel"}


def test_list_no_depende_del_platform_admin() -> None:
    service, _, _, _ = build_service("spanel.site")

    items, _ = service.list_domains(TENANT, can_manage=True)
    by_domain = {item.domain for item in items}

    assert by_domain == {"a.spanel", "b.spanel", "lucas.spanel"}


def test_list_does_not_invent_unpublished_owned_domains() -> None:
    owned = {"t1": {"a.spanel", "b.spanel", "debug.com"}, "t2": {"c.spanel"}}
    service, _, _, _ = build_service("spanel", owned=owned)

    items, _ = service.list_domains(TENANT, can_manage=True)

    assert "debug.com" not in {item.domain for item in items}


def test_owned_domain_published_as_extra_is_listed() -> None:
    owned = {"t1": {"a.spanel", "b.spanel", "debug.com"}, "t2": {"c.spanel", "other.com"}}
    service, ops, _, _ = build_service("spanel.site", owned=owned)
    ops.extras = [
        ManagedDomain("debug.com", "spanel-a-wp", "external", "spanel-a.yml")
    ]

    items, _ = service.list_domains(TENANT, can_manage=True)

    assert "debug.com" in {row.domain for row in items}


def test_check_available_free_domain_regardless_of_suffix() -> None:
    service, _, _, _ = build_service("spanel.site")

    for domain in ("debug.com", "test2.com", "paginadelucas.spanel.site"):
        result = service.check_domain(_context(), domain)
        assert result.available is True, domain
        assert result.suffix == "spanel.site"


def test_check_available_without_suffix() -> None:
    service, _, _, _ = build_service(None)

    result = service.check_domain(_context(), "debug.com")

    assert result == DomainCheckResponse(available=True, reason=None, suffix=None)


def test_check_other_tenant_owner_is_unavailable() -> None:
    service, _, _, _ = build_service(
        "spanel",
        owned={"t1": {"a.spanel", "b.spanel"}, "t2": {"other.com"}},
    )

    result = service.check_domain(_context(), "other.com")

    assert result.available is False
    assert "otro tenant" in (result.reason or "")


def test_check_primary_mentions_container() -> None:
    service, _, _, _ = build_service("spanel")

    result = service.check_domain(_context(), "a.spanel")

    assert result.available is False
    assert "spanel-a-wp" in (result.reason or "")


def test_check_extra_mentions_container() -> None:
    service, _, _, _ = build_service("spanel")

    result = service.check_domain(_context(), "lucas.spanel")

    assert result.available is False
    assert "spanel-a-wp" in (result.reason or "")


def test_check_invalid_domain_is_422() -> None:
    service, _, _, _ = build_service("spanel")

    with pytest.raises(AppError) as exc:
        service.check_domain(_context(), "mal dominio")

    assert exc.value.status_code == 422
    assert exc.value.code == "domains.invalid_domain"


def test_check_never_writes_or_reserves() -> None:
    service, ops, db, _ = build_service("spanel")

    first = service.check_domain(_context(), "debug.com")
    second = service.check_domain(_context(), "debug.com")

    assert first == second
    assert ops.added == []
    assert ops.removed == []
    assert db.added == []
    assert db.flushes == 0
    assert db.commits == 0


def test_certificate_works_for_attached_domain() -> None:
    service, _, _, _ = build_service("spanel.site")

    info = service.certificate(TENANT, "lucas.spanel")
    assert info.state == "active"


def test_check_no_modifica_filas_reales_de_tenants_ni_hosting_sites() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            db.add(Tenant(id=TENANT, name="Demo", slug="demo", domain="demo.com"))
            db.add(
                HostingSite(
                    tenant_id=TENANT,
                    container_name="spanel-a-wp",
                    domain="a.spanel",
                )
            )
            db.commit()

            tenants_before = [
                (row.id, row.domain) for row in db.scalars(select(Tenant)).all()
            ]
            sites_before = [
                (row.id, row.tenant_id, row.container_name, row.domain)
                for row in db.scalars(select(HostingSite)).all()
            ]

            app = FastAPI()
            register_site_provider(app, "hosting", lambda _db, _tenant: [SITE_A])
            register_site_suffix_provider(app, "tenant", lambda _db, _tenant: "spanel")
            ops = FakeOps()
            service = DomainsService(db, app)
            service._operations = lambda: ops  # type: ignore[method-assign]
            service._settings = lambda: SimpleNamespace(profile="internal")  # type: ignore[method-assign]

            first = service.check_domain(_context(), "debug.com")
            second = service.check_domain(_context(), "debug.com")

            assert first == second
            assert [
                (row.id, row.domain) for row in db.scalars(select(Tenant)).all()
            ] == tenants_before
            assert [
                (row.id, row.tenant_id, row.container_name, row.domain)
                for row in db.scalars(select(HostingSite)).all()
            ] == sites_before
            assert ops.added == []
            assert ops.removed == []
    finally:
        engine.dispose()
