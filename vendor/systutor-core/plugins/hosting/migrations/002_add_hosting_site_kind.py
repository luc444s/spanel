"""Add `hosting_sites.kind` with a `wordpress` default and backfill.

Idempotent: the column is only added when it is missing. Reversible on SQLite
and PostgreSQL, and it never touches rows other than the default value.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

revision = "002_add_hosting_site_kind"
_DEFAULT = "wordpress"


def _columns(db) -> set[str]:
    bind = db.get_bind()
    if not inspect(bind).has_table("hosting_sites"):
        return set()
    return {column["name"] for column in inspect(bind).get_columns("hosting_sites")}


def _execute(db, statement: str) -> None:
    bind = db.get_bind()
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            connection.execute(text(statement))
        return
    db.execute(text(statement))


def upgrade(db) -> None:
    columns = _columns(db)
    if not columns or "kind" in columns:
        return
    _execute(
        db,
        "ALTER TABLE hosting_sites ADD COLUMN kind VARCHAR(16) "
        f"NOT NULL DEFAULT '{_DEFAULT}'",
    )
    _execute(
        db,
        "UPDATE hosting_sites SET kind = "
        f"'{_DEFAULT}' WHERE kind IS NULL OR kind = ''",
    )


def downgrade(db) -> None:
    columns = _columns(db)
    if "kind" not in columns:
        return
    _execute(db, "ALTER TABLE hosting_sites DROP COLUMN kind")
