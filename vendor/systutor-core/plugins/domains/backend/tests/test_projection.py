from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from plugins.domains.backend.models import DomainRoute, DomainSyncState, ObservedDomain
from plugins.domains.backend.proxy import ManagedDocument, render_site_yaml
from plugins.domains.backend.service import DomainsService
from plugins.domains.backend.sync import apply_sync, prepare_sync
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.kernel.tenants.models import Tenant


class SnapshotOperations:
    def __init__(self, documents):
        self.documents = documents
        self.calls = 0

    def managed_documents(self):
        self.calls += 1
        return self.documents


def setup_db():
    engine = create_engine("sqlite://")
    Tenant.__table__.create(engine)
    DomainRoute.__table__.create(engine)
    ObservedDomain.__table__.create(engine)
    DomainSyncState.__table__.create(engine)
    db = Session(engine)
    db.add_all(
        [
            Tenant(id="t1", name="One", slug="one"),
            Tenant(id="t2", name="Two", slug="two"),
        ]
    )
    db.commit()
    app = FastAPI()
    sites = [
        OwnedSite("s1", "t1", "site-one", None),
        OwnedSite("s2", "t2", "site-two", None),
    ]
    register_site_provider(
        app,
        "test",
        lambda _db, tenant_id: sites
        if tenant_id is None
        else [site for site in sites if site.tenant_id == tenant_id],
    )
    register_domain_providers(
        app, "test", list_domains=lambda _db, _tenant: [], owner=lambda _db, _domain: None
    )
    return db, app


def document(name: str, container: str, domain: str) -> ManagedDocument:
    return ManagedDocument(Path(name), render_site_yaml(container, [domain], "test"))


def test_complete_generation_is_idempotent_and_global_read_avoids_proxy():
    db, app = setup_db()
    operations = SnapshotOperations(
        [
            document("spanel-one.yml", "site-one", "one.test"),
            document("spanel-two.yml", "site-two", "two.test"),
        ]
    )
    first = prepare_sync(db, app, operations)  # type: ignore[arg-type]
    apply_sync(db, first)
    db.commit()
    second = prepare_sync(db, app, operations)  # type: ignore[arg-type]
    assert second.additions == second.changes == second.removals == []
    apply_sync(db, second)
    db.commit()

    service = DomainsService(db, app)
    service._operations = lambda: (_ for _ in ()).throw(AssertionError("proxy read"))  # type: ignore[method-assign]
    items, _, error = service.list_all_domains(active_tenant_id="t1", can_manage_active=True)
    assert {item.domain for item in items} == {"one.test", "two.test"}
    assert next(item for item in items if item.domain == "one.test").editable is True
    assert next(item for item in items if item.domain == "two.test").editable is False
    assert error is None


def test_invalid_snapshot_never_replaces_active_generation():
    db, app = setup_db()
    valid = prepare_sync(
        db, app, SnapshotOperations([document("spanel-one.yml", "site-one", "one.test")])
    )
    apply_sync(db, valid)
    db.commit()
    generation = db.get(DomainSyncState, 1).active_generation

    invalid = prepare_sync(
        db,
        app,
        SnapshotOperations([ManagedDocument(Path("spanel-bad.yml"), "not: [yaml")]),
    )
    assert invalid.ok is False
    assert invalid.invalid
    assert db.get(DomainSyncState, 1).active_generation == generation


def test_cross_tenant_owner_is_a_conflict():
    db, app = setup_db()
    app.state.domain_namespace_providers["test"]["owner"] = lambda _db, _domain: "t2"
    plan = prepare_sync(
        db, app, SnapshotOperations([document("spanel-one.yml", "site-one", "one.test")])
    )
    assert plan.ok is False
    assert "differs from site tenant" in plan.conflicts[0]
