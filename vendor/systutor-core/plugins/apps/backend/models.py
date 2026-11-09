"""Tenant-aware persistence for the Apps plugin."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base
from systutor.kernel.auth.models import utc_now


def new_id() -> str:
    return str(uuid4())


class GithubProvider(Base):
    __tablename__ = "apps_github_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    github_app_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    app_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_login: Mapped[str | None] = mapped_column(String(255))
    owner_type: Mapped[str | None] = mapped_column(String(32))
    private_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    private_key_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending_installation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GithubInstallation(Base):
    __tablename__ = "apps_github_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("apps_github_providers.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    github_installation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(32))
    repository_selection: Mapped[str] = mapped_column(String(32), default="selected")
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active")
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GithubRepository(Base):
    __tablename__ = "apps_github_repositories"
    __table_args__ = (
        UniqueConstraint("installation_id", "github_repository_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("apps_github_installations.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    github_repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(510), nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(Base):
    __tablename__ = "apps_applications"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100))
    repository: Mapped[str] = mapped_column(String(510))
    branch: Mapped[str] = mapped_column(String(255), default="main")
    compose_file: Mapped[str | None] = mapped_column(String(510))
    # Legacy creator identity, retained for audit and for pre-0053 applications.
    source_connection_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("apps_github_installations.id")
    )
    source_repository_id: Mapped[str | None] = mapped_column(
        ForeignKey("apps_github_repositories.id")
    )
    source_status: Mapped[str] = mapped_column(String(32), default="connected")
    runtime_project: Mapped[str] = mapped_column(String(63), unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    ingress_service: Mapped[str | None] = mapped_column(String(255))
    ingress_port: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    active_deployment_id: Mapped[str | None] = mapped_column(String(36))
    active_operation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Deployment(Base):
    __tablename__ = "apps_deployments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("apps_applications.id"), index=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    repository: Mapped[str] = mapped_column(String(510))
    branch: Mapped[str] = mapped_column(String(255))
    commit: Mapped[str | None] = mapped_column(String(64))
    tree_hash: Mapped[str | None] = mapped_column(String(64))
    compose_file: Mapped[str | None] = mapped_column(String(510))
    ingress_service: Mapped[str | None] = mapped_column(String(255))
    ingress_port: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="building")
    release: Mapped[str | None] = mapped_column(String(1000))
    manifest: Mapped[dict | None] = mapped_column(JSON)
    public_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Operation(Base):
    __tablename__ = "apps_operations"
    __table_args__ = (UniqueConstraint("actor_user_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("apps_applications.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    phase: Mapped[str] = mapped_column(String(64), default="queued")
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(255))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    public_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
