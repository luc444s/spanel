from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend import router as router_module
from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.models import (
    Application,
    GithubInstallation,
    GithubProvider,
    GithubRepository,
    Operation,
)
from plugins.apps.backend.operations import create_operation, reconcile
from plugins.apps.backend.runtime.deployment import _state_root
from plugins.apps.backend.schemas import DeleteRequest, DeployRequest
from plugins.apps.backend.worker import AppsWorker
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


def database(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    db.add(Tenant(id="t2", name="Other", slug="other"))
    for user_id, tenant_id in (("u1", "t1"), ("u2", "t2")):
        db.add(User(
            id=user_id, tenant_id=tenant_id, email=f"{user_id}@example.test",
            full_name=user_id, password_hash="x",
        ))
    db.add(GithubProvider(
        id="p1", tenant_id="t1", created_by_user_id="u1", github_app_id=1,
        app_slug="one", name="One", private_key_ciphertext=b"x",
        private_key_nonce=b"y", key_version=1, status="active",
    ))
    db.add(GithubInstallation(
        id="i1", provider_id="p1", tenant_id="t1", github_installation_id=11,
        account_id="5", account_login="acme", repository_selection="selected",
        permissions={}, status="active",
    ))
    db.add(GithubRepository(
        id="r1", installation_id="i1", tenant_id="t1", github_repository_id=101,
        full_name="acme/repo", private=True, default_branch="main",
        archived=False, disabled=False,
    ))
    db.add(Application(
        id="a1", tenant_id="t1", name="Demo", slug="demo", repository="acme/repo",
        branch="main", source_connection_user_id="u1", source_installation_id="i1",
        source_repository_id="r1", source_status="connected", runtime_project="app-demo",
        active_deployment_id=None,
    ))
    db.commit()
    return db


def context(tenant_id: str = "t1", platform: bool = False) -> TenantContext:
    permissions = ("apps.deployments.create", "apps.applications.delete")
    if platform:
        permissions = (*permissions, "core.platform.admin")
    return TenantContext(
        current_tenant_id=tenant_id, current_branch_id=None, current_user_id="u1",
        current_permissions=permissions, current_warehouse_ids=None,
    )


def test_local_runtime_uses_shared_apps_state_root() -> None:
    assert _state_root("local", {
        "SPANEL_APPS_STATE_ROOT": "/var/lib/spanel/apps",
        "SPANEL_APP_STATE_ROOT": "/legacy/path",
    }) == Path("/var/lib/spanel/apps")
    assert _state_root("local", {
        "SPANEL_APP_STATE_ROOT": "/legacy/path",
    }) == Path("/legacy/path")


def test_deploy_and_delete_keep_the_platform_double_gate(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    request = SimpleNamespace(headers={})
    with pytest.raises(AppError) as deploy:
        router_module.deploy_application(
            "a1", DeployRequest(), request, "key", db, context(), user,
        )
    assert deploy.value.status_code == 403
    with pytest.raises(AppError) as delete:
        router_module.delete_application(
            "a1", request, DeleteRequest(confirm="demo"), "key", db, context(), user,
        )
    assert delete.value.status_code == 403


def test_deploy_prepends_stable_managed_database_environment(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")

    class Dispatcher:
        def enqueue(self, _operation_id: str) -> str:
            return "message"

    resources = SimpleNamespace(
        settings=SimpleNamespace(state_root=tmp_path / "state"),
        cipher=lambda: TokenCipher(b"k" * 32),
        dispatcher=Dispatcher(),
    )
    request = SimpleNamespace(
        headers={}, app=SimpleNamespace(state=SimpleNamespace(apps_resources=resources))
    )
    operation = router_module.deploy_application(
        "a1",
        DeployRequest(environment=[("DATABASE_URL", "scheme://${APP_DB_USER}@database")]),
        request,
        "managed-key",
        db,
        context(platform=True),
        user,
    )
    pending = resources.settings.state_root / "pending" / f"{operation.operation_id}.env"
    lines = pending.read_text(encoding="utf-8").splitlines()
    assert [line.split("=", 1)[0] for line in lines] == [
        "APP_DB_NAME", "APP_DB_USER", "APP_DB_PASSWORD", "DATABASE_URL",
    ]
    assert lines[-1] == "DATABASE_URL=scheme://${APP_DB_USER}@database"
    app = db.get(Application, "a1")
    assert app.managed_env_ciphertext is not None
    stored = db.get(Operation, operation.operation_id)
    assert stored.report["tenant_environment_names"] == ["DATABASE_URL"]
    assert "scheme://" not in str(stored.report)


def test_deploy_rejects_managed_database_override(tmp_path: Path) -> None:
    db = database(tmp_path)
    user = db.get(User, "u1")
    resources = SimpleNamespace(cipher=lambda: TokenCipher(b"k" * 32))
    request = SimpleNamespace(
        headers={}, app=SimpleNamespace(state=SimpleNamespace(apps_resources=resources))
    )
    with pytest.raises(AppError) as error:
        router_module.deploy_application(
            "a1", DeployRequest(environment=[("APP_DB_NAME", "manual")]),
            request, "managed-override", db, context(platform=True), user,
        )
    assert error.value.code == "apps.environment_managed"


def test_cross_tenant_application_access_is_not_found(tmp_path: Path) -> None:
    db = database(tmp_path)
    with pytest.raises(AppError) as error:
        router_module.get_application("a1", db, context("t2"))
    assert error.value.status_code == 404


def test_tombstoned_repository_blocks_inspect_before_any_github_call(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.query(GithubRepository).filter(GithubRepository.id == "r1").update(
        {"removed_at": datetime.now(UTC)}
    )
    db.commit()
    github = SimpleNamespace(
        installation_token=lambda *args, **kwargs: pytest.fail("must not request a token")
    )
    worker = AppsWorker(
        lambda: Session(db.get_bind()), TokenCipher(b"k" * 32), github=github
    )
    with pytest.raises(RuntimeError, match="reconnect"):
        worker._source_operation("op", "a1", "inspect")
    assert db.get(Application, "a1").source_status == "source_reconnect_required"


def test_sync_rejects_cross_tenant_subject_without_external_io(tmp_path: Path) -> None:
    db = database(tmp_path)
    db.add(Operation(
        id="op", application_id=None, tenant_id="t2", actor_user_id="u2",
        subject_type="installation", subject_id="i1", kind="github_sync",
        status="running", idempotency_key="k", request_hash="h",
    ))
    db.commit()
    github = SimpleNamespace(
        installation_token=lambda *args, **kwargs: pytest.fail("must not request a token")
    )
    worker = AppsWorker(
        lambda: Session(db.get_bind()), TokenCipher(b"k" * 32), github=github
    )
    with pytest.raises(RuntimeError, match="not found"):
        worker._sync_operation("op", "installation", "i1")


def test_reconcile_requeues_only_the_operation_id_and_clears_stale_locks(tmp_path: Path) -> None:
    db = database(tmp_path)
    app = db.get(Application, "a1")
    queued, _ = create_operation(
        db, application=app, actor_user_id="u1", kind="inspect",
        idempotency_key="queued", payload={},
    )
    running, _ = create_operation(
        db, application=None, actor_user_id="u1", kind="github_sync",
        idempotency_key="running", payload={}, tenant_id="t1",
        subject_type="installation", subject_id="i1",
    )
    running.status = "running"
    running.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    class Broker:
        def __init__(self):
            self.args = []

        def enqueue(self, operation_id):
            self.args.append(operation_id)
            return "message"

    broker = Broker()
    requeued, interrupted = reconcile(db, broker)
    assert requeued == 1 and interrupted == 1
    assert broker.args == [queued.id]
    db.refresh(running)
    assert running.status == "interrupted"
    assert running.error_code == "apps_worker_interrupted"
