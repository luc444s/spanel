"""Create DNS zone ownership and public nameserver settings (A.SPEC 0067)."""

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
    text,
)

revision = "003_create_dns_zone_ownership"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))

zones = Table(
    "domains_dns_zones",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("zone_name", String(253), nullable=False),
    Column("kind", String(16), nullable=False, server_default="Native"),
    Column("observed_version", String(64), nullable=False, server_default=""),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("zone_name"),
    Index("ix_domains_dns_zones_tenant_id", "tenant_id"),
    Index("ix_domains_dns_zones_zone_name", "zone_name"),
)

settings = Table(
    "domains_dns_settings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ns1_hostname", String(253), nullable=False),
    Column("ns2_hostname", String(253), nullable=False),
    Column("ns1_ipv4", String(45), nullable=True),
    Column("ns1_ipv6", String(45), nullable=True),
    Column("ns2_ipv4", String(45), nullable=True),
    Column("ns2_ipv6", String(45), nullable=True),
    Column("soa_contact", String(253), nullable=False),
    Column("default_ttl", Integer, nullable=False, server_default="300"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
)


def upgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = inspect(bind).get_table_names()
    if "domains_dns_zones" not in names:
        zones.create(bind)
    if "domains_dns_settings" not in names:
        settings.create(bind)


def downgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = inspect(bind).get_table_names()
    if "domains_dns_settings" in names:
        settings.drop(bind)
    if "domains_dns_zones" in names:
        zones.drop(bind)
