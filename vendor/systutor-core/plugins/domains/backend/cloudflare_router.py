"""Thin public/tenant HTTP surface for Cloudflare OAuth and discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from plugins.domains.backend.cloudflare.client import (
    CloudflareClient,
    CloudflareError,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig, CloudflareConfigError
from plugins.domains.backend.cloudflare.service import CloudflareService
from plugins.domains.backend.cloudflare_schemas import (
    CloudflareRecordItem,
    CloudflareRecordListResponse,
    CloudflareZoneItem,
    CloudflareZoneListResponse,
    ConnectionStatusResponse,
    DisconnectRequest,
    DisconnectResponse,
    OAuthStartResponse,
)
from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(tags=["domains-cloudflare"])

CLOUDFLARE = "domains.dns.cloudflare"


def _config() -> CloudflareConfig:
    try:
        return CloudflareConfig.from_env()
    except CloudflareConfigError as exc:
        raise AppError(str(exc), status_code=422, code="domains.dns.cloudflare.validation") from exc


def _client(request: Request) -> CloudflareClient:
    existing = getattr(request.app.state, "cloudflare_client", None)
    if existing is not None:
        return existing
    client = CloudflareClient(_config())
    request.app.state.cloudflare_client = client
    return client


def _service(request: Request, db: Session) -> CloudflareService:
    config = _config()
    cipher = getattr(request.app.state, "cloudflare_cipher", None)
    return CloudflareService(db, request.app, _client(request), config, cipher=cipher)


def _translate(exc: CloudflareError) -> AppError:
    return AppError(str(exc), status_code=exc.status_code, code=exc.code)


@router.post("/dns/cloudflare/oauth/start", response_model=OAuthStartResponse)
def start_oauth(
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(CLOUDFLARE)),
    db: Session = Depends(get_db_session),
) -> OAuthStartResponse:
    try:
        payload = _service(request, db).start(tenant_context)
    except CloudflareError as exc:
        raise _translate(exc) from exc
    return OAuthStartResponse.model_validate(payload)


@router.get("/dns/cloudflare/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    request: Request,
    state: str = Query(default="", max_length=512),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=128),
    redirect_uri: str | None = Query(default=None, max_length=1000),
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    # Public route: no session and no tenant query parameter. The opaque,
    # hashed, single-use state stored in the flow is the only authorization.
    result = _service(request, db).complete_callback(
        state=state,
        code=code,
        error=error,
        redirect_uri=redirect_uri,
    )
    return HTMLResponse(result.html, status_code=200 if result.success else 400)


@router.get("/dns/cloudflare/status", response_model=ConnectionStatusResponse)
def connection_status(
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(CLOUDFLARE)),
    db: Session = Depends(get_db_session),
) -> ConnectionStatusResponse:
    try:
        payload = _service(request, db).status(tenant_context)
    except CloudflareError as exc:
        raise _translate(exc) from exc
    return ConnectionStatusResponse.model_validate(payload)


@router.post("/dns/cloudflare/disconnect", response_model=DisconnectResponse)
def disconnect(
    request: Request,
    body: DisconnectRequest | None = None,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(CLOUDFLARE)),
    db: Session = Depends(get_db_session),
) -> DisconnectResponse:
    apply = True if body is None else body.apply
    try:
        payload = _service(request, db).disconnect(tenant_context, apply=apply)
    except CloudflareError as exc:
        raise _translate(exc) from exc
    return DisconnectResponse.model_validate(payload)


@router.get("/dns/cloudflare/zones", response_model=CloudflareZoneListResponse)
def list_zones(
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(CLOUDFLARE)),
    db: Session = Depends(get_db_session),
) -> CloudflareZoneListResponse:
    try:
        items = _service(request, db).zones(tenant_context)
    except CloudflareError as exc:
        raise _translate(exc) from exc
    return CloudflareZoneListResponse(
        items=[CloudflareZoneItem.model_validate(item) for item in items]
    )


@router.get(
    "/dns/cloudflare/zones/{zone_id}/records",
    response_model=CloudflareRecordListResponse,
)
def list_records(
    zone_id: str,
    request: Request,
    tenant_context: TenantContext = Depends(get_current_tenant_context),
    _current_user: User = Depends(require_permission(CLOUDFLARE)),
    db: Session = Depends(get_db_session),
) -> CloudflareRecordListResponse:
    try:
        items = _service(request, db).records(tenant_context, zone_id)
    except CloudflareError as exc:
        raise _translate(exc) from exc
    return CloudflareRecordListResponse(
        zone_id=zone_id,
        items=[CloudflareRecordItem.model_validate(item) for item in items],
    )
