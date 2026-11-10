"""Tenant-scoped GitHub App catalog and application source selection."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from systutor.core.errors import AppError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

from .managed_environment import MANAGED_DATABASE_NAMES
from .models import (
    Application,
    Deployment,
    GithubInstallation,
    GithubProvider,
    GithubRepository,
    Operation,
)
from .operations import ACTIVE
from .runtime.types import SECRET_NAME_RE
from .schemas import ApplicationCreate, SourceUpdate
from .settings import AppsSettings

ENTITY = "github_provider"


def audit(
    db: Session,
    action: str,
    *,
    tenant_id: str,
    actor: str | None,
    entity_id: str | None,
    result: str = "success",
    details: dict | None = None,
    entity_type: str = ENTITY,
) -> None:
    record_audit(
        db, tenant_id=tenant_id, branch_id=None, actor_user_id=actor,
        actor_type="user" if actor else "system", module="apps", action=action,
        entity_type=entity_type, entity_id=entity_id, result=result,
        correlation_id=None, request_id=None, details=details or {},
    )


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
    if not result:
        raise AppError("Invalid application name", status_code=422, code="apps.invalid_name")
    return result


def get_provider(db: Session, tenant_id: str, provider_id: str) -> GithubProvider:
    provider = db.scalar(select(GithubProvider).where(
        GithubProvider.id == provider_id, GithubProvider.tenant_id == tenant_id,
    ))
    if provider is None:
        raise AppError("GitHub provider not found", status_code=404, code="apps.provider_not_found")
    return provider


def get_installation(db: Session, tenant_id: str, installation_id: str) -> GithubInstallation:
    installation = db.scalar(select(GithubInstallation).where(
        GithubInstallation.id == installation_id,
        GithubInstallation.tenant_id == tenant_id,
    ))
    if installation is None:
        raise AppError(
            "GitHub installation not found", status_code=404,
            code="apps.installation_not_found",
        )
    return installation


def require_source(
    db: Session, app: Application
) -> tuple[GithubInstallation, GithubRepository, GithubProvider]:
    """Resolve the authorized source from the server-side catalog."""

    if not app.source_installation_id or not app.source_repository_id:
        raise AppError(
            "Application source requires reconnection", status_code=409,
            code="apps.source_reconnect_required",
        )
    installation = db.get(GithubInstallation, app.source_installation_id)
    repository = db.get(GithubRepository, app.source_repository_id)
    if (
        installation is None or repository is None
        or installation.tenant_id != app.tenant_id
        or repository.tenant_id != app.tenant_id
        or repository.installation_id != installation.id
    ):
        raise AppError(
            "Application source requires reconnection", status_code=409,
            code="apps.source_reconnect_required",
        )
    provider = db.get(GithubProvider, installation.provider_id)
    if (
        provider is None or provider.tenant_id != app.tenant_id
        or provider.status != "active" or installation.status != "active"
        or repository.removed_at is not None
    ):
        raise AppError(
            "Application source requires reconnection", status_code=409,
            code="apps.source_reconnect_required",
        )
    if app.source_status != "connected":
        raise AppError(
            "Application source requires reconnection", status_code=409,
            code="apps.source_reconnect_required",
        )
    return installation, repository, provider


def select_source(
    db: Session, tenant_id: str, installation_id: str, repository_id: str
) -> tuple[GithubInstallation, GithubRepository, GithubProvider]:
    installation = get_installation(db, tenant_id, installation_id)
    repository = db.scalar(select(GithubRepository).where(
        GithubRepository.id == repository_id,
        GithubRepository.tenant_id == tenant_id,
        GithubRepository.installation_id == installation.id,
        GithubRepository.removed_at.is_(None),
    ))
    if repository is None:
        raise AppError(
            "Repository is not authorized by this installation", status_code=422,
            code="apps.repository_unauthorized",
        )
    provider = db.get(GithubProvider, installation.provider_id)
    if (
        provider is None or provider.tenant_id != tenant_id
        or provider.status != "active" or installation.status != "active"
        or repository.disabled
    ):
        raise AppError(
            "GitHub source is not active", status_code=409,
            code="apps.source_inactive",
        )
    return installation, repository, provider


def create_application(
    db: Session, context: TenantContext, user: User, body: ApplicationCreate
) -> Application:
    installation, repository, _provider = select_source(
        db, context.current_tenant_id, body.installation_id, body.repository_id
    )
    slug = _slug(body.name)
    if db.scalar(select(Application.id).where(
        Application.tenant_id == context.current_tenant_id, Application.slug == slug,
    )):
        raise AppError(
            "Application slug already exists", status_code=409, code="apps.slug_conflict"
        )
    app = Application(
        tenant_id=context.current_tenant_id, name=body.name, slug=slug,
        repository=repository.full_name,
        branch=body.branch or repository.default_branch or "main",
        compose_file=body.compose_file,
        source_connection_user_id=user.id,
        source_installation_id=installation.id,
        source_repository_id=repository.id,
        source_status="connected",
        runtime_project=f"app-{uuid4().hex[:24]}",
        # Ingress belongs to Dominios. Historic input columns stay readable
        # for existing rows, but new applications never publish an automatic
        # second route from Apps.
        domain=None,
        ingress_service=None,
        ingress_port=None,
    )
    db.add(app)
    db.flush()
    audit(
        db, "application.create", tenant_id=app.tenant_id, actor=user.id,
        entity_id=app.id, entity_type="application",
        details={
            "installation_id": installation.id,
            "repository_id": repository.id,
            "repository": repository.full_name,
        },
    )
    db.commit()
    db.refresh(app)
    return app


def update_source(
    db: Session, app: Application, body: SourceUpdate, actor_user_id: str
) -> Application:
    if app.active_operation_id is not None:
        raise AppError(
            "Application has an active operation", status_code=409,
            code="apps.operation_active",
        )
    installation, repository, _provider = select_source(
        db, app.tenant_id, body.installation_id, body.repository_id
    )
    app.source_installation_id = installation.id
    app.source_repository_id = repository.id
    app.repository = repository.full_name
    app.branch = body.branch or repository.default_branch or "main"
    app.source_status = "connected"
    audit(
        db, "application.source_selected", tenant_id=app.tenant_id,
        actor=actor_user_id, entity_id=app.id, entity_type="application",
        details={
            "installation_id": installation.id,
            "repository_id": repository.id,
            "repository": repository.full_name,
        },
    )
    db.commit()
    db.refresh(app)
    return app


def get_application(db: Session, tenant_id: str, application_id: str) -> Application:
    row = db.scalar(select(Application).where(
        Application.id == application_id, Application.tenant_id == tenant_id,
        Application.deleted_at.is_(None),
    ))
    if row is None:
        raise AppError("Application not found", status_code=404, code="apps.not_found")
    return row


def provider_views(db: Session, tenant_id: str) -> list[dict]:
    rows = db.scalars(select(GithubProvider).where(
        GithubProvider.tenant_id == tenant_id,
    ).order_by(GithubProvider.created_at)).all()
    return [_provider_view(row) for row in rows]


def _provider_view(row: GithubProvider) -> dict:
    return {
        "id": row.id,
        "github_app_id": row.github_app_id,
        "app_slug": row.app_slug,
        "name": row.name,
        "owner_login": row.owner_login,
        "owner_type": row.owner_type,
        "status": row.status,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
    }


def installation_views(db: Session, tenant_id: str) -> list[dict]:
    rows = db.scalars(select(GithubInstallation).where(
        GithubInstallation.tenant_id == tenant_id,
    ).order_by(GithubInstallation.installed_at)).all()
    counts = dict(db.execute(
        select(GithubRepository.installation_id, func.count())
        .where(GithubRepository.tenant_id == tenant_id, GithubRepository.removed_at.is_(None))
        .group_by(GithubRepository.installation_id)
    ).all())
    result = []
    for row in rows:
        item = _installation_view(row)
        item["repository_count"] = int(counts.get(row.id, 0))
        result.append(item)
    return result


def _installation_view(row: GithubInstallation) -> dict:
    return {
        "id": row.id,
        "provider_id": row.provider_id,
        "github_installation_id": row.github_installation_id,
        "account_login": row.account_login,
        "account_type": row.account_type,
        "repository_selection": row.repository_selection,
        "permissions": row.permissions or {},
        "status": row.status,
        "installed_at": row.installed_at,
        "last_synced_at": row.last_synced_at,
        "suspended_at": row.suspended_at,
        "deleted_at": row.deleted_at,
    }


def repository_views(db: Session, tenant_id: str, installation_id: str) -> list[dict]:
    get_installation(db, tenant_id, installation_id)
    rows = db.scalars(select(GithubRepository).where(
        GithubRepository.tenant_id == tenant_id,
        GithubRepository.installation_id == installation_id,
        GithubRepository.removed_at.is_(None),
    ).order_by(GithubRepository.full_name)).all()
    return [{
        "id": row.id,
        "github_repository_id": row.github_repository_id,
        "full_name": row.full_name,
        "private": bool(row.private),
        "default_branch": row.default_branch,
        "archived": bool(row.archived),
        "disabled": bool(row.disabled),
        "observed_at": row.observed_at,
        "removed_at": row.removed_at,
    } for row in rows]


def status(db: Session, tenant_id: str, settings: AppsSettings, production: bool) -> dict:
    from .public_url import PublicUrlError, install_url, parse_public_url
    from .readiness import secret_available

    try:
        parse_public_url(settings.public_url, production=production)
        public_ok = True
    except PublicUrlError:
        public_ok = False
    configured = public_ok and secret_available(settings.encryption_key_file)
    try:
        providers = db.scalars(select(GithubProvider).where(
            GithubProvider.tenant_id == tenant_id,
        )).all()
        installations = db.scalars(select(GithubInstallation).where(
            GithubInstallation.tenant_id == tenant_id,
        )).all()
        syncing = db.scalar(select(Operation.id).where(
            Operation.tenant_id == tenant_id,
            Operation.kind == "github_sync",
            Operation.status.in_(ACTIVE),
        )) is not None
    except SQLAlchemyError:
        # A missing/unavailable catalog table is an explicit readiness failure,
        # not an HTTP 500 and never a false "disconnected" state.
        return {
            "state": "error",
            "configured": configured,
            "connected": False,
            "reconnect_required": False,
            "login": None,
            "provider_count": 0,
            "installation_count": 0,
            "syncing": False,
        }
    active = [row for row in installations if row.status == "active"]
    pending = any(row.status == "pending_installation" for row in providers)
    suspended = any(row.status == "suspended" for row in installations)
    if not configured:
        state = "error"
    elif syncing:
        state = "syncing"
    elif active:
        state = "connected"
    elif suspended:
        state = "suspended"
    elif providers or pending:
        state = "pending_installation"
    else:
        state = "unconfigured"
    account = active[0].account_login if active else None
    pending_provider = next(
        (row for row in providers if row.status == "pending_installation"), None
    )
    pending_install_url = None
    if pending_provider is not None:
        try:
            pending_install_url = install_url(pending_provider.app_slug)
        except PublicUrlError:
            pending_install_url = None
    return {
        "state": state,
        "configured": configured,
        "connected": bool(active),
        "reconnect_required": any(
            not row.status == "active" for row in installations
        ),
        "login": account,
        "provider_count": len(providers),
        "installation_count": len(installations),
        "syncing": syncing,
        "install_url": pending_install_url,
    }


def application_detail(db: Session, tenant_id: str, application_id: str) -> dict:
    app = get_application(db, tenant_id, application_id)
    deployments = db.scalars(select(Deployment).where(
        Deployment.application_id == app.id,
    ).order_by(Deployment.created_at.desc())).all()
    active = db.get(Deployment, app.active_deployment_id) if app.active_deployment_id else None
    latest_inspect = db.scalar(select(Operation).where(
        Operation.application_id == app.id,
        Operation.kind == "inspect",
        Operation.status == "succeeded",
    ).order_by(Operation.finished_at.desc()))
    report = (latest_inspect.result or latest_inspect.report or {}) if latest_inspect else {}
    services = (
        sorted((active.manifest or {}).get("containers", {}).keys())
        if active else sorted(report.get("services", [])) if isinstance(report, dict) else []
    )
    return {
        "id": app.id, "name": app.name, "slug": app.slug,
        "repository": app.repository, "branch": app.branch,
        "compose_file": app.compose_file, "domain": app.domain,
        "ingress_service": app.ingress_service, "ingress_port": app.ingress_port,
        "status": app.status,
        "source_installation_id": app.source_installation_id,
        "source_repository_id": app.source_repository_id,
        "source_status": app.source_status,
        "runtime_project": app.runtime_project,
        "active_deployment": _deployment_view(active) if active else None,
        "services": services,
        "compose_candidates": (
            report.get("compose_candidates", []) if isinstance(report, dict) else []
        ),
        "findings": report.get("findings", []) if isinstance(report, dict) else [],
        "deployments": [_deployment_view(row) for row in deployments],
    }


def _environment_metadata(manifest: dict, service: str) -> list[dict]:
    """Non-secret variable metadata; values are never stored here."""
    environments = manifest.get("service_environment") or {}
    names = environments.get(service) or []
    if not isinstance(names, list):
        return []
    tenant_names = set(manifest.get("tenant_environment_names") or [])
    result: list[dict] = []
    for raw in names:
        name = str(raw)
        if name in MANAGED_DATABASE_NAMES:
            source, revealable = "managed", True
        elif name in tenant_names:
            source, revealable = "tenant", False
        else:
            source, revealable = "image", False
        result.append({
            "name": name,
            "source": source,
            "secret": bool(SECRET_NAME_RE.search(name)),
            "revealable": revealable,
        })
    return result


def _deployment_view(row: Deployment) -> dict:
    manifest = row.manifest or {}
    services = sorted(manifest.get("containers", {}).keys())
    images = manifest.get("images", {})
    environments = manifest.get("service_environment", {})
    ports = manifest.get("service_ports", {})
    runtime = manifest.get("service_runtime", {})
    details = []
    for service in services:
        state = runtime.get(service) if isinstance(runtime, dict) else {}
        state = state if isinstance(state, dict) else {}
        health = state.get("health")
        names = environments.get(service, [])
        details.append({
            "name": service,
            "image": images.get(service),
            "status": state.get("status") or row.status,
            "health": "unknown" if health in (None, "none") else health,
            "ports": ports.get(service) or [],
            "restart_count": state.get("restart_count") or 0,
            "environment_names": names,
            "environment": _environment_metadata(manifest, service),
            "created_at": state.get("created_at") or row.created_at,
        })
    return {
        "id": row.id, "status": row.status, "commit": row.commit,
        "tree_hash": row.tree_hash, "compose_file": row.compose_file,
        "ingress_service": row.ingress_service, "ingress_port": row.ingress_port,
        "release": row.release, "public_error": row.public_error,
        "services": services,
        "service_details": details,
        "created_at": row.created_at, "finished_at": row.finished_at,
    }


def revoke_provider(
    db: Session, tenant_id: str, provider_id: str, actor_user_id: str
) -> None:
    provider = get_provider(db, tenant_id, provider_id)
    now = datetime.now(UTC)
    provider.status = "revoked"
    provider.revoked_at = now
    installations = db.scalars(select(GithubInstallation).where(
        GithubInstallation.provider_id == provider.id,
        GithubInstallation.tenant_id == tenant_id,
    )).all()
    installation_ids = [row.id for row in installations]
    for row in installations:
        row.status = "deleted"
        row.deleted_at = now
    if installation_ids:
        apps = db.scalars(select(Application).where(
            Application.tenant_id == tenant_id,
            Application.source_installation_id.in_(installation_ids),
            Application.deleted_at.is_(None),
        )).all()
        for app in apps:
            app.source_status = "source_reconnect_required"
    audit(
        db, "github.provider.revoked", tenant_id=tenant_id, actor=actor_user_id,
        entity_id=provider.id,
        details={"github_app_id": provider.github_app_id, "installations": len(installations)},
    )
    db.commit()


def mark_installation_suspended(
    db: Session, installation: GithubInstallation, status: str
) -> None:
    now = datetime.now(UTC)
    installation.status = status
    if status == "suspended":
        installation.suspended_at = now
    if status in {"deleted", "revoked"}:
        installation.deleted_at = now
    apps = db.scalars(select(Application).where(
        Application.tenant_id == installation.tenant_id,
        Application.source_installation_id == installation.id,
        Application.deleted_at.is_(None),
    )).all()
    for app in apps:
        app.source_status = "source_reconnect_required"
