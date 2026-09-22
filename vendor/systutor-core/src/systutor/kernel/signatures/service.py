from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.kernel.documents.models import CoreDocumentVersion
from systutor.kernel.events.service import emit_event
from systutor.kernel.signatures.models import CoreSignatureEvidence, CoreSignatureSession


def create_signature_session(
    db: Session,
    *,
    tenant_id: str,
    document_version_id: str,
    signer_name: str | None = None,
    signer_email: str | None = None,
    signer_phone: str | None = None,
    signer_role: str | None = None,
    provider: str = "INTERNAL",
    verification_channel: str = "IN_APP",
) -> CoreSignatureSession:
    session = CoreSignatureSession(
        tenant_id=tenant_id,
        document_version_id=document_version_id,
        signer_name=signer_name,
        signer_email=signer_email,
        signer_phone=signer_phone,
        signer_role=signer_role,
        provider=provider,
        status="PENDING",
        verification_channel=verification_channel,
    )
    db.add(session)
    db.flush()

    document = db.scalar(
        select(CoreDocumentVersion).where(CoreDocumentVersion.id == document_version_id)
    )

    emit_event(
        db,
        event=EventContract(
            event_name="core.signature.session_created",
            module="core",
            tenant_id=tenant_id,
            actor_user_id=None,
            actor_type="system",
            entity_type=document.entity_type if document else None,
            entity_id=document.entity_id if document else None,
            payload={
                "signature_session_id": session.id,
                "document_version_id": document_version_id,
                "signer_role": signer_role,
                "verification_channel": verification_channel,
            },
        ),
    )

    return session


def get_signature_session(db: Session, *, session_id: str) -> CoreSignatureSession | None:
    return db.scalar(select(CoreSignatureSession).where(CoreSignatureSession.id == session_id))


def list_signature_sessions_for_entity(
    db: Session,
    *,
    tenant_id: str,
    module: str,
    entity_type: str,
    entity_id: str,
) -> list[CoreSignatureSession]:
    return list(
        db.scalars(
            select(CoreSignatureSession)
            .join(
                CoreDocumentVersion,
                CoreDocumentVersion.id == CoreSignatureSession.document_version_id,
            )
            .where(
                CoreSignatureSession.tenant_id == tenant_id,
                CoreDocumentVersion.tenant_id == tenant_id,
                CoreDocumentVersion.module == module,
                CoreDocumentVersion.entity_type == entity_type,
                CoreDocumentVersion.entity_id == entity_id,
            )
            .order_by(CoreSignatureSession.created_at.desc())
        ).all()
    )


def get_latest_pending_signature_session_for_entity(
    db: Session,
    *,
    tenant_id: str,
    module: str,
    entity_type: str,
    entity_id: str,
) -> CoreSignatureSession | None:
    return db.scalar(
        select(CoreSignatureSession)
        .join(
            CoreDocumentVersion,
            CoreDocumentVersion.id == CoreSignatureSession.document_version_id,
        )
        .where(
            CoreSignatureSession.tenant_id == tenant_id,
            CoreSignatureSession.status == "PENDING",
            CoreDocumentVersion.tenant_id == tenant_id,
            CoreDocumentVersion.module == module,
            CoreDocumentVersion.entity_type == entity_type,
            CoreDocumentVersion.entity_id == entity_id,
        )
        .order_by(CoreSignatureSession.created_at.desc())
        .limit(1)
    )


def complete_signature_session(
    db: Session,
    *,
    session: CoreSignatureSession,
    signer_name: str,
    signer_email: str | None,
    signer_phone: str | None,
    evidence_type: str,
    evidence_payload: dict,
) -> CoreSignatureSession:
    session.signer_name = signer_name.strip()
    session.signer_email = signer_email.strip() if signer_email else None
    session.signer_phone = signer_phone.strip() if signer_phone else None
    session.status = "COMPLETED"
    session.completed_at = datetime.now(UTC)
    db.add(session)
    db.flush()

    db.add(
        CoreSignatureEvidence(
            tenant_id=session.tenant_id,
            signature_session_id=session.id,
            evidence_type=evidence_type,
            payload_json=evidence_payload,
        )
    )
    db.flush()

    document = db.scalar(
        select(CoreDocumentVersion).where(CoreDocumentVersion.id == session.document_version_id)
    )

    emit_event(
        db,
        event=EventContract(
            event_name="core.signature.completed",
            module="core",
            tenant_id=session.tenant_id,
            actor_user_id=None,
            actor_type="system",
            entity_type=document.entity_type if document else None,
            entity_id=document.entity_id if document else None,
            payload={
                "signature_session_id": session.id,
                "document_version_id": session.document_version_id,
                "evidence_type": evidence_type,
                "signer_name": signer_name.strip(),
            },
        ),
    )

    return session
