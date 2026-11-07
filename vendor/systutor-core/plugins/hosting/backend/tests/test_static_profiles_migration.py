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


# --- 004_seed_default_static_profiles (A.SPEC 0046) ------------------------

FOURTH = "004_seed_default_static_profiles"

# Exact values of the lab-validated prototype
# (scripts/spanel_profiles/profiles.py, build_profiles()).
ASTRO_COMMAND = (
    "cd /site; i=0; while [ ! -f src/pages/index.astro ]; do "
    "i=$((i+1)); [ $i -gt 300 ] && exit 1; sleep 1; done; "
    "npm install --no-fund --no-audit "
    "&& npx astro build "
    "&& npx astro preview --host 0.0.0.0 --port 80"
)

EXPECTED_ROWS = {
    "react": (
        "React SPA (Caddy)", "caddy:2-alpine", "/usr/share/caddy", 80,
        "marker", None, None,
    ),
    "boot": (
        "Bootstrap (Apache)", "httpd:alpine", "/usr/local/apache2/htdocs", 80,
        "marker", None, None,
    ),
    "astro": (
        "Astro (build + preview)", "node:24-alpine", "/site", 80,
        "http_200", None, ASTRO_COMMAND,
    ),
}

SEED_IDS = ("react", "boot", "astro")


def _load_rows(engine) -> dict[str, tuple]:
    with Session(engine) as session:
        return {
            str(row[0]): tuple(row[1:])
            for row in session.execute(
                text(
                    "SELECT id, name, image, web_root, container_port, "
                    "health_mode, env_json, command "
                    "FROM hosting_static_profiles"
                )
            ).fetchall()
        }


def _seed_003(engine) -> None:
    _seed_sites(engine)
    third = _load("003_add_hosting_static_profiles")
    with Session(engine) as session:
        third.upgrade(session)


def test_004_up_inserts_the_three_lab_rows_next_to_nginx(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_003(engine)
        fourth = _load(FOURTH)
        with Session(engine) as session:
            fourth.upgrade(session)

        rows = _load_rows(engine)
        # I4: the builtin nginx survives 004, plus exactly the three rows.
        assert set(rows) == {"nginx", *SEED_IDS}
        assert rows["nginx"] == (
            "Nginx (estático)", "nginx:alpine-lab", "/usr/share/nginx/html",
            80, "marker", None, None,
        )
        for seed_id in SEED_IDS:
            # I1: every field matches the lab-validated prototype.
            assert rows[seed_id] == EXPECTED_ROWS[seed_id]
    finally:
        engine.dispose()


def test_004_up_is_idempotent_and_keeps_admin_edits(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_003(engine)
        fourth = _load(FOURTH)
        with Session(engine) as session:
            fourth.upgrade(session)
        # The admin edits the react image in the panel (I2 prep).
        with Session(engine) as session:
            session.execute(
                text(
                    "UPDATE hosting_static_profiles SET image = :image "
                    "WHERE id = 'react'"
                ),
                {"image": "caddy:custom-editada"},
            )
            session.commit()

        # Repeated upgrade: zero writes, no duplicates, no overrides.
        with Session(engine) as session:
            fourth.upgrade(session)
            fourth.upgrade(session)

        rows = _load_rows(engine)
        assert len(rows) == 4
        # I2: the admin-edited image survives untouched.
        _name, image, _web, _port, _health, _env, _cmd = rows["react"]
        assert image == "caddy:custom-editada"
        # The rest of the row stays as seeded.
        assert rows["react"][2:] == EXPECTED_ROWS["react"][2:]
    finally:
        engine.dispose()


def test_004_downgrade_keeps_in_use_and_removes_unused(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_003(engine)
        fourth = _load(FOURTH)
        with Session(engine) as session:
            fourth.upgrade(session)
        # A site leans on `react` (the "hosting_profile_in_use" guard).
        with Session(engine) as session:
            session.execute(
                text(
                    "UPDATE hosting_sites SET profile_id = 'react' "
                    "WHERE id = 's-static'"
                )
            )
            session.commit()

        with Session(engine) as session:
            fourth.downgrade(session)

        rows = _load_rows(engine)
        # I3: the in-use profile stays; the unused ones disappear.
        assert "react" in rows
        assert "boot" not in rows and "astro" not in rows
        # I4: the builtin nginx is never touched by the downgrade.
        assert "nginx" in rows

        # A later upgrade re-seeds only the missing rows (react survives).
        with Session(engine) as session:
            fourth.upgrade(session)
        rows = _load_rows(engine)
        assert set(rows) == {"nginx", "react", "boot", "astro"}
        with Session(engine) as session:
            profile_id = session.execute(
                text("SELECT profile_id FROM hosting_sites WHERE id = 's-static'")
            ).scalar()
            assert profile_id == "react"
    finally:
        engine.dispose()


def test_004_downgrade_without_table_is_harmless(seed) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        _seed_sites(engine)
        fourth = _load(FOURTH)
        with Session(engine) as session:
            fourth.downgrade(session)
        assert not inspect(engine).has_table("hosting_static_profiles")
        assert inspect(engine).has_table("hosting_sites")
    finally:
        engine.dispose()
