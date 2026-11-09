# ruff: noqa: E501
"""Thin, tenant-aware Apps HTTP API. Runtime I/O is worker-only."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

from .models import Application, Deployment, Operation
from .oauth import OAuthUnavailable
from .operations import create_operation, dispatch_operation, write_pending_env
from .schemas import (
    ApplicationCreate,
    ApplicationView,
    DeleteRequest,
    DeployRequest,
    OAuthStartRequest,
    OperationDetail,
    OperationView,
    ReconnectRequest,
)
from .service import AppsService, audit

router = APIRouter(tags=["apps"])


def _resources(request: Request):
    resources = getattr(request.app.state, "apps_resources", None)
    if resources is None:
        raise AppError("Apps is unavailable", status_code=503, code="apps.unavailable")
    return resources


def _service(request: Request, db: Session) -> AppsService:
    return AppsService(db, _resources(request).cipher())


def _queued(operation: Operation) -> OperationView:
    return OperationView(
        operation_id=operation.id, application_id=operation.application_id,
        kind=operation.kind, status=operation.status,
        poll_url=f"/api/v1/plugins/apps/operations/{operation.id}",
    )


def _queue(
    request: Request, db: Session, app: Application, user: User, kind: str,
    key: str | None, payload: object, pending: list[tuple[str, str]] | None = None,
) -> OperationView:
    operation, created = create_operation(
        db, application=app, actor_user_id=user.id, kind=kind,
        idempotency_key=key or "", payload=payload,
        correlation_id=request.headers.get("X-Request-ID"),
    )
    if not created:
        return _queued(operation)
    pending_path: Path | None = None
    try:
        if kind == "delete" and isinstance(payload, dict):
            operation.report = {"purge_data": bool(payload.get("purge_data"))}
            db.commit()
        if pending is not None:
            pending_path, names, fingerprint = write_pending_env(
                _resources(request).settings.state_root, operation.id, pending
            )
            operation.report = {"environment_names": names, "environment_fingerprint": fingerprint}
            db.commit()
        audit(db, "operation.queue", tenant_id=app.tenant_id, actor=user.id,
              entity_id=app.id, details={"operation_id": operation.id, "kind": kind})
        db.commit()
        dispatch_operation(db, operation, _resources(request).dispatcher)
    except Exception:
        if pending_path is not None:
            pending_path.unlink(missing_ok=True)
        raise
    return _queued(operation)


@router.get("/github/connection")
def github_connection(
    request: Request, db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> dict:
    row = _service(request, db).connection(user.id)
    if row is None:
        return {"connected": False, "reconnect_required": False}
    return {
        "connected": not row.reconnect_required, "reconnect_required": row.reconnect_required,
        "github_user_id": row.github_user_id, "login": row.login,
        "avatar_url": row.avatar_url, "scopes": row.scopes,
    }


@router.post("/github/oauth/start")
def github_oauth_start(
    body: OAuthStartRequest, request: Request,
    user: User = Depends(require_permission("apps.github.connect")),
) -> dict:
    try:
        start = _resources(request).oauth().start(user.id, body.redirect)
    except OAuthUnavailable as exc:
        raise AppError(
            "OAuth state store unavailable", status_code=503, code="apps.oauth_unavailable"
        ) from exc
    return {"authorization_url": start.authorization_url}


@router.get("/github/oauth/callback", include_in_schema=True)
def github_oauth_callback(code: str, state: str, request: Request,
                          db: Session = Depends(get_db_session)) -> RedirectResponse:
    try:
        user_id, tokens, identity = _resources(request).oauth().callback(code, state)
    except OAuthUnavailable as exc:
        raise AppError(
            "OAuth state store unavailable", status_code=503, code="apps.oauth_unavailable"
        ) from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError("OAuth user is unavailable", status_code=404, code="apps.oauth_user_invalid")
    _service(request, db).save_connection(user_id, tokens, identity, user.tenant_id)
    return RedirectResponse("/app/apps?github=connected", status_code=303)


@router.post("/github/disconnect", status_code=204)
def github_disconnect(
    request: Request, db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> Response:
    resources = _resources(request)
    try:
        secret = resources.settings.read_secret(resources.settings.github_client_secret_file)
    except OSError:
        secret = ""
    _service(request, db).disconnect(
        user, resources.github_http, resources.settings.github_client_id,
        secret,
    )
    return Response(status_code=204)


@router.post("/applications", response_model=ApplicationView, status_code=201)
def create_application(
    body: ApplicationCreate, request: Request, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> Application:
    return _service(request, db).create_application(context, user, body)


@router.get("/applications", response_model=list[ApplicationView])
def list_applications(
    db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> list[Application]:
    return list(db.scalars(select(Application).where(
        Application.tenant_id == context.current_tenant_id, Application.deleted_at.is_(None),
    )).all())


@router.get("/applications/{application_id}", response_model=ApplicationView)
def get_application(
    application_id: str, request: Request, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> Application:
    return _service(request, db).get_application(context.current_tenant_id, application_id)


@router.post("/applications/{application_id}/source/reconnect", status_code=204)
def reconnect_source(
    application_id: str, body: ReconnectRequest, request: Request,
    db: Session = Depends(get_db_session), context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> Response:
    service = _service(request, db)
    service.reconnect(service.get_application(context.current_tenant_id, application_id),
                      body.source_connection_user_id, user.id)
    return Response(status_code=204)


@router.post("/applications/{application_id}/inspect", response_model=OperationView, status_code=202)
def inspect_application(
    application_id: str, request: Request, idempotency_key: str | None = Header(None),
    db: Session = Depends(get_db_session), context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> OperationView:
    app = _service(request, db).get_application(context.current_tenant_id, application_id)
    return _queue(request, db, app, user, "inspect", idempotency_key, {})


@router.post("/applications/{application_id}/deployments", response_model=OperationView, status_code=202)
def deploy_application(
    application_id: str, body: DeployRequest, request: Request,
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.deployments.create")),
) -> OperationView:
    if not context.is_platform:
        raise AppError("Platform permission required", status_code=403, code="apps.platform_required")
    app = _service(request, db).get_application(context.current_tenant_id, application_id)
    return _queue(request, db, app, user, "deploy", idempotency_key,
                  {"environment": body.environment}, body.environment)


@router.get("/applications/{application_id}/deployments")
def list_deployments(
    application_id: str, request: Request, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> list[dict]:
    app = _service(request, db).get_application(context.current_tenant_id, application_id)
    rows = db.scalars(select(Deployment).where(Deployment.application_id == app.id)).all()
    return [{"id": row.id, "status": row.status, "commit": row.commit,
             "tree_hash": row.tree_hash, "public_error": row.public_error} for row in rows]


@router.post("/applications/{application_id}/actions/{action}", response_model=OperationView, status_code=202)
def application_action(
    application_id: str, action: str, request: Request,
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.manage")),
) -> OperationView:
    if action not in {"refresh", "logs", "start", "stop", "restart", "down"}:
        raise AppError("Unknown action", status_code=404, code="apps.action_not_found")
    app = _service(request, db).get_application(context.current_tenant_id, application_id)
    return _queue(request, db, app, user, action, idempotency_key, {})


@router.delete("/applications/{application_id}", response_model=OperationView, status_code=202)
def delete_application(
    application_id: str, request: Request, body: DeleteRequest = Body(...),
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.delete")),
) -> OperationView:
    if not context.is_platform:
        raise AppError("Platform permission required", status_code=403, code="apps.platform_required")
    app = _service(request, db).get_application(context.current_tenant_id, application_id)
    if body.confirm != app.slug:
        raise AppError("Confirmation does not match", status_code=422, code="apps.confirmation_invalid")
    return _queue(request, db, app, user, "delete", idempotency_key,
                  {"confirm": body.confirm, "purge_data": body.purge_data})


@router.get("/operations/{operation_id}", response_model=OperationDetail)
def get_operation(
    operation_id: str, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> Operation:
    operation = db.scalar(select(Operation).where(
        Operation.id == operation_id, Operation.tenant_id == context.current_tenant_id,
    ))
    if operation is None:
        raise AppError("Operation not found", status_code=404, code="apps.operation_not_found")
    return operation
