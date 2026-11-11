"""Superficie HTTP del plugin de dominios.

La ruta es delgada: valida permisos, delega en `DomainsService` y publica el
evento declarado. No arma YAML ni comandos.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from plugins.domains.backend.schemas import (
    AvailableDomainsResponse,
    CertificateResponse,
    CreateDomainRequest,
    CreateDomainsResponse,
    DomainCheckResponse,
    DomainsResponse,
    LinkDomainRequest,
    RouteCreate,
    RouteItem,
    RouteProbeResponse,
    RoutesResponse,
    RouteUpdate,
)
from plugins.domains.backend.service import DomainsService
from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

logger = logging.getLogger(__name__)


def _response(items, sites, proxy_error: str | None) -> DomainsResponse:
    return DomainsResponse(
        items=items,
        sites=sites,
        proxy_available=proxy_error is None,
        proxy_error=proxy_error,
    )

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
            "scope must be 'tenant' or 'all'", status_code=422, code="domains.invalid_scope"
        )
    if scope == "all":
        if not tenant_context.is_platform:
            raise AppError(
                "Only the platform administrator can view all tenants",
                status_code=403,
                code="domains.platform_scope_forbidden",
            )
        items, sites, proxy_error = service.list_all_domains()
        return _response(items, sites, proxy_error)
    items, sites, proxy_error = service.list_domains(
        tenant_context.current_tenant_id,
        can_manage=tenant_context.has_permission("domains.domains.manage"),
    )
    return _response(items, sites, proxy_error)


@router.get("/check", response_model=DomainCheckResponse)
def check_domain(
    domain: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> DomainCheckResponse:
    return service.check_domain(tenant_context, domain)


@router.get("/available", response_model=AvailableDomainsResponse)
def available_domains(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> AvailableDomainsResponse:
    return service.available_domains(tenant_context.current_tenant_id)


@router.get("/routes", response_model=RoutesResponse)
def list_routes(
    site_id: str | None = None,
    domain: str | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> RoutesResponse:
    return RoutesResponse(
        items=service.list_routes(tenant_context, site_id=site_id, domain=domain)
    )


@router.post("/routes", response_model=RouteItem, status_code=201)
def create_route(
    body: RouteCreate,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> RouteItem:
    return service.create_route(tenant_context, body)


@router.get("/routes/{route_id}", response_model=RouteItem)
def get_route(
    route_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> RouteItem:
    return service.get_route(tenant_context, route_id)


@router.patch("/routes/{route_id}", response_model=RouteItem)
def update_route(
    route_id: str,
    body: RouteUpdate,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> RouteItem:
    return service.update_route(tenant_context, route_id, body)


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(
    route_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> Response:
    service.delete_route(tenant_context, route_id)
    return Response(status_code=204)


@router.post("/routes/{route_id}/probe", response_model=RouteProbeResponse)
def probe_route(
    route_id: str,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.read")),
    service: DomainsService = Depends(_get_service),
) -> RouteProbeResponse:
    return service.probe_route(tenant_context, route_id)


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


@router.post("/{domain}/link", response_model=CreateDomainsResponse, status_code=200)
def link_domain(
    domain: str,
    body: LinkDomainRequest,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("domains.domains.manage")),
    service: DomainsService = Depends(_get_service),
) -> CreateDomainsResponse:
    items = service.link(
        tenant_context,
        domain=domain,
        site_id=body.site_id,
        www=body.www,
    )
    for item in items:
        _publish(
            request,
            "domains.domain.linked",
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
