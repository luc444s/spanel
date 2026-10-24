"""HTTP surface of the hosting plugin. Entrypoints stay thin.

Every route declares the kernel `TenantContext` and the permission the manifest
ships for it. Site-scoped routes depend on `get_owned_site`, so the container
name is always read from an owned row.
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

from .guard import get_owned_site
from .models import HostingSite
from .phpmyadmin_proxy import PmaClient, proxy_response, validate_url
from .phpmyadmin_session import (
    COOKIE,
    PmaSession,
    authorize_proxy_session,
    create_session,
    invalidate,
    load_session,
    proxy_base,
    secure_request,
)
from .schemas import (
    CreateSiteRequest,
    CreateSiteResponse,
    HostingSiteListResponse,
    HostingSiteSummary,
    PhpMyAdminSessionResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    SecretRotationResponse,
    SiteConfigResponse,
    SiteConfigUpdateRequest,
    SiteDatabaseResponse,
    SiteLogsResponse,
    WordPressUserListResponse,
)
from .service import HostingService
from .settings import get_hosting_settings

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


@router.get("/{site_id}/config", response_model=SiteConfigResponse)
def get_site_config(
    site: HostingSite = Depends(get_owned_site),
    _current_user: User = Depends(require_permission("hosting.sites.read")),
    service: HostingService = Depends(_get_hosting_service),
) -> SiteConfigResponse:
    return service.get_site_config(site)


@router.patch("/{site_id}/config", response_model=SiteConfigResponse)
def update_site_config(
    body: SiteConfigUpdateRequest,
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> SiteConfigResponse:
    result = service.update_site_config(site, tenant_context, body.env)
    db.commit()
    return result


@router.post("/{site_id}/database", response_model=SiteDatabaseResponse)
def site_database(
    response: Response,
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> SiteDatabaseResponse:
    """Reveal the site's own database credential and the phpMyAdmin link."""
    # Owned-site/RBAC dependencies have already run. Release their read
    # transaction before the Docker inspections in site_database().
    db.commit()
    result = service.site_database(site, tenant_context)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post("/{site_id}/phpmyadmin/session", response_model=PhpMyAdminSessionResponse)
def create_phpmyadmin_session(
    response: Response,
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> PhpMyAdminSessionResponse:
    user_id = current_user.id
    site_id = site.id
    # The dependencies checked ownership and permissions; their read
    # transaction must not hold a pool connection during Docker or HTTP login.
    db.commit()
    settings = get_hosting_settings()
    client = PmaClient(validate_url(settings.phpmyadmin_internal_url), settings.phpmyadmin_timeout)
    cookies = service.phpmyadmin_login_credentials(site, tenant_context, client)
    db.commit()
    _, ticket = create_session(
        user_id,
        tenant_context.current_tenant_id,
        site_id,
        cookies,
        settings.phpmyadmin_session_ttl,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return PhpMyAdminSessionResponse(
        url=f"{proxy_base(site_id)}/?ticket={ticket}", expires_in=settings.phpmyadmin_session_ttl
    )


@router.delete("/{site_id}/phpmyadmin/session", status_code=204)
def close_phpmyadmin_session(
    request: Request,
    response: Response,
    site: HostingSite = Depends(get_owned_site),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
) -> None:
    session = load_session(request.cookies.get(COOKIE))
    if session and session.site_id == site.id:
        invalidate(session)
    response.delete_cookie(COOKIE, path=proxy_base(site.id))
    response.headers["Cache-Control"] = "no-store"


@router.api_route(
    "/{site_id}/phpmyadmin", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
)
@router.api_route(
    "/{site_id}/phpmyadmin/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def phpmyadmin_proxy(
    request: Request,
    site_id: str,
    path: str = "",
) -> Response:
    base = proxy_base(site_id)
    try:
        session: PmaSession = await run_in_threadpool(authorize_proxy_session, request, site_id)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return HTMLResponse(
            "La sesión de phpMyAdmin expiró; volvé a abrir phpMyAdmin",
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    if getattr(request.state, "pma_ticket_exchange", False):
        redirect = RedirectResponse(base + "/", status_code=303)
        redirect.set_cookie(
            COOKIE,
            session.id,
            path=base,
            httponly=True,
            samesite="strict",
            secure=secure_request(request),
            max_age=max(1, int(session.expires_at - time.time())),
        )
        redirect.headers["Referrer-Policy"] = "no-referrer"
        redirect.headers["Cache-Control"] = "no-store"
        return redirect
    body = await request.body()
    if request.method == "POST":
        content_type = request.headers.get("content-type", "").lower()
        forbidden = False
        if content_type.startswith("application/x-www-form-urlencoded"):
            fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            forbidden = "pma_username" in fields or "pma_password" in fields
        elif content_type.startswith("multipart/form-data"):
            forbidden = bool(
                re.search(
                    rb'content-disposition:[^\r\n]*\bname="pma_(?:username|password)"', body, re.I
                )
            )
        if forbidden:
            raise AppError(
                "Login deshabilitado", status_code=403, code="hosting_phpmyadmin_login_disabled"
            )
    settings = get_hosting_settings()
    client = PmaClient(validate_url(settings.phpmyadmin_internal_url), settings.phpmyadmin_timeout)
    excluded = {
        "host",
        "cookie",
        "authorization",
        "connection",
        "content-length",
        "accept-encoding",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-for",
    }
    headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded}
    result, cookies, expired = await run_in_threadpool(
        proxy_response,
        client,
        request.method,
        path,
        request.url.query,
        body,
        headers,
        session.cookies,
        settings.phpmyadmin_url,
        base,
    )
    if expired:
        invalidate(session)
        result.delete_cookie(COOKIE, path=base)
    else:
        session.cookies = cookies
        session.save()
    result.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{site_id}/secrets/salts", response_model=SecretRotationResponse)
def rotate_site_salts(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.manage")),
    service: HostingService = Depends(_get_hosting_service),
    db: Session = Depends(get_db_session),
) -> SecretRotationResponse:
    result = service.rotate_site_salts(site, tenant_context)
    db.commit()
    return result


@router.delete("/{site_id}", status_code=204)
def delete_site(
    site: HostingSite = Depends(get_owned_site),
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission("hosting.sites.delete", platform_only=True)),
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
