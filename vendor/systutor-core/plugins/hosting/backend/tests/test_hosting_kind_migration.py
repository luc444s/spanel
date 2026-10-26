"""`hosting_sites.kind` migration: up, backfill, down and up again (SQLite)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _load(name: str) -> ModuleType:
    path = MIGRATIONS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"hosting_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kind_migration_up_down_up_with_backfill() -> None:
    first = _load("001_create_hosting_sites")
    second = _load("002_add_hosting_site_kind")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with Session(engine) as session:
            first.upgrade(session)
            session.execute(
                text(
                    "INSERT INTO hosting_sites "
                    "(id, tenant_id, container_name, domain, created_at, updated_at) "
                    "VALUES ('s1', 't1', 'spanel-demo-wp', 'demo.spanel', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            session.commit()

        with Session(engine) as session:
            second.upgrade(session)

        columns = {c["name"] for c in inspect(engine).get_columns("hosting_sites")}
        assert "kind" in columns
        with Session(engine) as session:
            assert session.execute(text("SELECT kind FROM hosting_sites")).scalar() == "wordpress"

        with Session(engine) as session:
            second.downgrade(session)
        columns = {c["name"] for c in inspect(engine).get_columns("hosting_sites")}
        assert "kind" not in columns
        assert inspect(engine).has_table("hosting_sites")

        with Session(engine) as session:
            second.upgrade(session)
        with Session(engine) as session:
            assert session.execute(text("SELECT kind FROM hosting_sites")).scalar() == "wordpress"

        # Idempotent on a second call.
        with Session(engine) as session:
            second.upgrade(session)
        assert "kind" in {
            c["name"] for c in inspect(engine).get_columns("hosting_sites")
        }
    finally:
        engine.dispose()
