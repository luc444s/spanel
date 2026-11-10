from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor/systutor-core"))
SPEC = importlib.util.spec_from_file_location(
    "refresh_routes", ROOT / "scripts/refresh_routes.py"
)
assert SPEC and SPEC.loader
refresh = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = refresh
SPEC.loader.exec_module(refresh)

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.models import Application
from systutor.core.database import Base


def database(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Application(
            id="a1", tenant_id="t1", name="Active", slug="active",
            repository="acme/repo", branch="main", source_connection_user_id="u1",
            runtime_project="app-active",
        ))
        db.add(Application(
            id="a2", tenant_id="t1", name="Deleted", slug="deleted",
            repository="acme/repo", branch="main", source_connection_user_id="u1",
            runtime_project="app-deleted",
            deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
        ))
        db.commit()
    return engine


def test_select_applications_skips_deleted_and_filters_identity(tmp_path: Path) -> None:
    engine = database(tmp_path)
    with Session(engine) as db:
        rows = refresh.select_applications(db, None, None)
        assert [row.slug for row in rows] == ["active"]
        assert refresh.select_applications(db, "a1", None)[0].slug == "active"
        assert refresh.select_applications(db, None, "active")[0].id == "a1"
        assert refresh.select_applications(db, None, "missing") == []
