"""Superficie HTTP del plugin de dominios.

La ruta es delgada: valida permisos, delega en `DomainsService` y publica el
evento declarado. No arma YAML ni comandos.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from plugins.domains.backend.schemas import (
    CertificateResponse,
    CreateDomainRequest,
    CreateDomainsResponse,
    DomainCheckResponse,
    DomainsResponse,
)
from plugins.domains.backend.service import DomainsService
from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(tags=["domains"])


def _get_service(request: Request, db: Session = Depends(get_db_session)) -> DomainsService:
    return DomainsService(db, request.app)


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
    scope: str = "tenant",
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> DomainsResponse:
    if scope not in ("tenant", "all"):
        raise AppError(
            "scope debe ser 'tenant' o 'all'", status_code=422, code="domains.invalid_scope"
        )
    if scope == "all":
        if not tenant_context.is_platform:
            raise AppError(
                "Solo el administrador de plataforma puede ver todos los tenants",
                status_code=403,
                code="domains.platform_scope_forbidden",
            )
        items, sites = service.list_all_domains()
        return DomainsResponse(items=items, sites=sites)
    items, sites = service.list_domains(
        tenant_context.current_tenant_id,
        can_manage=tenant_context.has_permission("domains.domains.manage"),
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
    )
