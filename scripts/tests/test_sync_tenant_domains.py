"""Sincronización de `tenant_domains`: dry-run, apply, idempotencia y conflictos."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "vendor" / "systutor-core" / "src"))
sys.path.insert(0, str(REPO_ROOT / "vendor" / "systutor-core"))

import sync_tenant_domains
import systutor.kernel.models  # noqa: F401
from plugins.tenant.backend.models import TenantDomain
from systutor.core.database import Base
from systutor.kernel.tenants.models import Tenant


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def factory() -> sessionmaker[Session]:
    session_factory = _factory()
    with session_factory() as db:
        db.add_all(
            [
                Tenant(id="target", name="Demo", slug="demo", domain="spanel.site"),
                Tenant(id="old", name="Viejo", slug="old", domain="debug.com"),
            ]
        )
        db.add_all(
            [
                TenantDomain(domain="spanel.site", tenant_id="target"),
                TenantDomain(domain="test2.com", tenant_id="target"),
                TenantDomain(domain="debug.com", tenant_id="old"),
                TenantDomain(domain="extra.old.com", tenant_id="old"),
            ]
        )
        db.commit()
    yield session_factory
    engine = session_factory.kw["bind"]
    engine.dispose()


def _rows(factory: sessionmaker[Session]) -> set[tuple[str, str]]:
    with factory() as db:
        return {(row.domain, row.tenant_id) for row in db.scalars(select(TenantDomain))}


def test_dry_run_no_escribe_y_reporta(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    report = tmp_path / "report.json"

    code = sync_tenant_domains.run(["--report", str(report)], session_factory=factory)

    assert code == 0
    assert _rows(factory) == {
        ("spanel.site", "target"),
        ("test2.com", "target"),
        ("debug.com", "old"),
        ("extra.old.com", "old"),
    }
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied"] is False
    assert payload["counts"]["remove"] == 2
    assert sorted(item["domain"] for item in payload["remove"]) == [
        "debug.com",
        "spanel.site",
    ]


def test_apply_elimina_primarios_redundantes_y_es_idempotente(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    report = tmp_path / "report.json"

    assert sync_tenant_domains.run(["--apply", "--report", str(report)], session_factory=factory) == 0
    assert _rows(factory) == {("test2.com", "target"), ("extra.old.com", "old")}
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["applied"] is True

    second = tmp_path / "second.json"
    assert sync_tenant_domains.run(["--apply", "--report", str(second)], session_factory=factory) == 0
    assert json.loads(second.read_text(encoding="utf-8"))["counts"]["remove"] == 0


def test_conflicto_con_primario_ajeno_no_escribe(
    tmp_path: Path,
) -> None:
    session_factory = _factory()
    with session_factory() as db:
        db.add_all(
            [
                Tenant(id="target", name="Demo", slug="demo", domain="spanel.site"),
                Tenant(id="old", name="Viejo", slug="old", domain="debug.com"),
            ]
        )
        db.add(TenantDomain(domain="debug.com", tenant_id="target"))
        db.commit()

    report = tmp_path / "report.json"
    code = sync_tenant_domains.run(["--apply", "--report", str(report)], session_factory=session_factory)

    assert code == 1
    assert _rows(session_factory) == {("debug.com", "target")}
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["counts"]["conflicts"] == 1
    assert payload["conflicts"][0]["primary_tenant_slug"] == "old"


def test_dominio_invalido_se_reporta_y_no_rompe(
    tmp_path: Path,
) -> None:
    session_factory = _factory()
    with session_factory() as db:
        db.add(Tenant(id="bad", name="Malo", slug="bad", domain="https://x"))
        db.commit()

    report = tmp_path / "report.json"
    code = sync_tenant_domains.run(["--report", str(report)], session_factory=session_factory)

    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["counts"]["invalid"] == 1
