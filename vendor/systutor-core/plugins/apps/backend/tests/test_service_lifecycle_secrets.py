"""Lifecycle por servicio, detalles dinámicos y secretos administrados."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend import catalog
from plugins.apps.backend import router as router_module
from plugins.apps.backend import worker as worker_module
from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.managed_environment import managed_database_environment
from plugins.apps.backend.models import Application, Deployment, Operation
from plugins.apps.backend.schemas import ActionRequest, SecretRevealRequest
from plugins.apps.backend.worker import AppsWorker
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    get_current_user,
)
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


class Dispatcher:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def enqueue(self, operation_id: str) -> str:
        self.ids.append(operation_id)
        return "message"


def database(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
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
        db.add(Application(
            id="a1", tenant_id="t1", name="Demo", slug="demo", repository="acme/repo",
            branch="main", source_connection_user_id="u1", runtime_project="app-demo",
            active_deployment_id="d1",
        ))
        db.add(Application(
            id="a2", tenant_id="t2", name="Other", slug="other", repository="acme/other",
            branch="main", source_connection_user_id="u2", runtime_project="app-other",
        ))
        db.add(Deployment(
            id="d1", application_id="a1", operation_id="op-old", repository="acme/repo",
            branch="main", status="running", manifest={
                "containers": {"web": "container-web", "db": "container-db"},
                "images": {"web": "app:web", "db": "postgres:16"},
                "service_environment": {
                    "web": ["APP_DB_PASSWORD", "JWT_SECRET_KEY"],
                    "db": ["POSTGRES_PASSWORD"],
                },
                "service_ports": {"web": [8000], "db": [5432]},
                "service_runtime": {
                    "web": {
                        "status": "running",
                        "health": "healthy",
                        "restart_count": 2,
                        "created_at": "2026-11-09T00:00:00Z",
                    }
                },
            },
        ))
        db.commit()
    return engine


def context(permissions: tuple[str, ...]) -> TenantContext:
    return TenantContext(
        current_tenant_id="t1",
        current_branch_id=None,
        current_user_id="u1",
        current_permissions=permissions,
        current_warehouse_ids=None,
    )


def client(engine, permissions: tuple[str, ...], *, cipher: TokenCipher | None = None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router_module.router, prefix="/api/v1/plugins/apps")
    dispatcher = Dispatcher()
    app.state.apps_resources = SimpleNamespace(
        dispatcher=dispatcher,
        cipher=lambda: cipher or TokenCipher(b"k" * 32),
    )
    user = SimpleNamespace(id="u1", tenant_id="t1")
    tenant_context = context(permissions)

    def session():
        with Session(engine) as db:
            yield db

    from systutor.api.deps import get_db_session

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_tenant_context] = lambda: tenant_context
    return TestClient(app), dispatcher


def test_action_request_rejects_runtime_selectors() -> None:
    with pytest.raises(ValidationError):
        ActionRequest.model_validate({"service": "web", "container": "container-web"})
    with pytest.raises(ValidationError):
        ActionRequest(service="../../etc/passwd")


def test_service_action_persists_app_service_subject_and_idempotency(tmp_path: Path) -> None:
    engine = database(tmp_path)
    http, dispatcher = client(engine, ("apps.applications.manage",))
    headers = {"Idempotency-Key": "service-key"}
    response = http.post(
        "/api/v1/plugins/apps/applications/a1/actions/restart",
        json={"service": "web"}, headers=headers,
    )
    assert response.status_code == 202
    operation_id = response.json()["operation_id"]
    assert dispatcher.ids == [operation_id]
    with Session(engine) as db:
        operation = db.get(Operation, operation_id)
        assert operation.subject_type == "app_service"
        assert operation.subject_id == "a1:web"
        assert operation.report == {"service": "web"}

    same = http.post(
        "/api/v1/plugins/apps/applications/a1/actions/restart",
        json={"service": "web"}, headers=headers,
    )
    assert same.status_code == 202
    assert same.json()["operation_id"] == operation_id

    conflict = http.post(
        "/api/v1/plugins/apps/applications/a1/actions/restart",
        json={"service": "db"}, headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "apps.idempotency_conflict"


def test_service_action_validates_active_deployment_and_down(tmp_path: Path) -> None:
    engine = database(tmp_path)
    http, _ = client(engine, ("apps.applications.manage",))
    unknown = http.post(
        "/api/v1/plugins/apps/applications/a1/actions/restart",
        json={"service": "missing"}, headers={"Idempotency-Key": "unknown"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "apps.invalid_service"

    down = http.post(
        "/api/v1/plugins/apps/applications/a1/actions/down",
        json={"service": "web"}, headers={"Idempotency-Key": "down"},
    )
    assert down.status_code == 422
    assert down.json()["error"]["code"] == "apps.service_scope_forbidden"


def test_worker_revalidates_service_before_lifecycle(tmp_path: Path, monkeypatch) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        db.add(Operation(
            id="op-service", application_id="a1", tenant_id="t1",
            actor_user_id="u1", subject_type="app_service", subject_id="a1:missing",
            kind="restart", status="running", idempotency_key="op",
            request_hash="h", report={"service": "missing"},
        ))
        db.commit()
    called: list[object] = []
    monkeypatch.setattr(worker_module, "lifecycle", lambda *args, **kwargs: called.append(args))
    worker = AppsWorker(lambda: Session(engine), TokenCipher(b"k" * 32))
    with pytest.raises(RuntimeError, match="Service does not belong"):
        worker._lifecycle_operation("op-service", "a1", "restart")
    assert called == []


def test_service_details_are_dynamic_and_never_include_values(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        deployment = db.get(Deployment, "d1")
        view = catalog._deployment_view(deployment)
    web = next(item for item in view["service_details"] if item["name"] == "web")
    db_service = next(item for item in view["service_details"] if item["name"] == "db")
    assert web == {
        "name": "web",
        "image": "app:web",
        "status": "running",
        "health": "healthy",
        "ports": [8000],
        "restart_count": 2,
        "environment_names": ["APP_DB_PASSWORD", "JWT_SECRET_KEY"],
        "created_at": "2026-11-09T00:00:00Z",
    }
    assert db_service["health"] == "unknown"
    assert db_service["restart_count"] == 0
    assert db_service["ports"] == [5432]
    assert "JWT_SECRET_KEY" in web["environment_names"]
    assert "canary-secret" not in str(view)


def test_legacy_manifest_keeps_compatible_empty_fields(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        deployment = db.get(Deployment, "d1")
        deployment.manifest = {"containers": {"web": "container-web"}}
        view = catalog._deployment_view(deployment)
    detail = view["service_details"][0]
    assert detail["ports"] == []
    assert detail["health"] == "unknown"
    assert detail["restart_count"] == 0
    assert detail["environment_names"] == []


def test_secret_reveal_returns_no_store_audits_without_value(tmp_path: Path) -> None:
    engine = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    with Session(engine) as db:
        app = db.get(Application, "a1")
        values = managed_database_environment(db, app, cipher)
        db.commit()
        canary = values["APP_DB_PASSWORD"]
    http, _ = client(engine, ("apps.secrets.read",), cipher=cipher)
    response = http.post(
        "/api/v1/plugins/apps/applications/a1/secrets/APP_DB_PASSWORD/reveal",
        json={"reason": "Diagnóstico solicitado por el tenant"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"name": "APP_DB_PASSWORD", "value": canary}
    with Session(engine) as db:
        audit = db.scalar(select(AuditLog).where(
            AuditLog.action == "apps.secret.revealed"
        ))
        assert audit is not None
        assert audit.details["name"] == "APP_DB_PASSWORD"
        assert audit.details["services"] == ["web"]
        assert canary not in str(audit.details)


def test_secret_reveal_requires_permission_and_tenant(tmp_path: Path) -> None:
    engine = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    with Session(engine) as db:
        app = db.get(Application, "a1")
        managed_database_environment(db, app, cipher)
        db.commit()

    forbidden, _ = client(engine, ("apps.applications.read",), cipher=cipher)
    denied = forbidden.post(
        "/api/v1/plugins/apps/applications/a1/secrets/APP_DB_PASSWORD/reveal",
        json={"reason": "Diagnóstico"},
    )
    assert denied.status_code == 403

    allowed, _ = client(engine, ("apps.secrets.read",), cipher=cipher)
    cross = allowed.post(
        "/api/v1/plugins/apps/applications/a2/secrets/APP_DB_PASSWORD/reveal",
        json={"reason": "Diagnóstico"},
    )
    assert cross.status_code == 404
    missing = allowed.post(
        "/api/v1/plugins/apps/applications/a1/secrets/NOT_MANAGED/reveal",
        json={"reason": "Diagnóstico"},
    )
    assert missing.status_code == 404


def test_reveal_request_requires_bounded_reason() -> None:
    with pytest.raises(ValidationError):
        SecretRevealRequest(reason="")
    with pytest.raises(ValidationError):
        SecretRevealRequest.model_validate({"reason": "ok", "value": "secret"})


def test_lifecycle_appends_service_as_the_final_compose_argument(tmp_path: Path) -> None:
    from plugins.apps.backend.runtime.lifecycle import lifecycle
    from plugins.apps.backend.runtime.types import (
        MANIFEST,
        OWNER_LABEL,
        Completed,
        LifecycleOptions,
    )

    release = tmp_path / "app-demo"
    release.mkdir()
    manifest = {
        "project": "app-demo",
        "owner_label": OWNER_LABEL,
        "release": str(release),
        "compose": "docker-compose.yml",
        "has_env": False,
        "status": "running",
        "containers": {"web": "container-web", "other": "container-other"},
    }
    (release / MANIFEST).write_text(__import__("json").dumps(manifest), encoding="utf-8")

    class Runner:
        transport = "local"
        environ = {"SPANEL_APPS_STATE_ROOT": str(tmp_path)}

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def run(self, argv, *, check=True, timeout=0, stdin=None):
            self.calls.append(list(argv))
            return Completed(0, "")

    runner = Runner()
    lifecycle(
        LifecycleOptions(
            command="restart", project="app-demo", release=release, service="web"
        ),
        runner,
    )
    restart = next(call for call in runner.calls if "restart" in call)
    assert restart[-1] == "web"
    assert restart[-2] == "restart"

    with pytest.raises(Exception, match="whole application"):
        lifecycle(
            LifecycleOptions(
                command="down", project="app-demo", release=release, service="web"
            ),
            runner,
        )


def test_service_ports_only_expose_container_side() -> None:
    from plugins.apps.backend.runtime.deployment import _service_internal_ports

    ports = _service_internal_ports({
        "services": {
            "web": {
                "ports": ["127.0.0.1:8080:8000", {"target": 8443, "published": 443}],
                "expose": ["9000"],
            }
        }
    })
    assert ports == {"web": [8000, 8443, 9000]}


def test_domains_sites_use_stable_ids_and_container_names(tmp_path: Path) -> None:
    from plugins.apps.backend.register import resolve_apps_sites

    engine = database(tmp_path)
    with Session(engine) as db:
        deployment = db.get(Deployment, "d1")
        deployment.manifest = {
            **deployment.manifest,
            "container_names": {"web": "app-demo-web-1", "db": "app-demo-db-1"},
        }
        db.commit()
        sites = resolve_apps_sites(db, "t1")
    assert [(site.site_id, site.container) for site in sites] == [
        ("a1:db", "app-demo-db-1"),
        ("a1:web", "app-demo-web-1"),
    ]
    assert all(":" in site.site_id for site in sites)
