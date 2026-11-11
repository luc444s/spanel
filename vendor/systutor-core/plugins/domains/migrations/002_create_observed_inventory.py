"""Create the rebuildable Traefik domain observation (A.SPEC 0064)."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    inspect,
)

revision = "002_create_observed_inventory"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))

observed = Table(
    "domains_observed",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("domain", String(253), nullable=False),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("site_id", String(504), nullable=False),
    Column("container", String(255), nullable=False),
    Column("resolver", String(255), nullable=False),
    Column("source_file", String(255), nullable=False),
    Column("document_hash", String(64), nullable=False),
    Column("generation", String(36), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("generation", "domain"),
)

state = Table(
    "domains_sync_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("active_generation", String(36), nullable=True),
    Column("synced_at", DateTime(timezone=True), nullable=True),
    Column("document_count", Integer, nullable=False),
    Column("domain_count", Integer, nullable=False),
    Column("conflict_count", Integer, nullable=False),
    Column("orphan_count", Integer, nullable=False),
    Column("invalid_count", Integer, nullable=False),
    Column("last_error", String(1000), nullable=True),
)


def upgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = inspect(bind).get_table_names()
    if "domains_observed" not in names:
        observed.create(bind)
        for column in ("domain", "tenant_id", "site_id", "generation"):
            Index(f"ix_domains_observed_{column}", observed.c[column]).create(bind)
    if "domains_sync_state" not in names:
        state.create(bind)


def downgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = inspect(bind).get_table_names()
    if "domains_sync_state" in names:
        state.drop(bind)
    if "domains_observed" in names:
        observed.drop(bind)
