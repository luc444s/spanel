from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend import service as service_module
from plugins.domains.backend.proxy import CertificateInfo, ManagedDomain
from plugins.domains.backend.service import DomainsService
from systutor.contracts.domain_ownership import (
    OwnedSite,
    register_extra_claims,
    register_site_provider,
)
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.context import TenantContext

SITE_A = OwnedSite("site-a", "t1", "spanel-a-wp", "a.spanel")
SITE_B = OwnedSite("site-b", "t1", "spanel-b-wp", None)
SITE_C = OwnedSite("site-c", "t2", "spanel-c-wp", "c.spanel")


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class FakeOps:
    def __init__(self) -> None:
        self.extras = [
            ManagedDomain("b.spanel", "spanel-a-wp", "internal", "spanel-a.yml"),
            ManagedDomain("c.spanel", "spanel-c-wp", "internal", "spanel-c.yml"),
        ]
        self.added: list[tuple[str, str, bool]] = []
        self.removed: list[str] = []

    def config_writable(self):
        return True, ""

    def managed_domains(self):
        return list(self.extras)

    def add(self, container, domain, *, www=False, wait=0, set_wordpress_url=False):
        self.added.append((container, domain, www))
        self.extras.append(
            ManagedDomain(domain, container, "internal", f"{container}.yml")
        )
        if www:
            self.extras.append(
                ManagedDomain(f"www.{domain}", container, "internal", f"{container}.yml")
            )
        return 0

    def remove(self, domain):
        self.removed.append(domain)
        self.extras = [
            row
            for row in self.extras
            if row.domain not in (domain, f"www.{domain}")
        ]
        return 0

    def certificate(self, domain):
        return CertificateInfo("active", issuer="CN=Test", not_after="2030", verified=True)


@pytest.fixture()
def setup():
    app = FastAPI()
    rows = [SITE_A, SITE_B, SITE_C]

    def provider(db: Session, tenant_id: str | None):
        return rows if tenant_id is None else [row for row in rows if row.tenant_id == tenant_id]

    register_site_provider(app, "hosting", provider)
    register_extra_claims(app, "domains", lambda db, domain: False)

    ops = FakeOps()
    audits: list[dict] = []
    service = DomainsService(FakeDb(), app)
    service._operations = lambda: ops  # type: ignore[method-assign]
    service._ready = lambda: ops  # type: ignore[method-assign]
    service._settings = lambda: SimpleNamespace(profile="internal")  # type: ignore[method-assign]
    service._test_sites = rows  # type: ignore[attr-defined]

    original = service_module.record_audit

    def fake_audit(db, **kwargs):
        audits.append(kwargs)
        return None

    service_module.record_audit = fake_audit
    try:
        yield service, ops, audits
    finally:
        service_module.record_audit = original


def ctx(tenant="t1", permissions=("domains.domains.read", "domains.domains.manage")):
    return TenantContext(tenant, None, "user-1", permissions, None)


def test_list_merges_primary_and_extra(setup):
    service, _, _ = setup
    items, sites = service.list_domains("t1", can_manage=True)
    by_domain = {item.domain: item for item in items}

    assert by_domain["a.spanel"].owner == "hosting"
    assert by_domain["a.spanel"].editable is False
    assert by_domain["b.spanel"].owner == "domains"
    assert by_domain["b.spanel"].editable is True
    assert "c.spanel" not in by_domain, "extra de otro tenant"
    assert [site.container for site in sites] == ["spanel-a-wp", "spanel-b-wp"]


def test_list_marks_conflict_when_primary_is_also_extra(setup):
    service, ops, _ = setup
    ops.extras.append(ManagedDomain("a.spanel", "spanel-a-wp", "internal", "spanel-a.yml"))
    items, _ = service.list_domains("t1", can_manage=True)
    primary = next(item for item in items if item.domain == "a.spanel")
    assert primary.owner == "hosting"
    assert primary.conflict is True
    assert len([item for item in items if item.domain == "a.spanel"]) == 1


def test_add_rejects_primary(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.add(ctx(), site_id="site-b", domain="a.spanel", www=False)
    assert exc.value.code == "domains.primary_owned"
    assert ops.added == []


def test_add_rejects_primary_www_variant(setup):
    service, ops, _ = setup
    service._test_sites.append(  # type: ignore[attr-defined]
        OwnedSite("site-d", "t1", "spanel-d-wp", "www.d.spanel")
    )
    with pytest.raises(AppError) as exc:
        service.add(ctx(), site_id="site-b", domain="d.spanel", www=True)
    assert exc.value.code == "domains.primary_owned"
    assert ops.added == []


def test_add_rejects_extra_owned_by_another_site(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.add(ctx(), site_id="site-b", domain="b.spanel", www=False)
    assert exc.value.code == "domains.already_claimed"
    assert ops.added == []


def test_add_writes_and_audits(setup):
    service, ops, audits = setup
    items = service.add(ctx(), site_id="site-b", domain="d.spanel", www=True)

    assert ops.added == [("spanel-b-wp", "d.spanel", True)]
    assert [item.domain for item in items] == ["d.spanel", "www.d.spanel"]
    assert audits[-1]["module"] == "domains"
    assert audits[-1]["action"] == "domains.domain.added"
    assert audits[-1]["details"]["container"] == "spanel-b-wp"


def test_remove_primary_only_is_rejected(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.remove(ctx(), "a.spanel")
    assert exc.value.code == "domains.primary_owned"
    assert ops.removed == []


def test_remove_extra_works(setup):
    service, ops, audits = setup
    service.remove(ctx(), "b.spanel")
    assert ops.removed == ["b.spanel"]
    assert audits[-1]["action"] == "domains.domain.removed"


def test_remove_conflict_removes_only_the_extra(setup):
    service, ops, _ = setup
    ops.extras.append(ManagedDomain("a.spanel", "spanel-a-wp", "internal", "spanel-a.yml"))
    service.remove(ctx(), "a.spanel")
    assert ops.removed == ["a.spanel"]


def test_remove_unknown_is_404(setup):
    service, _, _ = setup
    with pytest.raises(NotFoundError):
        service.remove(ctx(), "nope.spanel")


def test_certificate_only_for_visible_domains(setup):
    service, _, _ = setup
    assert service.certificate("t1", "a.spanel").state == "active"
    assert service.certificate("t1", "b.spanel").verified is True
    with pytest.raises(NotFoundError):
        service.certificate("t1", "c.spanel")
