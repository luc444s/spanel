"""Unificación de tenants: dry-run, apply, idempotencia, rollback y purge."""

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

import systutor.kernel.models  # noqa: F401
import unify_tenants
from plugins.hosting.backend.models import HostingSite
from plugins.tenant.backend.models import TenantDomain
from systutor.core.database import Base
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant


@pytest.fixture()
def factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        db.add_all(
            [
                Tenant(id="target", name="Demo", slug="demo", domain="spanel.site"),
                Tenant(id="old1", name="Viejo uno", slug="old1", domain="old1.com"),
                Tenant(id="old2", name="Viejo dos", slug="old2", domain="old2.com"),
            ]
        )
        db.add_all(
            [
                HostingSite(id="s1", tenant_id="old1", container_name="spanel-s1-wp", domain="spanel.site"),
                HostingSite(id="s2", tenant_id="old1", container_name="spanel-s2-wp", domain="debug.com"),
                HostingSite(id="s3", tenant_id="old2", container_name="spanel-s3-wp", domain="test2.com"),
                HostingSite(id="s4", tenant_id="old2", container_name="spanel-s4-web", domain="otro.com"),
            ]
        )
        db.add_all(
            [
                User(id="u-target", tenant_id="target", email="dup@x.com", full_name="Dup", password_hash="x"),
                User(id="u-a", tenant_id="old1", email="a@x.com", full_name="A", password_hash="x"),
                User(id="u-b", tenant_id="old2", email="b@x.com", full_name="B", password_hash="x"),
            ]
        )
        db.commit()
    yield session_factory
    engine.dispose()


def _args(plan: Path, *extra: str) -> list[str]:
    return [
        "--target-slug",
        "demo",
        "--all-others",
        "--move-all-sites",
        "--domain",
        "spanel.site",
        "--domain",
        "debug.com",
        "--domain",
        "test2.com",
        "--plan",
        str(plan),
        *extra,
    ]


def _state(factory: sessionmaker[Session]) -> dict[str, object]:
    with factory() as db:
        return {
            "sites": {
                row.id: row.tenant_id for row in db.scalars(select(HostingSite))
            },
            "users": {
                row.id: (row.tenant_id, row.is_active) for row in db.scalars(select(User))
            },
            "tenants": {
                row.id: (row.is_active, row.slug, row.domain)
                for row in db.scalars(select(Tenant))
            },
            "domains": {
                row.domain: row.tenant_id for row in db.scalars(select(TenantDomain))
            },
        }


def test_dry_run_no_escribe_y_escribe_plan(tmp_path, factory) -> None:
    plan = tmp_path / "plan.json"
    before = _state(factory)

    code = unify_tenants.run(_args(plan), session_factory=factory)

    assert code == 0
    assert _state(factory) == before
    payload = json.loads(plan.read_text())
    assert payload["applied"] is False
    assert len(payload["sites"]) == 4
    assert {item["slug"] for item in payload["origins"]} == {"old1", "old2"}


def test_apply_mueve_reclama_desactiva_y_audita(tmp_path, factory) -> None:
    plan = tmp_path / "plan.json"

    code = unify_tenants.run(_args(plan, "--apply"), session_factory=factory)

    assert code == 0
    with factory() as db:
        assert {row.tenant_id for row in db.scalars(select(HostingSite))} == {"target"}
        assert db.get(Tenant, "old1").is_active is False
        assert db.get(Tenant, "old2").is_active is False
        assert db.get(Tenant, "old1").domain == "old1.com"
        assert db.get(Tenant, "old2").domain == "old2.com"
        assert db.get(User, "u-a").tenant_id == "target"
        assert db.get(User, "u-b").tenant_id == "target"
        assert {
            row.domain: row.tenant_id for row in db.scalars(select(TenantDomain))
        } == {
            "debug.com": "target",
            "test2.com": "target",
            "otro.com": "target",
        }
        actions = {
            row.action for row in db.scalars(select(AuditLog))
        }
        assert {
            "tenant.unify.site",
            "tenant.unify.user",
            "tenant.unify.domain",
            "tenant.unify.deactivate",
        } <= actions
        assert db.scalar(select(AuditLog.id).where(AuditLog.tenant_id == "old1")) is None


def test_limpia_primario_de_origen_y_rollback_lo_restaura(tmp_path, factory) -> None:
    plan = tmp_path / "plan.json"
    before = _state(factory)
    args = [
        "--target-slug",
        "demo",
        "--from-slug",
        "old1",
        "--domain",
        "old1.com",
        "--plan",
        str(plan),
    ]

    assert unify_tenants.run([*args, "--apply"], session_factory=factory) == 0
    with factory() as db:
        assert db.get(Tenant, "old1").domain is None
        row = db.get(TenantDomain, "old1.com")
        assert row is not None and row.tenant_id == "target"
        assert "tenant.unify.primary_cleared" in {
            item.action for item in db.scalars(select(AuditLog))
        }

    assert unify_tenants.run(
        ["--rollback", str(plan), "--apply"], session_factory=factory
    ) == 0
    assert _state(factory) == before


def test_segunda_ejecucion_no_cambia_filas(tmp_path, factory, capsys) -> None:
    plan = tmp_path / "plan.json"
    unify_tenants.run(_args(plan, "--apply"), session_factory=factory)
    before = _state(factory)

    code = unify_tenants.run(_args(tmp_path / "plan2.json", "--apply"), session_factory=factory)

    assert code == 0
    assert _state(factory) == before
    assert "aplicado: 0 cambios" in capsys.readouterr().out


def test_rollback_restaura_solo_lo_aplicado(tmp_path, factory) -> None:
    plan = tmp_path / "plan.json"
    before = _state(factory)
    unify_tenants.run(_args(plan, "--apply"), session_factory=factory)

    code = unify_tenants.run(
        ["--rollback", str(plan), "--apply"], session_factory=factory
    )

    assert code == 0
    assert _state(factory) == before


def test_rollback_sin_apply_falla(tmp_path, factory) -> None:
    plan = tmp_path / "plan.json"
    unify_tenants.run(_args(plan), session_factory=factory)

    code = unify_tenants.run(
        ["--rollback", str(plan)], session_factory=factory
    )

    assert code == 1


def test_purge_sin_dependencias_borra_tenant(tmp_path, factory) -> None:
    with factory() as db:
        db.add(Tenant(id="empty", name="Vacío", slug="empty"))
        db.commit()

    code = unify_tenants.run(
        [
            "--target-slug",
            "demo",
            "--from-slug",
            "empty",
            "--apply",
            "--purge",
            "--plan",
            str(tmp_path / "plan.json"),
        ],
        session_factory=factory,
    )

    assert code == 0
    with factory() as db:
        assert db.get(Tenant, "empty") is None


def test_purge_con_auditoria_no_borra_ni_escribe(tmp_path, factory) -> None:
    with factory() as db:
        record_audit(
            db,
            tenant_id="old2",
            branch_id=None,
            actor_user_id=None,
            actor_type="system",
            module="test",
            action="test.event",
            entity_type="test",
            entity_id="1",
            result="success",
            correlation_id=None,
            request_id=None,
            details={},
        )
        db.commit()
    before = _state(factory)

    code = unify_tenants.run(
        [
            "--target-slug",
            "demo",
            "--from-slug",
            "old2",
            "--apply",
            "--purge",
            "--plan",
            str(tmp_path / "plan.json"),
        ],
        session_factory=factory,
    )

    assert code == 1
    assert _state(factory) == before


def test_target_inexistente_falla_sin_escribir(tmp_path, factory) -> None:
    before = _state(factory)

    code = unify_tenants.run(
        [
            "--target-slug",
            "nope",
            "--from-slug",
            "old1",
            "--apply",
            "--plan",
            str(tmp_path / "plan.json"),
        ],
        session_factory=factory,
    )

    assert code == 1
    assert _state(factory) == before


def test_dominio_de_un_tercero_no_se_reclama(tmp_path, factory) -> None:
    with factory() as db:
        db.add(Tenant(id="third", name="Tercero", slug="third", domain="tercero.com"))
        db.add(TenantDomain(domain="tercero.com", tenant_id="third"))
        db.commit()
    before = _state(factory)

    code = unify_tenants.run(
        [
            "--target-slug",
            "demo",
            "--from-slug",
            "old1",
            "--domain",
            "tercero.com",
            "--apply",
            "--plan",
            str(tmp_path / "plan.json"),
        ],
        session_factory=factory,
    )

    assert code == 1
    assert _state(factory) == before


def test_script_no_importa_docker_ni_proxy() -> None:
    source = Path(unify_tenants.__file__).read_text()
    for forbidden in ("import docker", "proxyoperations(", "build_runner(", "certbot"):
        assert forbidden not in source.lower()
