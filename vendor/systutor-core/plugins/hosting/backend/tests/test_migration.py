"""The migration is reversible on the SQLite test database.

The live suite runs the same up/down/up against the real PostgreSQL when
`SPANEL_TEST_POSTGRES_URL` is set; this test keeps the contract testable
offline.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "001_create_hosting_sites.py"
)


def _cargar_migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hosting_migration_001", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migracion_up_down_up_en_sqlite() -> None:
    migracion = _cargar_migracion()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with Session(engine) as session:
            migracion.upgrade(session)
        assert inspect(engine).has_table("hosting_sites")

        columnas = {column["name"] for column in inspect(engine).get_columns("hosting_sites")}
        assert columnas == {
            "id",
            "tenant_id",
            "container_name",
            "domain",
            "created_at",
            "updated_at",
        }

        with Session(engine) as session:
            migracion.downgrade(session)
        assert not inspect(engine).has_table("hosting_sites")

        with Session(engine) as session:
            migracion.upgrade(session)
        assert inspect(engine).has_table("hosting_sites")
    finally:
        engine.dispose()
