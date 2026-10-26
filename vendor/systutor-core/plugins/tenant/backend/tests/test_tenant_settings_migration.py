"""La migración del sufijo es aditiva y reversible (A.SPEC 0030 / V4)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from systutor.kernel.tenants.models import Tenant

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "001_tenant_site_settings.py"
)


def _cargar_migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tenant_migration_001", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tenant_rows(engine) -> list[tuple[str, str | None]]:
    with engine.connect() as connection:
        return [
            (row[0], row[1])
            for row in connection.execute(text("SELECT id, domain FROM tenants ORDER BY id"))
        ]


def test_migracion_up_down_up_en_sqlite() -> None:
    migracion = _cargar_migracion()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Tenant.__table__.create(bind=engine)
        with Session(engine) as session:
            session.add_all(
                [
                    Tenant(id="t1", name="Uno", slug="uno", domain="uno.com"),
                    Tenant(id="t2", name="Dos", slug="dos", domain=None),
                ]
            )
            session.commit()

        before = _tenant_rows(engine)

        with Session(engine) as session:
            migracion.upgrade(session)
            session.commit()
        assert inspect(engine).has_table("tenant_site_settings")

        columnas = {
            column["name"]
            for column in inspect(engine).get_columns("tenant_site_settings")
        }
        assert columnas == {"tenant_id", "site_suffix", "created_at", "updated_at"}

        with engine.connect() as connection:
            backfill = dict(
                connection.execute(
                    text("SELECT tenant_id, site_suffix FROM tenant_site_settings")
                ).all()
            )
        assert backfill == {"t1": "uno.com", "t2": None}

        # El backfill no altera las filas ni el dominio de correo.
        assert _tenant_rows(engine) == before

        with Session(engine) as session:
            migracion.downgrade(session)
            session.commit()
        assert not inspect(engine).has_table("tenant_site_settings")
        assert inspect(engine).has_table("tenants")
        assert _tenant_rows(engine) == before

        with Session(engine) as session:
            migracion.upgrade(session)
            session.commit()
        assert inspect(engine).has_table("tenant_site_settings")
        with Session(engine) as session:
            rows = session.execute(
                text("SELECT tenant_id, site_suffix FROM tenant_site_settings")
            ).all()
        assert {row[0] for row in rows} == {"t1", "t2"}
    finally:
        engine.dispose()
