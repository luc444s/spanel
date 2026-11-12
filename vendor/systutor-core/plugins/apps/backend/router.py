# ruff: noqa: E501
"""Thin, tenant-aware Apps HTTP API. Runtime I/O is worker-only."""

from __future__ import annotations

import os
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
from systutor.kernel.tenants.models import Tenant

from . import catalog
from .managed_environment import (
    MANAGED_DATABASE_NAMES,
    managed_environment_values,
    merge_managed_environment,
)
from .manifest_flow import ManifestFlow
from .models import Application, Deployment, Operation
from .operations import create_operation, dispatch_operation, write_pending_env
from .schemas import (
    ActionRequest,
    ApplicationCreate,
    ApplicationView,
    DeleteRequest,
    DeployRequest,
    ManifestStartRequest,
    OperationDetail,
    OperationView,
    ProviderView,
    RepositoryView,
    SecretRevealRequest,
    SecretRevealResponse,
    SourceUpdate,
)
from .service import audit
from .states import StateUnavailable
from .tenant_environment import save_tenant_environment, saved_tenant_environment

router = APIRouter(tags=["apps"])


def _resources(request: Request):
    resources = getattr(request.app.state, "apps_resources", None)
    if resources is None:
        raise AppError("Apps is unavailable", status_code=503, code="apps.unavailable")
    return resources


def _production() -> bool:
    return os.environ.get("SYSTUTOR_ENV", "").strip().lower() == "production"


def _flow(request: Request, db: Session) -> ManifestFlow:
    resources = _resources(request)
    return ManifestFlow(
        db, settings=resources.settings, states=resources.states(),
        github=resources.github, cipher=resources.cipher(),
        dispatcher=resources.dispatcher, production=_production(),
    )


def _queued(operation: Operation) -> OperationView:
    return OperationView(
        operation_id=operation.id, application_id=operation.application_id,
        kind=operation.kind, status=operation.status,
        poll_url=f"/api/v1/plugins/apps/operations/{operation.id}",
    )


def _queue(
    request: Request, db: Session, app: Application, user: User, kind: str,
    key: str | None, payload: object, pending: list[tuple[str, str]] | None = None,
    *, service: str | None = None,
) -> OperationView:
    operation, created = create_operation(
        db, application=app, actor_user_id=user.id, kind=kind,
        idempotency_key=key or "", payload=payload,
        correlation_id=request.headers.get("X-Request-ID"),
        subject_type="app_service" if service else None,
        subject_id=f"{app.id}:{service}" if service else None,
    )
    if not created:
        return _queued(operation)
    pending_path: Path | None = None
    try:
        report: dict[str, object] = {}
        if kind == "delete" and isinstance(payload, dict):
            report["purge_data"] = bool(payload.get("purge_data"))
        if service:
            report["service"] = service
        if report:
            operation.report = report
            db.commit()
        if pending is not None:
            pending_path, names, fingerprint = write_pending_env(
                _resources(request).settings.state_root, operation.id, pending
            )
            operation.report = {
                "environment_names": names,
                "environment_fingerprint": fingerprint,
                "tenant_environment_names": sorted({
                    name for name, _value in pending
                    if name not in MANAGED_DATABASE_NAMES
                }),
            }
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


@router.get("/github/status")
def github_status(
    request: Request, db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> dict:
    """Readiness is DB-only and never requires decrypting or reading a secret."""

    return catalog.status(
        db, user.tenant_id, _resources(request).settings, production=_production()
    )


@router.post("/github/manifest/start")
def github_manifest_start(
    body: ManifestStartRequest, request: Request,
    db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> dict:
    try:
        return _flow(request, db).start(user, body)
    except StateUnavailable as exc:
        raise AppError(
            "GitHub state store unavailable", status_code=503,
            code="apps.state_unavailable",
        ) from exc


@router.get("/github/manifest/callback")
def github_manifest_callback(
    code: str, state: str, request: Request,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    target = _flow(request, db).conversion(code, state)
    return RedirectResponse(target, status_code=303)


@router.get("/github/install/callback")
def github_install_callback(
    state: str, request: Request, installation_id: str | None = None,
    setup_action: str | None = None, db: Session = Depends(get_db_session),
) -> RedirectResponse:
    if not installation_id:
        return RedirectResponse("/app/apps?github=error", status_code=303)
    target = _flow(request, db).installation(installation_id, state, setup_action)
    return RedirectResponse(target, status_code=303)


@router.get("/github/providers", response_model=list[ProviderView])
def github_providers(
    db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> list[dict]:
    return catalog.provider_views(db, user.tenant_id)


@router.get("/github/installations")
def github_installations(
    db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> list[dict]:
    return catalog.installation_views(db, user.tenant_id)


@router.get("/github/installations/{installation_id}/repositories", response_model=list[RepositoryView])
def github_repositories(
    installation_id: str, db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> list[dict]:
    return catalog.repository_views(db, user.tenant_id, installation_id)


@router.post("/github/installations/{installation_id}/sync", response_model=OperationView, status_code=202)
def github_sync(
    installation_id: str, request: Request,
    idempotency_key: str | None = Header(None),
    db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> OperationView:
    installation = catalog.get_installation(db, user.tenant_id, installation_id)
    operation, created = create_operation(
        db, application=None, actor_user_id=user.id, kind="github_sync",
        idempotency_key=idempotency_key or "", payload={},
        correlation_id=request.headers.get("X-Request-ID"),
        tenant_id=user.tenant_id, subject_type="installation",
        subject_id=installation.id,
    )
    if not created:
        return _queued(operation)
    audit(db, "github.sync.queued", tenant_id=user.tenant_id, actor=user.id,
          entity_id=installation.id, details={"operation_id": operation.id})
    db.commit()
    dispatch_operation(db, operation, _resources(request).dispatcher)
    return _queued(operation)


@router.delete("/github/providers/{provider_id}", status_code=204)
def github_provider_delete(
    provider_id: str, db: Session = Depends(get_db_session),
    user: User = Depends(require_permission("apps.github.connect")),
) -> Response:
    catalog.revoke_provider(db, user.tenant_id, provider_id, user.id)
    return Response(status_code=204)


@router.post("/applications", response_model=ApplicationView, status_code=201)
def create_application(
    body: ApplicationCreate, request: Request, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> Application:
    return catalog.create_application(db, context, user, body)


@router.get("/applications", response_model=list[ApplicationView])
def list_applications(
    scope: str = "tenant",
    db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> list[ApplicationView]:
    if scope not in ("tenant", "all"):
        raise AppError(
            "scope must be 'tenant' or 'all'", status_code=422, code="apps.invalid_scope"
        )
    if scope == "all" and not context.is_platform:
        raise AppError(
            "Only the platform administrator can view all tenants",
            status_code=403,
            code="apps.platform_scope_forbidden",
        )
    stmt = select(Application).where(Application.deleted_at.is_(None))
    if scope == "tenant":
        stmt = stmt.where(Application.tenant_id == context.current_tenant_id)
    applications = list(db.scalars(stmt.order_by(Application.name.asc())).all())
    tenant_names = dict(db.execute(select(Tenant.id, Tenant.name)).all())
    return [
        ApplicationView.model_validate(application).model_copy(update={
            "tenant_id": application.tenant_id,
            "tenant_name": tenant_names.get(application.tenant_id),
        })
        for application in applications
    ]


@router.get("/applications/{application_id}")
def get_application(
    application_id: str, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> dict:
    return catalog.application_detail(db, context.current_tenant_id, application_id)


@router.patch("/applications/{application_id}/source", response_model=ApplicationView)
def update_application_source(
    application_id: str, body: SourceUpdate, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> Application:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    return catalog.update_source(db, app, body, user.id)


@router.get("/applications/{application_id}/operations")
def list_operations(
    application_id: str, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> list[dict]:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    rows = db.scalars(select(Operation).where(
        Operation.application_id == app.id,
        Operation.tenant_id == context.current_tenant_id,
    ).order_by(Operation.created_at.desc())).all()
    return [_operation_dict(row) for row in rows]


@router.post("/applications/{application_id}/inspect", response_model=OperationView, status_code=202)
def inspect_application(
    application_id: str, request: Request, idempotency_key: str | None = Header(None),
    db: Session = Depends(get_db_session), context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.create")),
) -> OperationView:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    return _queue(request, db, app, user, "inspect", idempotency_key, {})


@router.post("/applications/{application_id}/deployments", response_model=OperationView, status_code=202)
def deploy_application(
    application_id: str, body: DeployRequest, request: Request,
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.deployments.create")),
) -> OperationView:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    cipher = _resources(request).cipher()
    tenant_environment = (
        body.environment
        or saved_tenant_environment(app, cipher)
    )
    environment = merge_managed_environment(db, app, cipher, tenant_environment)
    if body.environment:
        save_tenant_environment(db, app, cipher, body.environment)
    return _queue(request, db, app, user, "deploy", idempotency_key,
                  {"environment": tenant_environment}, environment)


@router.get("/applications/{application_id}/deployments")
def list_deployments(
    application_id: str, request: Request, db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("apps.applications.read")),
) -> list[dict]:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    rows = db.scalars(select(Deployment).where(
        Deployment.application_id == app.id,
    ).order_by(Deployment.created_at.desc())).all()
    return [catalog._deployment_view(row) for row in rows]


@router.post("/applications/{application_id}/actions/{action}", response_model=OperationView, status_code=202)
def application_action(
    application_id: str, action: str, request: Request,
    body: ActionRequest | None = Body(default=None),
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.manage")),
) -> OperationView:
    if action not in {"refresh", "logs", "start", "stop", "restart", "down"}:
        raise AppError("Unknown action", status_code=404, code="apps.action_not_found")
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    service = body.service if body is not None else None
    if service and action == "down":
        raise AppError(
            "down always affects the whole application",
            status_code=422,
            code="apps.service_scope_forbidden",
        )
    if service:
        deployment = (
            db.get(Deployment, app.active_deployment_id)
            if app.active_deployment_id else None
        )
        containers = (
            (deployment.manifest or {}).get("containers", {})
            if deployment is not None else {}
        )
        if service not in containers:
            raise AppError(
                "Service does not belong to the active deployment",
                status_code=422,
                code="apps.invalid_service",
            )
    payload = {"service": service} if service else {}
    return _queue(request, db, app, user, action, idempotency_key, payload, service=service)


@router.post(
    "/applications/{application_id}/secrets/{name}/reveal",
    response_model=SecretRevealResponse,
)
def reveal_managed_secret(
    application_id: str,
    name: str,
    body: SecretRevealRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.secrets.read")),
) -> SecretRevealResponse:
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    deployment = (
        db.get(Deployment, app.active_deployment_id)
        if app.active_deployment_id else None
    )
    manifest = deployment.manifest if deployment is not None else None
    if not isinstance(manifest, dict):
        raise AppError(
            "Managed secret not found", status_code=404, code="apps.secret_not_found"
        )
    environments = manifest.get("service_environment") or {}
    services = sorted(
        str(service)
        for service, names in environments.items()
        if isinstance(names, list) and name in names
    )
    if not services or name not in MANAGED_DATABASE_NAMES:
        raise AppError(
            "Managed secret not found", status_code=404, code="apps.secret_not_found"
        )
    values = managed_environment_values(app, _resources(request).cipher())
    if values is None or name not in values:
        raise AppError(
            "Managed secret not found", status_code=404, code="apps.secret_not_found"
        )
    audit(
        db, "apps.secret.revealed", tenant_id=app.tenant_id, actor=user.id,
        entity_id=app.id,
        details={"name": name, "services": services, "reason": body.reason},
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return SecretRevealResponse(name=name, value=values[name])


@router.delete("/applications/{application_id}", response_model=OperationView, status_code=202)
def delete_application(
    application_id: str, request: Request, body: DeleteRequest = Body(...),
    idempotency_key: str | None = Header(None), db: Session = Depends(get_db_session),
    context: TenantContext = Depends(get_current_tenant_context),
    user: User = Depends(require_permission("apps.applications.delete")),
) -> OperationView:
    if not context.is_platform:
        raise AppError("Platform permission required", status_code=403, code="apps.platform_required")
    app = catalog.get_application(db, context.current_tenant_id, application_id)
    if body.confirm != app.slug:
        raise AppError("Confirmation does not match", status_code=422, code="apps.confirmation_invalid")
    return _queue(request, db, app, user, "delete", idempotency_key,
                  {"confirm": body.confirm, "purge_data": body.purge_data})


def _operation_dict(row: Operation) -> dict:
    return {
        "id": row.id, "application_id": row.application_id,
        "subject_type": row.subject_type, "subject_id": row.subject_id,
        "kind": row.kind, "status": row.status, "phase": row.phase,
        "report": row.report, "result": row.result,
        "error_code": row.error_code, "public_error": row.public_error,
        "created_at": row.created_at, "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


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
