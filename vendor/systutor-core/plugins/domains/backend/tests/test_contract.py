from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import HostingSite  # noqa: F401
from plugins.hosting.backend.service import HostingService
from plugins.hosting.backend.settings import HostingSettings
from systutor.contracts.domain_ownership import (
    OwnedSite,
    is_extra_claimed,
    is_primary_domain,
    list_sites,
    register_extra_claims,
    register_site_provider,
    resolve_site,
)
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


def test_duplicate_site_provider_with_other_impl_raises():
    app = FastAPI()

    def a(db, tenant_id):
        return []

    def b(db, tenant_id):
        return []

    register_site_provider(app, "hosting", a)
    with pytest.raises(ValueError):
        register_site_provider(app, "hosting", b)
    register_site_provider(app, "hosting", a)  # idempotente


def test_duplicate_claim_checker_with_other_impl_raises():
    app = FastAPI()

    def a(db, domain):
        return False

    def b(db, domain):
        return False

    register_extra_claims(app, "domains", a)
    with pytest.raises(ValueError):
        register_extra_claims(app, "domains", b)


def test_site_lookup_filters_tenant_and_resolves():
    app = FastAPI()
    rows = [
        OwnedSite("s1", "t1", "spanel-a-wp", "a.spanel"),
        OwnedSite("s2", "t2", "spanel-b-wp", "b.spanel"),
    ]
    def provider(db, tenant_id):
        return rows if tenant_id is None else [row for row in rows if row.tenant_id == tenant_id]

    register_site_provider(app, "hosting", provider)

    sites = list_sites(app, None, "t1")  # type: ignore[arg-type]
    assert [site.site_id for site in sites] == ["s1"]
    assert resolve_site(app, None, "t1", "s2") is None  # type: ignore[arg-type]
    resolved = resolve_site(app, None, "t2", "s2")  # type: ignore[arg-type]
    assert resolved is not None and resolved.container == "spanel-b-wp"


def test_primary_domain_is_global():
    app = FastAPI()
    rows = [OwnedSite("s1", "t1", "a", "shared.spanel")]
    register_site_provider(app, "hosting", lambda db, tenant_id: rows)

    assert is_primary_domain(app, None, "shared.spanel") is True  # type: ignore[arg-type]
    assert is_primary_domain(app, None, "SHARED.SPANEL") is True  # type: ignore[arg-type]
    assert is_primary_domain(app, None, "other.spanel") is False  # type: ignore[arg-type]


def test_extra_claim_is_global_across_checkers():
    app = FastAPI()
    register_extra_claims(app, "one", lambda db, domain: domain == "x.spanel")
    register_extra_claims(app, "two", lambda db, domain: domain == "y.spanel")

    assert is_extra_claimed(app, None, "x.spanel") is True  # type: ignore[arg-type]
    assert is_extra_claimed(app, None, "y.spanel") is True  # type: ignore[arg-type]
    assert is_extra_claimed(app, None, "z.spanel") is False  # type: ignore[arg-type]


def test_hosting_create_site_rejects_claimed_extra(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'guard.db'}")
    Base.metadata.create_all(engine)
    app = FastAPI()
    register_extra_claims(app, "domains", lambda db, domain: domain == "claimed.spanel")

    with Session(engine) as db:
        db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
        db.commit()
        service = HostingService(db, settings=HostingSettings(docker_transport="local"))
        context = TenantContext("t1", None, "u1", (), None)

        with pytest.raises(AppError) as exc:
            service.create_site(
                context, "demo", "claimed.spanel", "admin@example.com", app=app
            )
        assert exc.value.status_code == 409
        assert exc.value.code == "hosting_site_conflict"
        assert "dominio extra" in exc.value.message.lower()
    engine.dispose()
