"""`hosting_static_profiles` + `hosting_sites.profile_id` migration: up, down, up."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
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


@pytest.fixture()
def seed(monkeypatch):
    monkeypatch.setenv("SPANEL_HOSTING_STATIC_IMAGE", "nginx:alpine-lab")


def _seed_sites(engine) -> None:
    first = _load("001_create_hosting_sites")
    second = _load("002_add_hosting_site_kind")
    with Session(engine) as session:
        first.upgrade(session)
    with Session(engine) as session:
        second.upgrade(session)
        session.execute(
            text(
                "INSERT INTO hosting_sites "
                "(id, tenant_id, container_name, domain, kind, created_at, updated_at) "
                "VALUES ('s-static', 't1', 'spanel-demo-web', 'demo.spanel', 'static', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        session.execute(
            text(
                "INSERT INTO hosting_sites "
                "(id, tenant_id, container_name, domain, kind, created_at, updated_at) "
                "VALUES ('s-wp', 't1', 'spanel-other-wp', 'other.spanel', 'wordpress', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        session.commit()


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_migration_creates_builtin_and_backfills_statics(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_sites(engine)
        third = _load("003_add_hosting_static_profiles")
        with Session(engine) as session:
            third.upgrade(session)

        assert inspect(engine).has_table("hosting_static_profiles")
        columns = _columns(engine, "hosting_static_profiles")
        for name in (
            "id", "name", "image", "web_root", "container_port",
            "health_mode", "env_json", "command", "created_at", "updated_at",
        ):
            assert name in columns

        with Session(engine) as session:
            row = session.execute(
                text(
                    "SELECT id, name, image, web_root, container_port, health_mode, "
                    "env_json, command FROM hosting_static_profiles WHERE id = 'nginx'"
                )
            ).one()
            assert row[0] == "nginx"
            assert row[1] == "Nginx (estático)"
            # The image is the SAME value the settings resolve from the env.
            assert row[2] == "nginx:alpine-lab"
            assert row[3] == "/usr/share/nginx/html"
            assert int(row[4]) == 80
            assert row[5] == "marker"
            assert row[6] is None and row[7] is None

            # Backfill: existing statics point at nginx; wordpress stays NULL.
            profile_ids: dict[str, str | None] = {}
            for site_row in session.execute(
                text("SELECT id, profile_id FROM hosting_sites")
            ).fetchall():
                profile_ids[str(site_row[0])] = site_row[1]
            assert profile_ids["s-static"] == "nginx"
            assert profile_ids["s-wp"] is None
    finally:
        engine.dispose()


def test_migration_downgrade_then_upgrade(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_sites(engine)
        third = _load("003_add_hosting_static_profiles")
        with Session(engine) as session:
            third.upgrade(session)
        with Session(engine) as session:
            third.downgrade(session)
        assert not inspect(engine).has_table("hosting_static_profiles")
        assert "profile_id" not in _columns(engine, "hosting_sites")
        assert inspect(engine).has_table("hosting_sites")

        with Session(engine) as session:
            third.upgrade(session)
        with Session(engine) as session:
            count = session.execute(
                text(
                    "SELECT COUNT(*) FROM hosting_static_profiles WHERE id = 'nginx'"
                )
            ).scalar()
            assert count == 1
            # The statics are re-mapped to the builtin (kind back to NULL first).
            static_profile = session.execute(
                text(
                    "SELECT profile_id FROM hosting_sites WHERE id = 's-static'"
                )
            ).scalar()
            assert static_profile == "nginx"

        # Idempotent: a repeated upgrade is a no-op apart from the builtin.
        with Session(engine) as session:
            third.upgrade(session)
        with Session(engine) as session:
            count = session.execute(
                text("SELECT COUNT(*) FROM hosting_static_profiles")
            ).scalar()
            assert count == 1
    finally:
        engine.dispose()


def test_builtin_image_reads_the_settings_env(seed, monkeypatch) -> None:
    monkeypatch.setenv("SPANEL_HOSTING_STATIC_IMAGE", "caddy:2-lab")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_sites(engine)
        third = _load("003_add_hosting_static_profiles")
        with Session(engine) as session:
            third.upgrade(session)
        with Session(engine) as session:
            image = session.execute(
                text("SELECT image FROM hosting_static_profiles WHERE id = 'nginx'")
            ).scalar()
            assert image == "caddy:2-lab"
    finally:
        engine.dispose()
