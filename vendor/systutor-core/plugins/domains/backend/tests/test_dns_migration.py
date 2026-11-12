"""Migration 003 is additive, idempotent and reversible."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from systutor.kernel.tenants.models import Tenant

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "003_create_dns_zone_ownership.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("domains_migration_003", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO domains_dns_zones "
                "(id, tenant_id, zone_name, kind, observed_version) VALUES "
                "('z1', 't1', 'example.com', 'Native', 'abc')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO domains_dns_settings "
                "(id, ns1_hostname, ns2_hostname, soa_contact, default_ttl, version) "
                "VALUES (1, 'ns1.example.com', 'ns2.example.com', 'hostmaster.example.com', 300, 1)"
            )
        )


def test_upgrade_downgrade_removes_only_the_two_tables() -> None:
    migration = _load()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        Tenant.__table__.create(bind=engine)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE pre_existing (id INTEGER PRIMARY KEY)"))
        with Session(engine) as session:
            session.add(Tenant(id="t1", name="One", slug="one"))
            session.commit()

        with Session(engine) as session:
            migration.upgrade(session)
            session.commit()
        names = set(inspect(engine).get_table_names())
        assert {"domains_dns_zones", "domains_dns_settings"} <= names
        assert "pre_existing" in names and "tenants" in names

        zone_columns = {
            column["name"] for column in inspect(engine).get_columns("domains_dns_zones")
        }
        assert zone_columns == {
            "id",
            "tenant_id",
            "zone_name",
            "kind",
            "observed_version",
            "created_at",
            "updated_at",
        }
        settings_columns = {
            column["name"] for column in inspect(engine).get_columns("domains_dns_settings")
        }
        assert settings_columns == {
            "id",
            "ns1_hostname",
            "ns2_hostname",
            "ns1_ipv4",
            "ns1_ipv6",
            "ns2_ipv4",
            "ns2_ipv6",
            "soa_contact",
            "default_ttl",
            "version",
            "updated_at",
        }
        assert inspect(engine).get_unique_constraints("domains_dns_zones")

        _seed(engine)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT zone_name, tenant_id FROM domains_dns_zones")
            ).all() == [("example.com", "t1")]
            assert connection.execute(
                text("SELECT ns1_hostname, version FROM domains_dns_settings")
            ).all() == [("ns1.example.com", 1)]

        with Session(engine) as session:
            migration.downgrade(session)
            session.commit()
        names = set(inspect(engine).get_table_names())
        assert "domains_dns_zones" not in names
        assert "domains_dns_settings" not in names
        assert "pre_existing" in names and "tenants" in names
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM tenants")).scalar() == 1

        # Upgrade is idempotent and starts from empty DNS tables after rollback.
        with Session(engine) as session:
            migration.upgrade(session)
            migration.upgrade(session)
            session.commit()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM domains_dns_zones")
            ).scalar() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM domains_dns_settings")
            ).scalar() == 0
    finally:
        engine.dispose()
