"""Allow stable app_service subjects (A.SPEC 0060)."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

revision = "004_expand_operation_subject_id"


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
    inspector = inspect(bind)
    if "apps_operations" not in inspector.get_table_names():
        return
    columns = {column["name"]: column for column in inspector.get_columns("apps_operations")}
    subject = columns.get("subject_id")
    if subject is None:
        return
    if bind.dialect.name == "postgresql":
        _execute(db, "ALTER TABLE apps_operations ALTER COLUMN subject_id TYPE VARCHAR(255)")


def downgrade(db) -> None:
    bind = _bind(db)
    inspector = inspect(bind)
    if "apps_operations" not in inspector.get_table_names():
        return
    if bind.dialect.name == "postgresql":
        _execute(db, "ALTER TABLE apps_operations ALTER COLUMN subject_id TYPE VARCHAR(36)")
