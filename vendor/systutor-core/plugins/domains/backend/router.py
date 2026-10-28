"""Superficie HTTP del plugin de dominios.

La ruta es delgada: valida permisos, delega en `DomainsService` y publica el
evento declarado. No arma YAML ni comandos.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from plugins.domains.backend.certificates.jobs import ActiveJobError
from plugins.domains.backend.certificates.service import CertificatesService
from plugins.domains.backend.schemas import (
    CertificateItem,
    CertificateJobResponse,
    CertificateRefreshResponse,
    CertificateRequestPayload,
    CertificateRequestResponse,
    CertificateResponse,
    CertificatesResponse,
    CreateDomainRequest,
    CreateDomainsResponse,
    DomainCheckResponse,
    DomainsResponse,
)
from plugins.domains.backend.service import DomainsService
from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["domains"])


def _get_service(request: Request, db: Session = Depends(get_db_session)) -> DomainsService:
    return DomainsService(db, request.app)


def _get_certificate_service(
    request: Request, db: Session = Depends(get_db_session)
) -> CertificatesService:
    return CertificatesService(db, request.app)


def _publish_certificate(
    request: Request, event_name: str, *, tenant_id: str, payload: dict[str, object]
) -> None:
    context = getattr(request.app.state, "domains_context", None)
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
        logger.warning("domains_event_publish_failed", extra={"event": event_name})


def _publish(request: Request, event_name: str, *, tenant_id: str, domain: str) -> None:
    context = getattr(request.app.state, "domains_context", None)
    if context is None:
        return
    try:
        context.publish_event(
            event_name=event_name,
            payload={"domain": domain},
            tenant_id=tenant_id,
            entity_type="domain",
            entity_id=domain,
        )
    except Exception:  # noqa: BLE001 - la auditoría ya quedó registrada
        logger.warning("domains_event_publish_failed", extra={"event": event_name})


@router.get("", response_model=DomainsResponse)
def list_domains(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> DomainsResponse:
    items, sites = service.list_domains(
        tenant_context.current_tenant_id,
        can_manage=tenant_context.has_permission("domains.domains.manage"),
        include_outside=tenant_context.is_platform,
    )
    return DomainsResponse(items=items, sites=sites)


@router.get("/check", response_model=DomainCheckResponse)
def check_domain(
    domain: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> DomainCheckResponse:
    return service.check_domain(tenant_context, domain)


@router.post("", response_model=CreateDomainsResponse, status_code=201)
def create_domain(
    body: CreateDomainRequest,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> CreateDomainsResponse:
    items = service.add(
        tenant_context,
        site_id=body.site_id,
        domain=body.domain,
        www=body.www,
    )
    for item in items:
        _publish(
            request,
            "domains.domain.added",
            tenant_id=tenant_context.current_tenant_id,
            domain=item.domain,
        )
    return CreateDomainsResponse(items=items)


@router.delete("/{domain}", status_code=204)
def delete_domain(
    domain: str,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> Response:
    service.remove(tenant_context, domain)
    _publish(
        request,
        "domains.domain.removed",
        tenant_id=tenant_context.current_tenant_id,
        domain=domain,
    )
    return Response(status_code=204)


@router.get("/certificates", response_model=CertificatesResponse)
def list_certificates(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificatesResponse:
    return service.list_certificates(tenant_context)


@router.post("/certificates/refresh", response_model=CertificateRefreshResponse)
def refresh_certificates(
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.certificates.manage")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateRefreshResponse:
    response = service.refresh(tenant_context)
    _publish_certificate(
        request,
        "domains.certificate.refreshed",
        tenant_id=tenant_context.current_tenant_id,
        payload={
            "request_id": response.request_id,
            "requested_by": tenant_context.current_user_id,
        },
    )
    return response


@router.get("/certificates/{hostname}", response_model=CertificateItem)
def get_mail_certificate(
    hostname: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateItem:
    return service.get_certificate(tenant_context, hostname)


@router.get("/certificates/{hostname}/jobs/{job_id}", response_model=CertificateJobResponse)
def get_certificate_job(
    hostname: str,
    job_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.certificates.read")),
    service: CertificatesService = Depends(_get_certificate_service),
) -> CertificateJobResponse:
    return service.get_job(tenant_context, hostname, job_id)


@router.post(
    "/certificates/{hostname}/request",
    response_model=CertificateRequestResponse,
    status_code=202,
)
def request_certificate(
    hostname: str,
    body: CertificateRequestPayload,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.certificates.manage")),
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
                    "code": "domains.certificate_job_active",
                    "message": str(exc),
                },
                "job_id": exc.job_id,
                "hostname": exc.hostname,
            },
        )
    _publish_certificate(
        request,
        "domains.certificate.requested",
        tenant_id=tenant_context.current_tenant_id,
        payload={
            "hostname": response.hostname,
            "job_id": response.job_id,
            "action": body.action,
            "requested_by": tenant_context.current_user_id,
        },
    )
    return response


@router.get("/{domain}/certificate", response_model=CertificateResponse)
def get_certificate(
    domain: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> CertificateResponse:
    return service.certificate(
        tenant_context.current_tenant_id,
        domain,
        include_outside=tenant_context.is_platform,
    )
