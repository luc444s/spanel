"""Thin public/tenant HTTP surface for Cloudflare OAuth and discovery."""

from __future__ import annotations

import logging
from typing import Any

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
    OAuthClientRegistrationRequest,
    OAuthClientStatusResponse,
    OAuthStartResponse,
)
from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(tags=["domains-cloudflare"])

CLOUDFLARE = "domains.dns.cloudflare"


class _OAuthCallbackAccessLogFilter(logging.Filter):
    """Strip callback query data (authorization code/state) from Uvicorn logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args: Any = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2])
            if "/dns/cloudflare/oauth/callback?" in path:
                cleaned = path.split("?", 1)[0] + "?<redacted>"
                record.args = (*args[:2], cleaned, *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_OAuthCallbackAccessLogFilter())


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


@router.get("/dns/cloudflare/oauth/client", response_model=OAuthClientStatusResponse)
def oauth_client_status(
    request: Request,
    _current_user: User = Depends(require_permission(CLOUDFLARE, platform_only=True)),
    db: Session = Depends(get_db_session),
) -> OAuthClientStatusResponse:
    return OAuthClientStatusResponse.model_validate(_service(request, db).client_status())


@router.post("/dns/cloudflare/oauth/register", response_model=OAuthClientStatusResponse)
def register_oauth_client(
    request: Request,
    body: OAuthClientRegistrationRequest | None = None,
    _current_user: User = Depends(require_permission(CLOUDFLARE, platform_only=True)),
    db: Session = Depends(get_db_session),
) -> OAuthClientStatusResponse:
    payload = body or OAuthClientRegistrationRequest()
    token = (payload.bootstrap_token or "").strip()
    if not token or len(token) > 4096:
        # Fixed message: never echo the submitted token.
        raise AppError(
            "a Cloudflare bootstrap token is required",
            status_code=422,
            code="domains.dns.cloudflare.validation",
        )
    service = _service(request, db)
    try:
        result = service.register(
            account_id=(payload.account_id or "").strip(),
            bootstrap_token=token,
            redirect_uri=service.config.redirect_uri,
            apply=True,
        )
    except CloudflareError as exc:
        if str(exc) in {
            "the token cannot list accounts; provide the Cloudflare account id",
            "the token has no accessible Cloudflare account",
            "the token can access multiple accounts; provide the Cloudflare account id",
        }:
            raise AppError(
                "enter the Cloudflare Account ID shown in the account dashboard",
                status_code=422,
                code="domains.dns.cloudflare.account_id_required",
            ) from exc
        if exc.status_code == 404:
            raise AppError(
                "Cloudflare could not find that Account ID or OAuth client resource",
                status_code=422,
                code="domains.dns.cloudflare.account_not_found",
            ) from exc
        raise _translate(exc) from exc
    raw_scopes = result.get("scopes")
    scopes = [str(name) for name in raw_scopes] if isinstance(raw_scopes, list) else []
    return OAuthClientStatusResponse(
        registered=True,
        account_id=str(result["account_id"]),
        client_id=str(result["client_id"]),
        redirect_uri=str(result["redirect_uri"]),
        scopes=scopes,
    )


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
