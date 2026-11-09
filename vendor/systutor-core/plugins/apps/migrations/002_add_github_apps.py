# ruff: noqa: E501
"""Add GitHub App providers, installations and authorized repositories."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    inspect,
    text,
)

revision = "002_add_github_apps"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))
Table("users", metadata, Column("id", String(36), primary_key=True))
Table("apps_applications", metadata, Column("id", String(36), primary_key=True))

providers = Table(
    "apps_github_providers", metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("created_by_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("github_app_id", BigInteger, nullable=False, unique=True),
    Column("app_slug", String(100), nullable=False),
    Column("name", String(255), nullable=False),
    Column("owner_login", String(255)),
    Column("owner_type", String(32)),
    Column("private_key_ciphertext", LargeBinary, nullable=False),
    Column("private_key_nonce", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
)
installations = Table(
    "apps_github_installations", metadata,
    Column("id", String(36), primary_key=True),
    Column("provider_id", String(36), ForeignKey("apps_github_providers.id"), nullable=False),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("github_installation_id", BigInteger, nullable=False, unique=True),
    Column("account_id", String(64), nullable=False),
    Column("account_login", String(255), nullable=False),
    Column("account_type", String(32)),
    Column("repository_selection", String(32), nullable=False),
    Column("permissions", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("installed_at", DateTime(timezone=True), nullable=False),
    Column("last_synced_at", DateTime(timezone=True)),
    Column("suspended_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
)
repositories = Table(
    "apps_github_repositories", metadata,
    Column("id", String(36), primary_key=True),
    Column("installation_id", String(36), ForeignKey("apps_github_installations.id"), nullable=False),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("github_repository_id", BigInteger, nullable=False),
    Column("full_name", String(510), nullable=False),
    Column("private", Boolean, nullable=False),
    Column("default_branch", String(255)),
    Column("archived", Boolean, nullable=False),
    Column("disabled", Boolean, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("removed_at", DateTime(timezone=True)),
    UniqueConstraint("installation_id", "github_repository_id"),
)
Index("ix_apps_github_providers_tenant_id", providers.c.tenant_id)
Index("ix_apps_github_installations_provider_id", installations.c.provider_id)
Index("ix_apps_github_installations_tenant_id", installations.c.tenant_id)
Index("ix_apps_github_repositories_installation_id", repositories.c.installation_id)
Index("ix_apps_github_repositories_tenant_id", repositories.c.tenant_id)

NEW_COLUMNS = {
    "apps_applications": (
        ("source_installation_id", "VARCHAR(36)"),
        ("source_repository_id", "VARCHAR(36)"),
        ("source_status", "VARCHAR(32) NOT NULL DEFAULT 'connected'"),
    ),
    "apps_operations": (
        ("subject_type", "VARCHAR(32)"),
        ("subject_id", "VARCHAR(36)"),
        ("started_at", "TIMESTAMP WITH TIME ZONE"),
    ),
}


def _bind(db):
    return db if isinstance(db, Engine) else db.get_bind()


def _execute(db, statement: str) -> None:
    """Run DDL against the Session/Savepoint, or an Engine transaction.

    SQLAlchemy 2.x removed ``Engine.execute``. When the plugin migration
    runtime hands us an Engine (as some installation paths do) we must open an
    explicit transaction; a Session keeps using its active savepoint.
    """

    if isinstance(db, Engine):
        with db.begin() as connection:
            connection.execute(text(statement))
        return
    db.execute(text(statement))


def _columns(db, table: str) -> set[str]:
    return {column["name"] for column in inspect(_bind(db)).get_columns(table)}


def upgrade(db) -> None:
    bind = _bind(db)
    for table in (providers, installations, repositories):
        table.create(bind=bind, checkfirst=True)
    existing_tables = set(inspect(bind).get_table_names())
    for table, columns in NEW_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = _columns(db, table)
        for name, ddl in columns:
            if name not in present:
                _execute(db, f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    _execute(
        db,
        "CREATE INDEX IF NOT EXISTS ix_apps_applications_source_installation_id "
        "ON apps_applications (source_installation_id)",
    )
    _execute(
        db,
        "CREATE INDEX IF NOT EXISTS ix_apps_operations_subject_id "
        "ON apps_operations (subject_id)",
    )
    if bind.dialect.name == "postgresql":
        _execute(
            db,
            "ALTER TABLE apps_operations ALTER COLUMN application_id DROP NOT NULL",
        )


def downgrade(db) -> None:
    bind = _bind(db)
    _execute(db, "DROP INDEX IF EXISTS ix_apps_operations_subject_id")
    _execute(db, "DROP INDEX IF EXISTS ix_apps_applications_source_installation_id")
    if bind.dialect.name == "postgresql":
        _execute(
            db,
            "ALTER TABLE apps_operations ALTER COLUMN application_id SET NOT NULL",
        )
    existing_tables = set(inspect(bind).get_table_names())
    for table, columns in NEW_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = _columns(db, table)
        for name, _ddl in reversed(columns):
            if name in present:
                _execute(db, f"ALTER TABLE {table} DROP COLUMN {name}")
    for table in (repositories, installations, providers):
        table.drop(bind=bind, checkfirst=True)
