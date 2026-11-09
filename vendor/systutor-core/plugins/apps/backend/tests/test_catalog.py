from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend import catalog
from plugins.apps.backend import router as router_module
from plugins.apps.backend.models import (
    Deployment,
    GithubInstallation,
    GithubProvider,
    GithubRepository,
    Operation,
)
from plugins.apps.backend.schemas import ApplicationCreate, SourceUpdate
from plugins.apps.backend.settings import AppsSettings
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


def settings(
    tmp_path: Path, *, key: bool = True, public: str = "https://panel.example"
) -> AppsSettings:
    key_file = tmp_path / "apps_key"
    if key:
        key_file.write_text("x" * 32, encoding="utf-8")
    else:
        key_file.unlink(missing_ok=True)
    return AppsSettings(
        encryption_key_file=key_file, public_url=public,
        redis_url="redis://unused", state_root=tmp_path / "state",
    )


def database(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    db.add(Tenant(id="t2", name="Other", slug="other"))
    db.add(User(
        id="u1", tenant_id="t1", email="u@example.test",
        full_name="U", password_hash="x",
    ))
    db.add(User(
        id="u2", tenant_id="t2", email="v@example.test",
        full_name="V", password_hash="x",
    ))
    db.add(GithubProvider(
        id="p1", tenant_id="t1", created_by_user_id="u1", github_app_id=1,
        app_slug="one", name="One", private_key_ciphertext=b"x",
        private_key_nonce=b"y", key_version=1, status="active",
    ))
    db.add(GithubProvider(
        id="p2", tenant_id="t2", created_by_user_id="u2", github_app_id=2,
        app_slug="two", name="Two", private_key_ciphertext=b"x",
        private_key_nonce=b"y", key_version=1, status="active",
    ))
    db.add(GithubInstallation(
        id="i1", provider_id="p1", tenant_id="t1", github_installation_id=11,
        account_id="5", account_login="acme", account_type="Organization",
        repository_selection="selected", permissions={"contents": "read"},
        status="active",
    ))
    db.add(GithubInstallation(
        id="i2", provider_id="p2", tenant_id="t2", github_installation_id=22,
        account_id="6", account_login="other", account_type="User",
        repository_selection="selected", permissions={}, status="active",
    ))
    db.add(GithubRepository(
        id="r1", installation_id="i1", tenant_id="t1", github_repository_id=101,
        full_name="acme/repo", private=True, default_branch="main",
        archived=False, disabled=False,
    ))
    db.add(GithubRepository(
        id="r2", installation_id="i2", tenant_id="t2", github_repository_id=202,
        full_name="other/repo", private=False, default_branch="trunk",
        archived=False, disabled=False,
    ))
    db.commit()
    return db


def context(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(
        current_tenant_id=tenant_id, current_branch_id=None, current_user_id="u1",
        current_permissions=("core.platform.admin",), current_warehouse_ids=None,
    )


def test_status_states_are_explicit_and_key_missing_is_error(tmp_path: Path) -> None:
    db = database(tmp_path)
    assert catalog.status(db, "t1", settings(tmp_path), production=True)["state"] == "connected"
    db.query(GithubInstallation).filter(GithubInstallation.id == "i1").update(
        {"status": "suspended"}
    )
    db.commit()
    assert catalog.status(db, "t1", settings(tmp_path), production=True)["state"] == "suspended"
    db.query(GithubInstallation).filter(GithubInstallation.id == "i1").delete()
    db.query(GithubProvider).filter(GithubProvider.id == "p1").update(
        {"status": "pending_installation"}
    )
    db.commit()
    assert catalog.status(db, "t1", settings(tmp_path), production=True)["state"] == (
        "pending_installation"
    )
    assert catalog.status(
        db, "t1", settings(tmp_path, key=False), production=True
    )["state"] == "error"


def test_status_api_function_works_without_the_secret_file(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    resources = SimpleNamespace(settings=settings(tmp_path, key=False))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(apps_resources=resources)))
    response = router_module.github_status(request, db=db, user=user)
    assert response["state"] == "error"
    assert response["configured"] is False


def test_create_application_derives_canonical_source(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    app = catalog.create_application(
        db, context(), user,
        ApplicationCreate(name="Demo", installation_id="i1", repository_id="r1"),
    )
    assert app.repository == "acme/repo"
    assert app.branch == "main"
    assert app.source_installation_id == "i1"
    assert app.source_repository_id == "r1"
    assert app.source_connection_user_id == "u1"
    assert app.source_status == "connected"


@pytest.mark.parametrize(
    ("installation_id", "repository_id", "code"),
    [
        ("i2", "r2", "apps.installation_not_found"),
        ("i1", "r2", "apps.repository_unauthorized"),
        ("i1", "missing", "apps.repository_unauthorized"),
    ],
)
def test_source_selection_is_tenant_and_installation_scoped(
    tmp_path: Path, installation_id: str, repository_id: str, code: str
) -> None:
    db = database(tmp_path)
    with pytest.raises(AppError) as error:
        catalog.select_source(db, "t1", installation_id, repository_id)
    assert error.value.code == code


def test_tombstoned_repository_blocks_application_source(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.query(GithubRepository).filter(GithubRepository.id == "r1").update(
        {"removed_at": datetime.now(UTC)}
    )
    db.commit()
    with pytest.raises(AppError) as error:
        catalog.select_source(db, "t1", "i1", "r1")
    assert error.value.code == "apps.repository_unauthorized"


def test_update_source_and_revoke_mark_reconnect_required(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    app = catalog.create_application(
        db, context(), user,
        ApplicationCreate(name="Demo", installation_id="i1", repository_id="r1"),
    )
    catalog.update_source(
        db, app, SourceUpdate(installation_id="i1", repository_id="r1", branch="dev"), "u1"
    )
    assert app.branch == "dev"
    catalog.revoke_provider(db, "t1", "p1", "u1")
    db.refresh(app)
    assert app.source_status == "source_reconnect_required"
    assert db.get(GithubProvider, "p1").status == "revoked"
    with pytest.raises(AppError):
        catalog.select_source(db, "t1", "i1", "r1")


def test_detail_survives_reload_with_histories_services_and_candidates(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    app = catalog.create_application(
        db, context(), user,
        ApplicationCreate(name="Demo", installation_id="i1", repository_id="r1"),
    )
    deployment = Deployment(
        id="d1", application_id=app.id, operation_id="o1", repository="acme/repo",
        branch="main", commit="a" * 40, status="running", release="/tmp/release",
        manifest={"containers": {"web": "container-1", "db": "container-2"}},
    )
    db.add(deployment)
    db.flush()
    app.active_deployment_id = "d1"
    db.add(Operation(
        id="o2", application_id=app.id, tenant_id="t1", actor_user_id="u1",
        kind="inspect", status="succeeded", idempotency_key="k", request_hash="h",
        result={
            "compose_candidates": ["compose.yaml"],
            "services": ["api", "web"],
            "findings": [{"severity": "warning", "code": "x"}],
        },
    ))
    db.commit()
    detail = catalog.application_detail(db, "t1", app.id)
    assert detail["active_deployment"]["commit"] == "a" * 40
    assert detail["services"] == ["db", "web"]
    assert [
        service["name"] for service in detail["active_deployment"]["service_details"]
    ] == ["db", "web"]
    assert detail["compose_candidates"] == ["compose.yaml"]
    assert detail["findings"][0]["code"] == "x"
    assert detail["deployments"][0]["id"] == "d1"
    app.active_deployment_id = None
    db.commit()
    assert catalog.application_detail(db, "t1", app.id)["services"] == ["api", "web"]


def test_repository_views_exclude_tombstones_and_cross_tenant(tmp_path: Path) -> None:
    db = database(tmp_path)
    assert [row["full_name"] for row in catalog.repository_views(db, "t1", "i1")] == [
        "acme/repo"
    ]
    db.query(GithubRepository).filter(GithubRepository.id == "r1").update(
        {"removed_at": datetime.now(UTC)}
    )
    db.commit()
    assert catalog.repository_views(db, "t1", "i1") == []
    with pytest.raises(AppError):
        catalog.repository_views(db, "t1", "i2")


def test_status_returns_explicit_error_when_catalog_tables_are_missing(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    Tenant.__table__.create(engine)
    User.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
        db.add(User(
            id="u1", tenant_id="t1", email="u@example.test",
            full_name="U", password_hash="x",
        ))
        db.commit()
        user = db.get(User, "u1")
        result = catalog.status(db, "t1", settings(tmp_path), production=True)
        assert result["state"] == "error"
        assert result["configured"] is True
        assert result["connected"] is False

        resources = SimpleNamespace(settings=settings(tmp_path))
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(apps_resources=resources))
        )
        response = router_module.github_status(request, db=db, user=user)
        assert response["state"] == "error"
