from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.models import Application, Operation
from systutor.core.database import Base

MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "002_add_github_apps.py"
)


def load():
    spec = importlib.util.spec_from_file_location("apps_migration_002", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_github_app_migration_is_additive_and_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    migration = load()
    with Session(engine) as db:
        migration.upgrade(db)
        migration.upgrade(db)
        db.commit()
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "apps_github_providers", "apps_github_installations",
            "apps_github_repositories",
        } <= tables
        application_columns = {
            column["name"] for column in inspector.get_columns("apps_applications")
        }
        assert {"source_installation_id", "source_repository_id", "source_status"} <= (
            application_columns
        )
        operation_columns = {
            column["name"] for column in inspector.get_columns("apps_operations")
        }
        assert {"subject_type", "subject_id", "started_at"} <= operation_columns
        assert "application_id" in operation_columns
        db.add(Operation(
            id="control", tenant_id="t1", actor_user_id="u1",
            subject_type="installation", subject_id="i1", kind="github_sync",
            status="queued", idempotency_key="k", request_hash="h",
            application_id=None,
        ))
        db.commit()
    assert Application.__tablename__ == "apps_applications"


def test_migration_ddl_accepts_both_engine_and_session(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'engine.db'}")
    Base.metadata.create_all(engine)
    migration = load()
    # Regression for SQLAlchemy 2.x: Engine has no .execute().
    migration._execute(engine, "CREATE TABLE engine_probe (id INTEGER)")
    assert "engine_probe" in inspect(engine).get_table_names()
    with Session(engine) as db:
        migration._execute(db, "CREATE TABLE session_probe (id INTEGER)")
        db.commit()
    assert "session_probe" in inspect(engine).get_table_names()
