from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.models import Application, Operation
from plugins.apps.backend.operations import (
    claim_operation,
    create_operation,
    dispatch_operation,
    write_pending_env,
)
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(Tenant(id="t1", name="Tenant", slug="tenant"))
        session.add(User(
            id="u1", tenant_id="t1", email="u@example.test",
            full_name="U", password_hash="x",
        ))
        session.add(Application(id="a1", tenant_id="t1", name="App", slug="app",
                                repository="o/r", branch="main", source_connection_user_id="u1",
                                runtime_project="app-project"))
        session.commit()
        yield session
    engine.dispose()


def test_idempotency_and_single_active_operation(db: Session) -> None:
    app = db.get(Application, "a1")
    first, created = create_operation(db, application=app, actor_user_id="u1", kind="inspect",
                                      idempotency_key="key", payload={})
    same, created_again = create_operation(db, application=app, actor_user_id="u1", kind="inspect",
                                           idempotency_key="key", payload={})
    assert created and not created_again and same.id == first.id
    with pytest.raises(AppError) as conflict:
        create_operation(db, application=app, actor_user_id="u1", kind="inspect",
                         idempotency_key="key", payload={"changed": True})
    assert conflict.value.status_code == 409


def test_payload_is_operation_id_only_and_duplicate_claim_is_noop(db: Session) -> None:
    app = db.get(Application, "a1")
    operation, _ = create_operation(db, application=app, actor_user_id="u1", kind="inspect",
                                    idempotency_key="key", payload={})

    class Broker:
        def __init__(self):
            self.args = []

        def enqueue(self, operation_id):
            self.args.append((operation_id,))
            return "message"

    broker = Broker()
    dispatch_operation(db, operation, broker)
    assert broker.args == [(operation.id,)]
    assert claim_operation(db, operation.id) is not None
    assert claim_operation(db, operation.id) is None


def test_dispatch_failure_is_terminal_and_releases_lock(db: Session) -> None:
    app = db.get(Application, "a1")
    operation, _ = create_operation(db, application=app, actor_user_id="u1", kind="inspect",
                                    idempotency_key="key", payload={})

    class Broken:
        def enqueue(self, operation_id):
            raise RuntimeError("down")

    with pytest.raises(AppError) as unavailable:
        dispatch_operation(db, operation, Broken())
    assert unavailable.value.status_code == 503
    assert db.get(Operation, operation.id).error_code == "apps_queue_unavailable"
    assert db.get(Application, "a1").active_operation_id is None


def test_pending_environment_is_private_and_rejects_injection(tmp_path: Path) -> None:
    path, names, fingerprint = write_pending_env(tmp_path, "operation", [("API_KEY", "canary")])
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert names == ["API_KEY"] and len(fingerprint) == 64
    with pytest.raises(ValueError):
        write_pending_env(tmp_path, "other", [("BAD", "line\nbreak")])


def test_control_operation_is_installation_scoped_and_dispatches_only_operation_id(
    db: Session,
) -> None:
    operation, created = create_operation(
        db, application=None, actor_user_id="u1", kind="github_sync",
        idempotency_key="sync", payload={}, tenant_id="t1",
        subject_type="installation", subject_id="i1",
    )
    assert created and operation.application_id is None
    assert operation.subject_type == "installation"

    class Broker:
        def __init__(self):
            self.args = []

        def enqueue(self, operation_id):
            self.args.append(operation_id)
            return "message"

    broker = Broker()
    dispatch_operation(db, operation, broker)
    assert broker.args == [operation.id]
    with pytest.raises(AppError) as active:
        create_operation(
            db, application=None, actor_user_id="u1", kind="github_sync",
            idempotency_key="other", payload={}, tenant_id="t1",
            subject_type="installation", subject_id="i1",
        )
    assert active.value.status_code == 409
