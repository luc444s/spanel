"""Endpoints del gestor de certificados TLS de correo (A.SPEC 0035/0036)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from plugins.mail.backend.certificates.jobs import ActiveJobError
from plugins.mail.backend.certificates.schemas import (
    CertificateAdminActionPayload,
    CertificateAlertsResponse,
    CertificateHealthResponse,
    CertificateItem,
    CertificateJobResponse,
    CertificateRefreshResponse,
    CertificateRequestPayload,
    CertificateRequestResponse,
    CertificatesResponse,
)
from plugins.mail.backend.certificates.service import CertificatesService
from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/certificates", tags=["mail-certificates"])


def _get_certificate_service(
    request: Request, db: Session = Depends(get_db_session)
) -> CertificatesService:
    return CertificatesService(db, request.app)


def _publish_certificate(
    request: Request, event_name: str, *, tenant_id: str, payload: dict[str, object]
) -> None:
    context = getattr(request.app.state, "mail_context", None)
    if context is None:
        return
    try:
        context.publish_event(
            event_name=event_name,
            payload=payload,
            tenant_id=tenant_id,
            actor_user_id=str(payload.get("requested_by") or "") or None,
            entity_type="certificate",
            entity_id=str(payload.get("hostname") or "certificates"),
        )
    except Exception:  # noqa: BLE001 - la auditoría de base ya quedó registrada
        logger.warning("mail_event_publish_failed", extra={"event": event_name})


@router.get("", response_model=CertificatesResponse)
def list_certificates(
    tenant_only: bool = False,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificatesResponse:
    return service.list_certificates(tenant_context, tenant_only=tenant_only)


@router.post("/refresh", response_model=CertificateRefreshResponse)
def refresh_certificates(
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateRefreshResponse:
    response = service.refresh(tenant_context)
    _publish_certificate(
        request,
        "mail.certificate.refreshed",
        tenant_id=tenant_context.current_tenant_id,
        payload={
            "request_id": response.request_id,
            "requested_by": tenant_context.current_user_id,
        },
    )
    return response


@router.get("/health", response_model=CertificateHealthResponse)
def get_certificate_health(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateHealthResponse:
    return service.health(tenant_context)


@router.get("/alerts", response_model=CertificateAlertsResponse)
def get_certificate_alerts(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateAlertsResponse:
    return service.alerts(tenant_context)


def _admin_certificate_action(
    request: Request,
    service: CertificatesService,
    tenant_context: TenantContext,
    hostname: str,
    action: str,
    reason: str | None = None,
):
    try:
        response = service.admin_action(
            tenant_context, hostname, action=action, reason=reason
        )
    except ActiveJobError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "mail.certificate_job_active",
                    "message": str(exc),
                },
                "job_id": exc.job_id,
                "hostname": exc.hostname,
            },
        )
    _publish_certificate(
        request,
        (
            "mail.certificate.refreshed"
            if action == "verify"
            else "mail.certificate.requested"
        ),
        tenant_id=tenant_context.current_tenant_id,
        payload={
            "hostname": response.hostname,
            "job_id": response.job_id,
            "action": action,
            "requested_by": tenant_context.current_user_id,
        },
    )
    return response


@router.post(
    "/{hostname}/verify",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def verify_certificate(
    hostname: str,
    request: Request,
    body: CertificateAdminActionPayload | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
):
    return _admin_certificate_action(
        request, service, tenant_context, hostname, "verify", body.reason if body else None
    )


@router.post(
    "/{hostname}/sync",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def sync_certificate(
    hostname: str,
    request: Request,
    body: CertificateAdminActionPayload | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
):
    return _admin_certificate_action(
        request, service, tenant_context, hostname, "sync", body.reason if body else None
    )


@router.post(
    "/{hostname}/install",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def install_certificate(
    hostname: str,
    request: Request,
    body: CertificateAdminActionPayload | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
):
    return _admin_certificate_action(
        request, service, tenant_context, hostname, "install", body.reason if body else None
    )


@router.post(
    "/{hostname}/rollback",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def rollback_certificate(
    hostname: str,
    request: Request,
    body: CertificateAdminActionPayload | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
):
    return _admin_certificate_action(
        request, service, tenant_context, hostname, "rollback", body.reason if body else None
    )


@router.get("/{hostname}", response_model=CertificateItem)
def get_mail_certificate(
    hostname: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateItem:
    return service.get_certificate(tenant_context, hostname)


@router.get("/{hostname}/jobs/{job_id}", response_model=CertificateJobResponse)
def get_certificate_job(
    hostname: str,
    job_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateJobResponse:
    return service.get_job(tenant_context, hostname, job_id)


@router.post(
    "/{hostname}/request",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def request_certificate(
    hostname: str,
    body: CertificateRequestPayload,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("mail.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
):
    try:
        response = service.request_certificate(
            tenant_context,
            hostname,
            action=body.action,
            reason=body.reason,
        )
    except ActiveJobError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "mail.certificate_job_active",
                    "message": str(exc),
                },
                "job_id": exc.job_id,
                "hostname": exc.hostname,
            },
        )
    _publish_certificate(
        request,
        "mail.certificate.requested",
        tenant_id=tenant_context.current_tenant_id,
        payload={
            "hostname": response.hostname,
            "job_id": response.job_id,
            "action": body.action,
            "requested_by": tenant_context.current_user_id,
        },
    )
    return response
