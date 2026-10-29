from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend import service as service_module
from plugins.domains.backend.proxy import CertificateInfo, ManagedDomain
from plugins.domains.backend.service import DomainsService
from systutor.contracts.domain_namespace import (
    DomainConflictError,
    register_domain_providers,
)
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

    owned: dict[str, set[str]] = {
        "t1": {"a.spanel", "b.spanel"},
        "t2": {"c.spanel"},
    }

    def list_owned(db, tenant_id):  # noqa: ANN001
        return sorted(owned.get(tenant_id, set()))

    def owner_of(db, domain):  # noqa: ANN001
        for tenant_id, names in owned.items():
            if domain in names:
                return tenant_id
        return None

    def claim(db, tenant_id, domain):  # noqa: ANN001
        current = owner_of(db, domain)
        if current is not None and current != tenant_id:
            raise DomainConflictError(domain, current)
        owned.setdefault(tenant_id, set()).add(domain)

    def release(db, tenant_id, domain):  # noqa: ANN001
        current = owner_of(db, domain)
        if current is None or current != tenant_id:
            raise AppError(
                "Dominio desconocido", status_code=404, code="domains.domain_not_found"
            )
        owned.get(tenant_id, set()).discard(domain)

    register_domain_providers(
        app, "tenant", list_domains=list_owned, owner=owner_of, claim=claim, release=release
    )


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
    items, sites, err = service.list_domains("t1", can_manage=True)
    by_domain = {item.domain: item for item in items}

    assert by_domain["a.spanel"].owner == "hosting"
    assert by_domain["a.spanel"].editable is False
    assert by_domain["b.spanel"].owner == "domains"
    assert by_domain["b.spanel"].editable is True
    assert "c.spanel" not in by_domain, "extra de otro tenant"
    assert [site.container for site in sites] == ["spanel-a-wp", "spanel-b-wp"]
    assert err is None


def test_list_marks_conflict_when_primary_is_also_extra(setup):
    service, ops, _ = setup
    ops.extras.append(ManagedDomain("a.spanel", "spanel-b-wp", "internal", "spanel-b.yml"))
    items, _, _ = service.list_domains("t1", can_manage=True)
    primary = next(item for item in items if item.domain == "a.spanel")
    assert primary.owner == "hosting"
    assert primary.conflict is True
    assert len([item for item in items if item.domain == "a.spanel"]) == 1


def test_primary_published_by_its_own_container_is_not_conflict(setup):
    """A static site's primary lives in its own managed YAML: one writer."""
    service, ops, _ = setup
    ops.extras.append(
        ManagedDomain("a.spanel", "spanel-a-wp", "internal", "spanel-a-web.yml")
    )
    items, _, _ = service.list_domains("t1", can_manage=True)
    primary = next(item for item in items if item.domain == "a.spanel")
    assert primary.owner == "hosting"
    assert primary.conflict is False
    assert primary.editable is False
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
    ops.extras.append(ManagedDomain("a.spanel", "spanel-b-wp", "internal", "spanel-b.yml"))
    service.remove(ctx(), "a.spanel")
    assert ops.removed == ["a.spanel"]


def test_remove_primary_published_by_own_container_is_rejected(setup):
    service, ops, _ = setup
    ops.extras.append(
        ManagedDomain("a.spanel", "spanel-a-wp", "internal", "spanel-a-web.yml")
    )
    with pytest.raises(AppError) as exc:
        service.remove(ctx(), "a.spanel")
    assert exc.value.code == "domains.primary_owned"
    assert ops.removed == []


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


# ---- A.SPEC 0040: reservar sin sitio, vincular y liberar ---------------------


def test_add_reserved_nunca_toca_el_proxy(setup):
    service, ops, audits = setup
    items = service.add(ctx(), site_id=None, domain="d.com", www=True)

    assert ops.added == [] and ops.removed == []
    assert [item.domain for item in items] == ["d.com", "www.d.com"]
    assert all(item.published is False and item.site_id is None for item in items)
    audit = audits[-1]
    assert audit["action"] == "domains.domain.added"
    assert audit["details"]["mode"] == "reserved"
    assert audit["details"]["site_id"] is None
    assert audit["details"]["container"] is None


def test_add_reserved_es_idempotente_para_el_mismo_tenant(setup):
    service, ops, _ = setup
    first = service.add(ctx(), site_id=None, domain="d.com", www=False)
    second = service.add(ctx(), site_id=None, domain="d.com", www=False)

    assert [item.published for item in first] == [False]
    assert [item.published for item in second] == [False]


def test_add_reserved_otro_tenant_es_409(setup):
    service, ops, _ = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    with pytest.raises(AppError) as exc:
        service.add(ctx("t2"), site_id=None, domain="d.com", www=False)
    assert exc.value.code == "domains.already_owned"
    assert exc.value.status_code == 409
    assert ops.added == []


def test_add_reserved_con_dominio_ya_reclamado(setup):
    service, ops, _ = setup
    # b.spanel es extra publicado por spanel-a-wp del propio tenant: sigue
    # caminando idempotente porque el contenedor ya es del tenant.
    items = service.add(ctx(), site_id=None, domain="d.com", www=False)
    assert items[0].published is False

    with pytest.raises(AppError) as exc:
        service.add(ctx("t2"), site_id=None, domain="b.spanel", www=False)
    assert exc.value.code == "domains.already_claimed"


def test_link_publica_y_audita(setup):
    service, ops, audits = setup
    service.add(ctx(), site_id=None, domain="d.com", www=True)

    items = service.link(ctx(), domain="d.com", site_id="site-b", www=True)

    assert ops.added == [("spanel-b-wp", "d.com", True)]
    assert all(item.published is True for item in items)
    assert all(item.container == "spanel-b-wp" for item in items)
    audit = audits[-1]
    assert audit["action"] == "domains.domain.linked"
    assert audit["details"]["site_id"] == "site-b"
    assert audit["details"]["container"] == "spanel-b-wp"


def test_link_exige_propiedad_del_tenant(setup):
    service, ops, _ = setup
    with pytest.raises(AppError) as exc:
        service.link(ctx(), domain="c.spanel", site_id="site-a", www=False)
    assert exc.value.status_code == 404
    assert exc.value.code == "domains.domain_not_found"
    assert ops.added == []


def test_link_sitio_desconocido_es_404(setup):
    service, _, _ = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    with pytest.raises(AppError) as exc:
        service.link(ctx(), domain="d.com", site_id="nope", www=False)
    assert exc.value.status_code == 404
    assert exc.value.code == "domains.site_not_found"


def test_link_con_proxy_caido_es_503_y_conserva_reserva(setup):
    service, ops, _ = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    def broken():
        raise AppError("proxy caído", status_code=503, code="domains.proxy_unavailable")

    service._ready = broken  # type: ignore[method-assign]
    with pytest.raises(AppError) as exc:
        service.link(ctx(), domain="d.com", site_id="site-b", www=False)
    assert exc.value.status_code == 503
    assert ops.added == []
    # La reserva sigue siendo del tenant.
    items, _, _ = service.list_domains("t1", can_manage=True)
    assert "d.com" in {item.domain for item in items}


def test_certificate_reservado_sin_probe(setup):
    service, ops, _ = setup
    calls: list[str] = []
    original = ops.certificate

    def spy(domain):
        calls.append(domain)
        return original(domain)

    ops.certificate = spy  # type: ignore[method-assign]
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    info = service.certificate("t1", "d.com")
    assert info.state == "unpublished"
    assert info.verified is False
    assert calls == []


def test_remove_reservado_libera_por_contrato(setup):
    service, ops, audits = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    service.remove(ctx(), "d.com")

    assert ops.removed == []
    assert audits[-1]["action"] == "domains.domain.removed"
    assert audits[-1]["details"]["mode"] == "reserved"
    with pytest.raises(NotFoundError):
        service.remove(ctx(), "d.com")


def test_list_incluye_reservados(setup):
    service, ops, _ = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    items, _, err = service.list_domains("t1", can_manage=True)
    by_domain = {item.domain: item for item in items}
    assert by_domain["d.com"].published is False
    assert by_domain["d.com"].site_id is None
    assert err is None


def test_list_respecta_aislamiento_de_reservas(setup):
    service, _, _ = setup
    service.add(ctx(), site_id=None, domain="d.com", www=False)

    items, _, _ = service.list_domains("t2", can_manage=True)
    assert "d.com" not in {item.domain for item in items}
