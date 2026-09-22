from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from systutor.core.database import Base


def new_uuid() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class CoreSignatureSession(Base):
    __tablename__ = "core_signature_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("core_document_versions.id"), nullable=False, index=True
    )
    signer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signer_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="INTERNAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    verification_channel: Mapped[str] = mapped_column(String(30), nullable=False, default="IN_APP")
    verification_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CoreSignatureEvidence(Base):
    __tablename__ = "core_signature_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    signature_session_id: Mapped[str] = mapped_column(
        ForeignKey("core_signature_sessions.id"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
