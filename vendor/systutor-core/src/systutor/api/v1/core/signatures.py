from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.api.v1.core.schemas import (
    CoreSignatureCompleteRequest,
    CoreSignatureSessionCreateRequest,
    CoreSignatureSessionRead,
)
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_any_permission
from systutor.kernel.auth.models import User
from systutor.kernel.signatures.service import (
    complete_signature_session,
    create_signature_session,
    get_signature_session,
    list_signature_sessions_for_entity,
)
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/core/signatures/sessions", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_SIGNATURE_WRITE = Depends(
    require_any_permission("core.roles.manage", "core.signatures.manage")
)
REQUIRE_SIGNATURE_READ = Depends(
    require_any_permission(
        "core.roles.manage",
        "core.signatures.read",
        "core.signatures.manage",
    )
)


@router.post("", response_model=CoreSignatureSessionRead, status_code=status.HTTP_201_CREATED)
def create_signature_session_endpoint(
    payload: CoreSignatureSessionCreateRequest,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_SIGNATURE_WRITE,
) -> CoreSignatureSessionRead:
    session = create_signature_session(
        db,
        tenant_id=tenant_context.current_tenant_id,
        document_version_id=payload.document_version_id,
        signer_name=payload.signer_name,
        signer_email=payload.signer_email,
        signer_phone=payload.signer_phone,
        signer_role=payload.signer_role,
        provider=payload.provider,
        verification_channel=payload.verification_channel,
    )
    db.commit()
    return CoreSignatureSessionRead.model_validate(session)


@router.get("/by-entity", response_model=list[CoreSignatureSessionRead])
def list_signature_sessions_by_entity_endpoint(
    module: str = Query(default=None),
    entity_type: str = Query(default=None),
    entity_id: str = Query(default=None),
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_SIGNATURE_READ,
) -> list[CoreSignatureSessionRead]:
    sessions = list_signature_sessions_for_entity(
        db,
        tenant_id=tenant_context.current_tenant_id,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return [CoreSignatureSessionRead.model_validate(s) for s in sessions]


@router.get("/{session_id}", response_model=CoreSignatureSessionRead)
def get_signature_session_endpoint(
    session_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_SIGNATURE_READ,
) -> CoreSignatureSessionRead:
    session = get_signature_session(db, session_id=session_id)
    if session is None or session.tenant_id != tenant_context.current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signature session not found",
        )
    return CoreSignatureSessionRead.model_validate(session)


@router.post("/{session_id}/complete", response_model=CoreSignatureSessionRead)
def complete_signature_session_endpoint(
    session_id: str,
    payload: CoreSignatureCompleteRequest,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_SIGNATURE_WRITE,
) -> CoreSignatureSessionRead:
    session = get_signature_session(db, session_id=session_id)
    if session is None or session.tenant_id != tenant_context.current_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signature session not found",
        )
    session = complete_signature_session(
        db,
        session=session,
        signer_name=payload.signer_name,
        signer_email=payload.signer_email,
        signer_phone=payload.signer_phone,
        evidence_type=payload.evidence_type,
        evidence_payload=payload.evidence_payload,
    )
    db.commit()
    return CoreSignatureSessionRead.model_validate(session)
