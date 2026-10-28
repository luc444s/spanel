"""Migración de dominios administrados: aditiva, idempotente y reversible."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from systutor.kernel.tenants.models import Tenant

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "002_tenant_domains.py"
)


def _cargar_migracion() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "tenant_migration_002", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_hosting_sites(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE hosting_sites ("
                "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL, "
                "container_name VARCHAR(255) NOT NULL, domain VARCHAR(255), "
                "kind VARCHAR(16) NOT NULL DEFAULT 'wordpress', "
                "created_at DATETIME, updated_at DATETIME)"
            )
        )


def _rows(engine, table: str) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [
            (row[0], row[1])
            for row in connection.execute(
                text(f"SELECT domain, tenant_id FROM {table} ORDER BY domain")
            )
        ]


def test_migracion_backfill_duplicados_y_up_down_up() -> None:
    migracion = _cargar_migracion()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Tenant.__table__.create(bind=engine)
        _create_hosting_sites(engine)
        with Session(engine) as session:
            session.add_all(
                [
                    Tenant(id="t1", name="Uno", slug="uno", domain="Uno.COM."),
                    Tenant(id="t2", name="Dos", slug="dos", domain=None),
                ]
            )
            session.commit()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO hosting_sites "
                    "(id, tenant_id, container_name, domain) VALUES "
                    "('s1', 't1', 'spanel-b-web', 'b.spanel'),"
                    "('s2', 't1', 'spanel-a-web', 'b.spanel'),"
                    "('s3', 't2', 'spanel-c-web', 'c.spanel'),"
                    "('s4', 't2', 'spanel-d-web', 'mal dominio'),"
                    "('s5', 't2', 'spanel-e-web', 'UNO.com')"
                )
            )

        with Session(engine) as session:
            migracion.upgrade(session)
            session.commit()

        assert inspect(engine).has_table("tenant_domains")
        columns = {
            column["name"] for column in inspect(engine).get_columns("tenant_domains")
        }
        assert columns == {"domain", "tenant_id", "created_at", "updated_at"}
        pk = inspect(engine).get_pk_constraint("tenant_domains")
        assert pk["constrained_columns"] == ["domain"]

        # El duplicado entre sitios conserva la primera fila por container_name;
        # el dominio del tenant gana sobre el sitio que lo repite.
        assert _rows(engine, "tenant_domains") == [
            ("b.spanel", "t1"),
            ("c.spanel", "t2"),
            ("uno.com", "t1"),
        ]

        with Session(engine) as session:
            migracion.upgrade(session)
            session.commit()
        assert len(_rows(engine, "tenant_domains")) == 3

        with Session(engine) as session:
            migracion.downgrade(session)
            session.commit()
        assert not inspect(engine).has_table("tenant_domains")
        assert inspect(engine).has_table("tenants")
        assert inspect(engine).has_table("hosting_sites")

        with Session(engine) as session:
            migracion.upgrade(session)
            session.commit()
        assert _rows(engine, "tenant_domains") == [
            ("b.spanel", "t1"),
            ("c.spanel", "t2"),
            ("uno.com", "t1"),
        ]
    finally:
        engine.dispose()
