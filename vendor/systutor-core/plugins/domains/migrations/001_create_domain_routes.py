"""Create stable Domains routes for registered domains (A.SPEC 0060)."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
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

revision = "001_create_domain_routes"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))

routes = Table(
    "domains_routes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("domain", String(253), nullable=False),
    Column("path", String(255), nullable=False),
    Column("internal_path", String(255), nullable=False),
    Column("strip_path", Boolean, nullable=False),
    Column("port", Integer, nullable=False),
    Column("entrypoints", JSON, nullable=False),
    Column("https", Boolean, nullable=False),
    Column("middlewares", JSON, nullable=False),
    Column("site_id", String(504), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "domain", "path"),
)


def upgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    if "domains_routes" in inspect(bind).get_table_names():
        return
    routes.create(bind)
    for column in ("tenant_id", "domain", "site_id"):
        Index(f"ix_domains_routes_{column}", routes.c[column]).create(bind)


def downgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    if "domains_routes" in inspect(bind).get_table_names():
        routes.drop(bind)
