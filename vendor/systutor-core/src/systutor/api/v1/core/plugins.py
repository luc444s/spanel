from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.api.v1.core.common import (
    PluginRuntimeRead,
    audit_core_action,
    build_action_context,
    tenant_not_found,
)
from systutor.api.v1.core.schemas import CorePluginMigrateRequest
from systutor.api.v1.core.services.plugins import (
    get_core_plugin,
    install_core_plugin,
    list_core_plugins,
    migrate_core_plugin,
    set_core_plugin_enabled,
    uninstall_core_plugin,
)
from systutor.core.lifecycle import bootstrap_app_state
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    require_any_permission,
    require_permission,
)
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/core/plugins", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_PLUGIN_MANAGE = Depends(require_permission("core.plugin.manage"))
REQUIRE_PLUGIN_RUNTIME_READ = Depends(
    require_any_permission("core.plugin.runtime.read", "core.plugin.manage")
)
MIGRATE_BODY = Body(default=None)


def _refresh_plugin_runtime(request: Request, db: Session) -> None:
    bootstrap_app_state(request.app, request.app.state.settings)
    db.expire_all()


@router.get("", response_model=list[PluginRuntimeRead])
def list_plugins(
    db: Session = DB_SESSION,
    _: User = REQUIRE_PLUGIN_RUNTIME_READ,
) -> list[PluginRuntimeRead]:
    return [PluginRuntimeRead.model_validate(record) for record in list_core_plugins(db)]


@router.get("/{plugin_id}", response_model=PluginRuntimeRead)
def get_plugin(
    plugin_id: str,
    db: Session = DB_SESSION,
    _: User = REQUIRE_PLUGIN_RUNTIME_READ,
) -> PluginRuntimeRead:
    record = get_core_plugin(db, plugin_id=plugin_id)
    if record is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(record)


@router.post("/{plugin_id}/install", response_model=PluginRuntimeRead)
def install_plugin(
    plugin_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRuntimeRead:
    action_context = build_action_context(request, tenant_context)
    try:
        record = install_core_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            action_context=action_context,
        )
        audit_core_action(
            db,
            context=action_context,
            action="plugin.install",
            entity_type="plugin",
            entity_id=plugin_id,
            details={"migration_version": record.migration_version},
        )
        db.commit()
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _refresh_plugin_runtime(request, db)
    refreshed = get_core_plugin(db, plugin_id=plugin_id)
    if refreshed is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(refreshed)


@router.post("/{plugin_id}/enable", response_model=PluginRuntimeRead)
def enable_plugin(
    plugin_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRuntimeRead:
    action_context = build_action_context(request, tenant_context)
    try:
        record = set_core_plugin_enabled(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            is_enabled=True,
            action_context=action_context,
        )
        audit_core_action(
            db,
            context=action_context,
            action="plugin.enable",
            entity_type="plugin",
            entity_id=plugin_id,
            details={"state": record.state},
        )
        db.commit()
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _refresh_plugin_runtime(request, db)
    refreshed = get_core_plugin(db, plugin_id=plugin_id)
    if refreshed is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(refreshed)


@router.post("/{plugin_id}/disable", response_model=PluginRuntimeRead)
def disable_plugin(
    plugin_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRuntimeRead:
    action_context = build_action_context(request, tenant_context)
    try:
        record = set_core_plugin_enabled(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            is_enabled=False,
            action_context=action_context,
        )
        audit_core_action(
            db,
            context=action_context,
            action="plugin.disable",
            entity_type="plugin",
            entity_id=plugin_id,
            details={"state": record.state},
        )
        db.commit()
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _refresh_plugin_runtime(request, db)
    refreshed = get_core_plugin(db, plugin_id=plugin_id)
    if refreshed is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(refreshed)


@router.post("/{plugin_id}/uninstall", response_model=PluginRuntimeRead)
def uninstall_plugin(
    plugin_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRuntimeRead:
    action_context = build_action_context(request, tenant_context)
    try:
        record = uninstall_core_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            context_builder=request.app.state.plugin_runtime.context_builder,
            action_context=action_context,
        )
        audit_core_action(
            db,
            context=action_context,
            action="plugin.uninstall",
            entity_type="plugin",
            entity_id=plugin_id,
            details={"state": record.state},
        )
        db.commit()
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _refresh_plugin_runtime(request, db)
    refreshed = get_core_plugin(db, plugin_id=plugin_id)
    if refreshed is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(refreshed)


@router.post("/{plugin_id}/migrate", response_model=PluginRuntimeRead)
def migrate_plugin(
    plugin_id: str,
    request: Request,
    payload: CorePluginMigrateRequest | None = MIGRATE_BODY,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_PLUGIN_MANAGE,
) -> PluginRuntimeRead:
    action_context = build_action_context(request, tenant_context)
    target_revision = payload.target_revision if payload is not None else None
    try:
        record = migrate_core_plugin(
            db,
            registry=request.app.state.plugin_registry,
            plugin_id=plugin_id,
            target_revision=target_revision,
            action_context=action_context,
        )
        audit_core_action(
            db,
            context=action_context,
            action="plugin.migrate",
            entity_type="plugin",
            entity_id=plugin_id,
            details={
                "migration_version": record.migration_version,
                "target_revision": target_revision,
            },
        )
        db.commit()
    except Exception:
        db.commit()
        _refresh_plugin_runtime(request, db)
        raise
    _refresh_plugin_runtime(request, db)
    refreshed = get_core_plugin(db, plugin_id=plugin_id)
    if refreshed is None:
        raise tenant_not_found("Plugin")
    return PluginRuntimeRead.model_validate(refreshed)
