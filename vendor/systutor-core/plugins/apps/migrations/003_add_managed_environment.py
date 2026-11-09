"""Add encrypted, application-scoped managed environment storage."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

revision = "003_add_managed_environment"


def _bind(db):
    return db if isinstance(db, Engine) else db.get_bind()


def _execute(db, statement: str) -> None:
    if isinstance(db, Engine):
        with db.begin() as connection:
            connection.execute(text(statement))
        return
    db.execute(text(statement))


def upgrade(db) -> None:
    bind = _bind(db)
    if "apps_applications" not in inspect(bind).get_table_names():
        return
    present = {
        column["name"] for column in inspect(bind).get_columns("apps_applications")
    }
    binary = "BYTEA" if bind.dialect.name == "postgresql" else "BLOB"
    columns = (
        ("managed_env_ciphertext", binary),
        ("managed_env_nonce", binary),
        ("managed_env_key_version", "INTEGER"),
    )
    for name, ddl in columns:
        if name not in present:
            _execute(db, f"ALTER TABLE apps_applications ADD COLUMN {name} {ddl}")


def downgrade(db) -> None:
    bind = _bind(db)
    if "apps_applications" not in inspect(bind).get_table_names():
        return
    present = {
        column["name"] for column in inspect(bind).get_columns("apps_applications")
    }
    for name in (
        "managed_env_key_version",
        "managed_env_nonce",
        "managed_env_ciphertext",
    ):
        if name in present:
            _execute(db, f"ALTER TABLE apps_applications DROP COLUMN {name}")
