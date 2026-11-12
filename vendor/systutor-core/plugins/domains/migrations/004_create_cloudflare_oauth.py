"""Create Cloudflare OAuth client, connection and flow tables (A.SPEC 0071)."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
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

revision = "004_create_cloudflare_oauth"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))

oauth_client = Table(
    "domains_cloudflare_oauth_client",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", String(32), nullable=False),
    Column("client_id", String(255), nullable=False),
    Column("secret_ciphertext", LargeBinary, nullable=False),
    Column("secret_nonce", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False, server_default="1"),
    Column("redirect_uri", String(1000), nullable=False),
    Column("scopes_json", JSON, nullable=False),
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
)

connections = Table(
    "domains_cloudflare_connections",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("cloudflare_account_id", String(32), nullable=False),
    Column("account_name", String(255), nullable=False),
    Column("access_ciphertext", LargeBinary, nullable=False),
    Column("access_nonce", LargeBinary, nullable=False),
    Column("refresh_ciphertext", LargeBinary, nullable=True),
    Column("refresh_nonce", LargeBinary, nullable=True),
    Column("key_version", Integer, nullable=False, server_default="1"),
    Column("scopes_json", JSON, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("status", String(32), nullable=False, server_default="active"),
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
    UniqueConstraint("tenant_id"),
    Index("ix_domains_cloudflare_connections_tenant_id", "tenant_id"),
)

flows = Table(
    "domains_cloudflare_oauth_flows",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("verifier_ciphertext", LargeBinary, nullable=False),
    Column("verifier_nonce", LargeBinary, nullable=False),
    Column("key_version", Integer, nullable=False, server_default="1"),
    Column("redirect_uri", String(1000), nullable=False),
    Column("status", String(32), nullable=False, server_default="pending"),
    Column("error_code", String(64), nullable=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
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
    UniqueConstraint("state_hash"),
    Index("ix_domains_cloudflare_oauth_flows_tenant_id", "tenant_id"),
    Index("ix_domains_cloudflare_oauth_flows_state_hash", "state_hash"),
)


def upgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = set(inspect(bind).get_table_names())
    for table in (oauth_client, connections, flows):
        if table.name not in names:
            table.create(bind)


def downgrade(db) -> None:
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    names = set(inspect(bind).get_table_names())
    for table in (flows, connections, oauth_client):
        if table.name in names:
            table.drop(bind)
