"""Migration 004: additive, constrained and reversible for all three tables."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.kernel.tenants.models import Tenant

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "004_create_cloudflare_oauth.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("domains_migration_004", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO domains_cloudflare_oauth_client "
                "(id, account_id, client_id, secret_ciphertext, secret_nonce, "
                "key_version, redirect_uri, scopes_json) VALUES "
                "(1, 'acct1', 'client-1', :secret, :nonce, 1, "
                "'https://spanel.lab/cb', :scopes)"
            ),
            {"secret": b"cipher", "nonce": b"nonce", "scopes": '["Zone Read"]'},
        )
        connection.execute(
            text(
                "INSERT INTO domains_cloudflare_connections "
                "(id, tenant_id, cloudflare_account_id, account_name, "
                "access_ciphertext, access_nonce, key_version, scopes_json, status) "
                "VALUES ('c1', 't1', 'acct1', 'Lab', :access, :nonce, 1, :scopes, 'active')"
            ),
            {"access": b"cipher", "nonce": b"nonce", "scopes": '["DNS Read"]'},
        )
        connection.execute(
            text(
                "INSERT INTO domains_cloudflare_oauth_flows "
                "(id, tenant_id, state_hash, verifier_ciphertext, verifier_nonce, "
                "key_version, redirect_uri, status, expires_at) VALUES "
                "('f1', 't1', :state, :verifier, :nonce, 1, "
                "'https://spanel.lab/cb', 'pending', CURRENT_TIMESTAMP)"
            ),
            {"state": "a" * 64, "verifier": b"cipher", "nonce": b"nonce"},
        )


def test_upgrade_downgrade_all_three_tables_and_constraints() -> None:
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
            migration.upgrade(session)
            session.commit()

        names = set(inspect(engine).get_table_names())
        assert {
            "domains_cloudflare_oauth_client",
            "domains_cloudflare_connections",
            "domains_cloudflare_oauth_flows",
        } <= names
        assert "pre_existing" in names and "tenants" in names

        expected = {
            "domains_cloudflare_oauth_client": {
                "id",
                "account_id",
                "client_id",
                "secret_ciphertext",
                "secret_nonce",
                "key_version",
                "redirect_uri",
                "scopes_json",
                "created_at",
                "updated_at",
            },
            "domains_cloudflare_connections": {
                "id",
                "tenant_id",
                "cloudflare_account_id",
                "account_name",
                "access_ciphertext",
                "access_nonce",
                "refresh_ciphertext",
                "refresh_nonce",
                "key_version",
                "scopes_json",
                "expires_at",
                "status",
                "created_at",
                "updated_at",
            },
            "domains_cloudflare_oauth_flows": {
                "id",
                "tenant_id",
                "state_hash",
                "verifier_ciphertext",
                "verifier_nonce",
                "key_version",
                "redirect_uri",
                "status",
                "error_code",
                "expires_at",
                "consumed_at",
                "created_at",
                "updated_at",
            },
        }
        for table, columns in expected.items():
            found = {column["name"] for column in inspect(engine).get_columns(table)}
            assert found == columns

        assert inspect(engine).get_unique_constraints("domains_cloudflare_connections")
        assert inspect(engine).get_unique_constraints("domains_cloudflare_oauth_flows")
        flow_foreign_keys = inspect(engine).get_foreign_keys(
            "domains_cloudflare_oauth_flows"
        )
        assert any(fk["referred_table"] == "tenants" for fk in flow_foreign_keys)

        _seed(engine)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT client_id, scopes_json FROM domains_cloudflare_oauth_client")
            ).one() == ("client-1", '["Zone Read"]')
            assert connection.execute(
                text("SELECT account_name, status FROM domains_cloudflare_connections")
            ).one() == ("Lab", "active")
            assert connection.execute(
                text("SELECT status FROM domains_cloudflare_oauth_flows")
            ).scalar() == "pending"

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO domains_cloudflare_connections "
                        "(id, tenant_id, cloudflare_account_id, account_name, "
                        "access_ciphertext, access_nonce, key_version, scopes_json, status) "
                        "VALUES ('c2', 't1', 'acct1', 'Lab', :access, :nonce, 1, '[]', 'active')"
                    ),
                    {"access": b"x", "nonce": b"y"},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO domains_cloudflare_oauth_flows "
                        "(id, tenant_id, state_hash, verifier_ciphertext, verifier_nonce, "
                        "key_version, redirect_uri, status, expires_at) VALUES "
                        "('f2', 't1', :state, :verifier, :nonce, 1, "
                        "'https://spanel.lab/cb', 'pending', CURRENT_TIMESTAMP)"
                    ),
                    {"state": "a" * 64, "verifier": b"x", "nonce": b"y"},
                )

        with Session(engine) as session:
            migration.downgrade(session)
            session.commit()
        names = set(inspect(engine).get_table_names())
        assert "domains_cloudflare_oauth_client" not in names
        assert "domains_cloudflare_connections" not in names
        assert "domains_cloudflare_oauth_flows" not in names
        assert "pre_existing" in names and "tenants" in names
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM tenants")).scalar() == 1

        with Session(engine) as session:
            migration.upgrade(session)
            migration.upgrade(session)
            session.commit()
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM domains_cloudflare_oauth_client")
            ).scalar() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM domains_cloudflare_connections")
            ).scalar() == 0
            assert connection.execute(
                text("SELECT COUNT(*) FROM domains_cloudflare_oauth_flows")
            ).scalar() == 0
    finally:
        engine.dispose()
