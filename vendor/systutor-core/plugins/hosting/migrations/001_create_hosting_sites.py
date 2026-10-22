"""Create the `hosting_sites` ownership table.

Idempotent on purpose: the plugin runtime may call upgrade twice (install and
boot), and `checkfirst=True` makes the second call a no-op. The same code runs
on PostgreSQL and on the SQLite test database.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, MetaData, String, Table

revision = "001_create_hosting_sites"

metadata = MetaData()

# Reference-only table: the FK target already exists in the kernel schema. The
# object is here so SQLAlchemy can compile the foreign key; this migration
# never creates or touches `tenants`.
Table("tenants", metadata, Column("id", String(36), primary_key=True))

hosting_sites = Table(
    "hosting_sites",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("container_name", String(255), nullable=False, unique=True),
    # PostgreSQL allows several NULLs in a UNIQUE column, so two sites without
    # a domain do not collide.
    Column("domain", String(255), nullable=True, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Index("ix_hosting_sites_tenant_id", hosting_sites.c.tenant_id)


def upgrade(db) -> None:
    hosting_sites.create(bind=db.get_bind(), checkfirst=True)


def downgrade(db) -> None:
    hosting_sites.drop(bind=db.get_bind(), checkfirst=True)
