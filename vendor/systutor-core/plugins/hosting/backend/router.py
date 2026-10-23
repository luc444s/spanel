"""HTTP surface of the hosting plugin. Entrypoints stay thin.

Every route declares the kernel `TenantContext` and the permission the manifest
ships for it. Site-scoped routes depend on `get_owned_site`, so the container
name is always read from an owned row.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

from .guard import get_owned_site
from .models import HostingSite
from .schemas import (
    CreateSiteRequest,
    CreateSiteResponse,
    HostingSiteListResponse,
    HostingSiteSummary,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SiteLogsResponse,
    WordPressUserListResponse,
)
from .service import HostingService

router = APIRouter(prefix="/sites", tags=["hosting"])


def _get_hosting_service(db: Session = Depends(get_db_session)) -> HostingService:
    return HostingService(db)


@router.get("", response_model=HostingSiteListResponse)
def list_sites(
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.read")),
    service: HostingService = Depends(_get_hosting_service),
) -> HostingSiteListResponse:
    return service.list_sites(tenant_context.current_tenant_id)


@router.post("", response_model=CreateSiteResponse, status_code=201)
def create_site(
    body: CreateSiteRequest,
    response: Response,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.create")),
    service: HostingService = Depends(_get_hosting_service),
) -> CreateSiteResponse:
    result = service.create_site(tenant_context, body.name, body.domain, body.admin_email)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{site_id}/start", response_model=HostingSiteSummary)
def start_site(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> HostingSiteSummary:
    result = service.start_site(site, tenant_context)
    db.commit()
    return result


@router.post("/{site_id}/stop", response_model=HostingSiteSummary)
def stop_site(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> HostingSiteSummary:
    result = service.stop_site(site, tenant_context)
    db.commit()
    return result


@router.post("/{site_id}/restart", response_model=HostingSiteSummary)
def restart_site(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> HostingSiteSummary:
    result = service.restart_site(site, tenant_context)
    db.commit()
    return result


@router.get("/{site_id}/logs", response_model=SiteLogsResponse)
def site_logs(
    lines: int = Query(default=200, ge=1),
    site: HostingSite = Depends(get_owned_site),
    _current_user: User = Depends(require_permission("hosting.sites.read")),
    service: HostingService = Depends(_get_hosting_service),
) -> SiteLogsResponse:
    return service.site_logs(site, lines)


@router.delete("/{site_id}", status_code=204)
def delete_site(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(
        require_permission("hosting.sites.delete", platform_only=True)
    ),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> None:
    service.delete_site(site, tenant_context)
    db.commit()


@router.get("/{site_id}/users", response_model=WordPressUserListResponse)
def list_site_users(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.users.read")),
    service: HostingService = Depends(_get_hosting_service),
) -> WordPressUserListResponse:
    return service.list_users(site)


@router.post("/{site_id}/users/{wp_user_id}/password", response_model=ResetPasswordResponse)
def reset_user_password(
    request: Request,
    response: Response,
    wp_user_id: int,
    site: HostingSite = Depends(get_owned_site),
    body: ResetPasswordRequest | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.users.password")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> ResetPasswordResponse:
    result = service.reset_password(
        site,
        wp_user_id=int(wp_user_id),
        password=body.password if body is not None else None,
        tenant_context=tenant_context,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    # The generated password must not be cached by any intermediary.
    response.headers["Cache-Control"] = "no-store"
    return result
