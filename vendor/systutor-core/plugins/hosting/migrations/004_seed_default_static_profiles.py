"""Seed the lab-validated default static profiles (A.SPEC 0046).

Inserts `react`, `boot` and `astro` next to the builtin `nginx` created by
003, ONLY when the row does not exist yet: admin-edited rows are never
overwritten (I2). The downgrade deletes a row only when NO `hosting_sites`
row references it (leave-in-use guard, I3) and never touches `nginx` (I4).

Idempotent on purpose and reversible on SQLite and PostgreSQL:
an upgrade over a DB that already has the rows performs zero writes.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

revision = "004_seed_default_static_profiles"

# Exact values validated end to end in the 0042 lab
# (scripts/spanel_profiles/profiles.py, build_profiles()).
_ASTRO_COMMAND = (
    "cd /site; i=0; while [ ! -f src/pages/index.astro ]; do "
    "i=$((i+1)); [ $i -gt 300 ] && exit 1; sleep 1; done; "
    "npm install --no-fund --no-audit "
    "&& npx astro build "
    "&& npx astro preview --host 0.0.0.0 --port 80"
)

_SEED_ROWS: tuple[dict, ...] = (
    {
        "id": "react",
        "name": "React SPA (Caddy)",
        "image": "caddy:2-alpine",
        "web_root": "/usr/share/caddy",
        "health_mode": "marker",
        "env_json": None,
        "command": None,
    },
    {
        "id": "boot",
        "name": "Bootstrap (Apache)",
        "image": "httpd:alpine",
        "web_root": "/usr/local/apache2/htdocs",
        "health_mode": "marker",
        "env_json": None,
        "command": None,
    },
    {
        "id": "astro",
        "name": "Astro (build + preview)",
        "image": "node:24-alpine",
        "web_root": "/site",
        "health_mode": "http_200",
        "env_json": None,
        "command": _ASTRO_COMMAND,
    },
)

_BUILTIN_ID = "nginx"


def _execute(db, statement: str, params: dict | None = None) -> None:
    bind = db.get_bind()
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            connection.execute(text(statement), params)
        return
    db.execute(text(statement), params)


def _row_count(db, statement: str, params: dict) -> int:
    bind = db.get_bind()
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            return int(
                connection.execute(text(statement), params).scalar() or 0
            )
    return int(db.execute(text(statement), params).scalar() or 0)


def upgrade(db) -> None:
    if inspect(db.get_bind()).has_table("hosting_static_profiles"):
        for row in _SEED_ROWS:
            count = _row_count(
                db,
                "SELECT COUNT(*) FROM hosting_static_profiles WHERE id = :id",
                {"id": row["id"]},
            )
            if count:
                continue
            _execute(
                db,
                "INSERT INTO hosting_static_profiles "
                "(id, name, image, web_root, container_port, health_mode, "
                "env_json, command, created_at, updated_at) "
                "VALUES (:id, :name, :image, :web_root, '80', :health_mode, "
                ":env_json, :command, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                row,
            )


def downgrade(db) -> None:
    if not inspect(db.get_bind()).has_table("hosting_static_profiles"):
        return
    has_profile_id = "profile_id" in {
        column["name"]
        for column in inspect(db.get_bind()).get_columns("hosting_sites")
        if inspect(db.get_bind()).has_table("hosting_sites")
    }
    for row in _SEED_ROWS:
        if has_profile_id:
            # Leave-in-use guard: the row stays while a site points at it.
            in_use = _row_count(
                db,
                "SELECT COUNT(*) FROM hosting_sites "
                "WHERE profile_id = :id AND profile_id IS NOT NULL",
                {"id": row["id"]},
            )
            if in_use:
                continue
        _execute(
            db,
            "DELETE FROM hosting_static_profiles "
            "WHERE id = :id AND id <> :builtin",
            {"id": row["id"], "builtin": _BUILTIN_ID},
        )
