# ruff: noqa: E501
"""Create durable Apps ownership, OAuth, deployment and operation tables."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

revision = "001_create_apps_tables"
metadata = MetaData()
Table("tenants", metadata, Column("id", String(36), primary_key=True))
Table("users", metadata, Column("id", String(36), primary_key=True))

connections = Table(
    "apps_github_connections", metadata,
    Column("user_id", String(36), ForeignKey("users.id"), primary_key=True),
    Column("github_user_id", String(64), nullable=False), Column("login", String(255), nullable=False),
    Column("avatar_url", String(1000)), Column("scopes", JSON, nullable=False),
    Column("access_ciphertext", LargeBinary, nullable=False), Column("access_nonce", LargeBinary, nullable=False),
    Column("refresh_ciphertext", LargeBinary), Column("refresh_nonce", LargeBinary),
    Column("key_version", Integer, nullable=False), Column("access_expires_at", DateTime(timezone=True)),
    Column("refresh_expires_at", DateTime(timezone=True)), Column("reconnect_required", Boolean, nullable=False),
    Column("connected_at", DateTime(timezone=True), nullable=False), Column("last_used_at", DateTime(timezone=True)),
)
applications = Table(
    "apps_applications", metadata, Column("id", String(36), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False), Column("name", String(255), nullable=False),
    Column("slug", String(100), nullable=False), Column("repository", String(510), nullable=False),
    Column("branch", String(255), nullable=False), Column("compose_file", String(510)),
    Column("source_connection_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("runtime_project", String(63), nullable=False, unique=True), Column("domain", String(255), unique=True),
    Column("ingress_service", String(255)), Column("ingress_port", Integer), Column("status", String(32), nullable=False),
    Column("active_deployment_id", String(36)), Column("active_operation_id", String(36)),
    Column("deleted_at", DateTime(timezone=True)), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False), UniqueConstraint("tenant_id", "slug"),
)
deployments = Table(
    "apps_deployments", metadata, Column("id", String(36), primary_key=True),
    Column("application_id", String(36), ForeignKey("apps_applications.id"), nullable=False),
    Column("operation_id", String(36), nullable=False, unique=True), Column("repository", String(510), nullable=False),
    Column("branch", String(255), nullable=False), Column("commit", String(64)), Column("tree_hash", String(64)),
    Column("compose_file", String(510)), Column("ingress_service", String(255)), Column("ingress_port", Integer),
    Column("status", String(32), nullable=False), Column("release", String(1000)), Column("manifest", JSON),
    Column("public_error", String(500)), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
)
operations = Table(
    "apps_operations", metadata, Column("id", String(36), primary_key=True),
    Column("application_id", String(36), ForeignKey("apps_applications.id"), nullable=False),
    Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    Column("actor_user_id", String(36), ForeignKey("users.id"), nullable=False), Column("kind", String(32), nullable=False),
    Column("status", String(32), nullable=False), Column("phase", String(64), nullable=False),
    Column("correlation_id", String(100)), Column("idempotency_key", String(255), nullable=False),
    Column("request_hash", String(64), nullable=False), Column("message_id", String(255)),
    Column("heartbeat_at", DateTime(timezone=True)), Column("attempt", Integer, nullable=False),
    Column("report", JSON), Column("result", JSON), Column("error_code", String(100)), Column("public_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("actor_user_id", "idempotency_key"),
)
Index("ix_apps_applications_tenant_id", applications.c.tenant_id)
Index("ix_apps_applications_active_operation_id", applications.c.active_operation_id)
Index("ix_apps_deployments_application_id", deployments.c.application_id)
Index("ix_apps_operations_application_id", operations.c.application_id)
Index("ix_apps_operations_tenant_id", operations.c.tenant_id)

def upgrade(db) -> None:
    for table in (connections, applications, deployments, operations):
        table.create(bind=db.get_bind(), checkfirst=True)

def downgrade(db) -> None:
    for table in (operations, deployments, applications, connections):
        table.drop(bind=db.get_bind(), checkfirst=True)
