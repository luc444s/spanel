from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from time import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.api.v1.core.schemas import (
    CoreDocumentRenderRequest,
    CoreDocumentSignedDownloadRead,
    CoreDocumentVersionRead,
)
from systutor.core.config import Settings, get_settings
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    require_any_permission,
)
from systutor.kernel.auth.models import User
from systutor.kernel.documents.service import (
    build_document_download_url,
    get_document_version,
    list_document_versions_for_entity,
    render_document_pdf,
    resolve_document_file_path,
)
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/core/documents", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_DOCUMENT_WRITE = Depends(
    require_any_permission("core.roles.manage", "core.documents.manage")
)
REQUIRE_DOCUMENT_READ = Depends(
    require_any_permission(
        "core.roles.manage",
        "core.documents.read",
        "core.documents.manage",
    )
)
SETTINGS_DEP = Depends(get_settings)


def _document_to_read(document) -> CoreDocumentVersionRead:
    return CoreDocumentVersionRead(
        id=document.id,
        tenant_id=document.tenant_id,
        module=document.module,
        entity_type=document.entity_type,
        entity_id=document.entity_id,
        template_code=document.template_code,
        version_number=document.version_number,
        status=document.status,
        title=document.title,
        file_path=build_document_download_url(document.id),
        sha256=document.sha256,
        created_by=document.created_by,
        created_at=document.created_at,
    )


def _build_signed_download_signature(
    *,
    document_version_id: str,
    tenant_id: str,
    expires_at: int,
    settings: Settings,
) -> str:
    payload = f"{document_version_id}:{tenant_id}:{expires_at}".encode()
    digest = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return digest


def _build_signed_download_url(
    *,
    document_version_id: str,
    tenant_id: str,
    settings: Settings,
    ttl_seconds: int = 300,
) -> CoreDocumentSignedDownloadRead:
    expires_at = int(time()) + ttl_seconds
    signature = _build_signed_download_signature(
        document_version_id=document_version_id,
        tenant_id=tenant_id,
        expires_at=expires_at,
        settings=settings,
    )
    return CoreDocumentSignedDownloadRead(
        url=(
            f"/api/v1/core/documents/{document_version_id}/signed-download"
            f"?tenant_id={tenant_id}&expires_at={expires_at}&signature={signature}"
        ),
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
    )


@router.post("/render", response_model=CoreDocumentVersionRead, status_code=status.HTTP_201_CREATED)
def render_document_endpoint(
    payload: CoreDocumentRenderRequest,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_DOCUMENT_WRITE,
) -> CoreDocumentVersionRead:
    document = render_document_pdf(
        db,
        tenant_id=tenant_context.current_tenant_id,
        module=payload.module,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        template_code=payload.template_code,
        payload=payload.payload,
        created_by=tenant_context.current_user_id,
        status=payload.status,
    )
    db.commit()
    return _document_to_read(document)


@router.get("/by-entity", response_model=list[CoreDocumentVersionRead])
def list_documents_by_entity_endpoint(
    module: str = Query(...),
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_DOCUMENT_READ,
) -> list[CoreDocumentVersionRead]:
    documents = list_document_versions_for_entity(
        db,
        tenant_id=tenant_context.current_tenant_id,
        module=module,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return [_document_to_read(doc) for doc in documents]


@router.get("/{document_version_id}", response_model=CoreDocumentVersionRead)
def get_document_endpoint(
    document_version_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_DOCUMENT_READ,
) -> CoreDocumentVersionRead:
    document = get_document_version(db, document_version_id=document_version_id)
    if document is None or document.tenant_id != tenant_context.current_tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _document_to_read(document)


@router.get("/{document_version_id}/signed-url", response_model=CoreDocumentSignedDownloadRead)
def get_document_signed_url_endpoint(
    document_version_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_DOCUMENT_READ,
    settings: Settings = SETTINGS_DEP,
) -> CoreDocumentSignedDownloadRead:
    document = get_document_version(db, document_version_id=document_version_id)
    if document is None or document.tenant_id != tenant_context.current_tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return _build_signed_download_url(
        document_version_id=document.id,
        tenant_id=tenant_context.current_tenant_id,
        settings=settings,
    )


@router.get("/{document_version_id}/download")
def download_document_endpoint(
    document_version_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_DOCUMENT_READ,
) -> FileResponse:
    document = get_document_version(db, document_version_id=document_version_id)
    if document is None or document.tenant_id != tenant_context.current_tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    path = resolve_document_file_path(document)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@router.get("/{document_version_id}/signed-download")
def download_document_signed_endpoint(
    document_version_id: str,
    tenant_id: str = Query(...),
    expires_at: int = Query(...),
    signature: str = Query(...),
    db: Session = DB_SESSION,
    settings: Settings = SETTINGS_DEP,
) -> FileResponse:
    if expires_at < int(time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signed URL expired",
        )
    expected_signature = _build_signed_download_signature(
        document_version_id=document_version_id,
        tenant_id=tenant_id,
        expires_at=expires_at,
        settings=settings,
    )
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signed URL")
    document = get_document_version(db, document_version_id=document_version_id)
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    path = resolve_document_file_path(document)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type="application/pdf",
        content_disposition_type="inline",
    )
