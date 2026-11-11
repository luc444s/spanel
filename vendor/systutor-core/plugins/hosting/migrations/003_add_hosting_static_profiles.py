"""Add `hosting_static_profiles` and `hosting_sites.profile_id` (A.SPEC 0042).

The migration creates the profile table, adds the nullable `profile_id`
column, backfills the existing `kind="static"` rows to the builtin `nginx`
and inserts that builtin row once, reading the image from the same
`SPANEL_HOSTING_STATIC_IMAGE` variable the hosting settings read.

Idempotent on purpose and reversible on SQLite and PostgreSQL: the downgrade
drops the table and the column; a later upgrade recreates everything.
"""

from __future__ import annotations

import os

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    inspect,
    text,
)

revision = "003_add_hosting_static_profiles"

_BUILTIN_ID = "nginx"
_BUILTIN_NAME = "Nginx (static)"
_STATIC_IMAGE_ENV = "SPANEL_HOSTING_STATIC_IMAGE"
_DEFAULT_STATIC_IMAGE = "nginx:alpine"

metadata = MetaData()

hosting_static_profiles = Table(
    "hosting_static_profiles",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("image", String(255), nullable=False),
    Column("web_root", String(255), nullable=False),
    Column("container_port", Integer, nullable=False),
    Column("health_mode", String(16), nullable=False),
    Column("env_json", Text, nullable=True),
    Column("command", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _builtin_image() -> str:
    """The running static image: the same value the settings resolve."""
    return os.getenv(_STATIC_IMAGE_ENV, "").strip() or _DEFAULT_STATIC_IMAGE


def _profiles_table(db) -> set[str]:
    bind = db.get_bind()
    if not inspect(bind).has_table("hosting_static_profiles"):
        return set()
    return {
        column["name"]
        for column in inspect(bind).get_columns("hosting_static_profiles")
    }


def _sites_columns(db) -> set[str]:
    bind = db.get_bind()
    if not inspect(bind).has_table("hosting_sites"):
        return set()
    return {column["name"] for column in inspect(bind).get_columns("hosting_sites")}


def _execute(db, statement: str, params: dict | None = None) -> None:
    bind = db.get_bind()
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            connection.execute(text(statement), params)
        return
    db.execute(text(statement), params)


def _builtin_row_exists(db) -> bool:
    bind = db.get_bind()
    if not inspect(bind).has_table("hosting_static_profiles"):
        return True  # Nothing to insert into; nothing to create either.
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM hosting_static_profiles WHERE id = :id"),
                {"id": _BUILTIN_ID},
            ).scalar()
    else:
        count = db.execute(
            text("SELECT COUNT(*) FROM hosting_static_profiles WHERE id = :id"),
            {"id": _BUILTIN_ID},
        ).scalar()
    return bool(count)


def _ensure_builtin(db) -> None:
    if _builtin_row_exists(db):
        return
    _execute(
        db,
        "INSERT INTO hosting_static_profiles "
        "(id, name, image, web_root, container_port, health_mode, "
        "env_json, command, created_at, updated_at) "
        "VALUES (:id, :name, :image, :web_root, '80', 'marker', "
        "NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        {
            "id": _BUILTIN_ID,
            "name": _BUILTIN_NAME,
            "image": _builtin_image(),
            "web_root": "/usr/share/nginx/html",
        },
    )


def upgrade(db) -> None:
    metadata.create_all(bind=db.get_bind(), tables=[hosting_static_profiles])
    columns = _sites_columns(db)
    if columns and "profile_id" not in columns:
        _execute(db, "ALTER TABLE hosting_sites ADD COLUMN profile_id VARCHAR(32)")
        columns = _sites_columns(db)
    _ensure_builtin(db)
    if columns and "profile_id" in columns and "kind" in columns:
        _execute(
            db,
            "UPDATE hosting_sites SET profile_id = :id "
            "WHERE kind = 'static' AND (profile_id IS NULL OR profile_id = '')",
            {"id": _BUILTIN_ID},
        )


def downgrade(db) -> None:
    columns = _sites_columns(db)
    has_kind = "kind" in columns
    if has_kind and "profile_id" in columns:
        # The custom profiles disappear with the table (ROLLBACK of the spec):
        # only the builtin mapping has a rollback value, and a static row
        # keeps its container until it is deleted by API as always.
        _execute(db, "UPDATE hosting_sites SET profile_id = NULL")
        _execute(db, "ALTER TABLE hosting_sites DROP COLUMN profile_id")
    elif "profile_id" in columns:
        _execute(db, "ALTER TABLE hosting_sites DROP COLUMN profile_id")
    metadata.drop_all(bind=db.get_bind(), tables=[hosting_static_profiles])
