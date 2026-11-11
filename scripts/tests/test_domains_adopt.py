from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

import domains_adopt
from plugins.domains.backend.sync import Observation, SyncPlan
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.kernel.tenants.models import Tenant


def setup():
    engine = create_engine("sqlite://")
    Tenant.__table__.create(engine)
    db = Session(engine)
    db.add_all(
        [Tenant(id="t1", name="One", slug="one"), Tenant(id="t2", name="Two", slug="two")]
    )
    db.commit()
    app = FastAPI()
    owners: dict[str, str] = {}
    register_domain_providers(
        app,
        "test",
        list_domains=lambda _db, tenant: [d for d, owner in owners.items() if owner == tenant],
        owner=lambda _db, domain: owners.get(domain),
        claim=lambda _db, tenant, domain: owners.setdefault(domain, tenant),
    )
    return db, app, owners


def adoption_plan() -> SyncPlan:
    return SyncPlan(
        generation="g",
        documents=1,
        observations=[Observation("one.test", "t1", "s1", "site", "r", "one.yml", "h")],
    )


def test_adoption_is_dry_run_by_default(monkeypatch):
    db, app, owners = setup()
    monkeypatch.setattr(domains_adopt, "prepare_sync", lambda *_args, **_kwargs: adoption_plan())
    result = domains_adopt.adopt_domain(
        db, app, object(), domain="ONE.TEST", tenant_value="one", apply=False
    )
    assert result["applied"] is False
    assert owners == {}


def test_adoption_claims_free_domain_and_syncs(monkeypatch):
    db, app, owners = setup()
    calls: list[str] = []
    monkeypatch.setattr(domains_adopt, "prepare_sync", lambda *_args, **_kwargs: adoption_plan())
    monkeypatch.setattr(domains_adopt, "apply_sync", lambda *_: calls.append("sync"))
    monkeypatch.setattr(domains_adopt, "record_audit", lambda *_args, **_kwargs: calls.append("audit"))
    domains_adopt.adopt_domain(
        db, app, object(), domain="one.test", tenant_value="t1", apply=True
    )
    assert owners == {"one.test": "t1"}
    assert calls == ["sync", "audit"]


def test_adoption_rejects_cross_tenant(monkeypatch):
    db, app, _ = setup()
    monkeypatch.setattr(domains_adopt, "prepare_sync", lambda *_args, **_kwargs: adoption_plan())
    with pytest.raises(domains_adopt.AdoptionError, match="site belongs"):
        domains_adopt.adopt_domain(
            db, app, object(), domain="one.test", tenant_value="two", apply=False
        )
