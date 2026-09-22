from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from systutor.api.v1.core.common import CoreActionContext
from systutor.kernel.plugins.manifest import PluginManifest
from systutor.kernel.plugins.models import PluginRegistry
from systutor.kernel.plugins.persistent import (
    PluginOperationContext,
    disable_plugin,
    enable_plugin,
    install_plugin,
    list_plugin_registry_records,
    rollback_plugin,
    uninstall_plugin,
    upgrade_plugin,
)
from systutor.kernel.plugins.runtime import PluginManifestRegistry
from systutor.sdk import PluginContext


def list_core_plugins(db: Session) -> list[PluginRegistry]:
    return list_plugin_registry_records(db)


def get_core_plugin(db: Session, *, plugin_id: str) -> PluginRegistry | None:
    for record in list_plugin_registry_records(db):
        if record.plugin_id == plugin_id:
            return record
    return None


def install_core_plugin(
    db: Session,
    *,
    registry: PluginManifestRegistry,
    plugin_id: str,
    context_builder: Callable[[PluginManifest], PluginContext],
    action_context: CoreActionContext,
) -> PluginRegistry:
    return install_plugin(
        db,
        registry=registry,
        plugin_id=plugin_id,
        context_builder=context_builder,
        operation_context=_to_plugin_operation_context(action_context),
    )


def set_core_plugin_enabled(
    db: Session,
    *,
    registry: PluginManifestRegistry,
    plugin_id: str,
    context_builder: Callable[[PluginManifest], PluginContext],
    is_enabled: bool,
    action_context: CoreActionContext,
) -> PluginRegistry:
    operation_context = _to_plugin_operation_context(action_context)
    if is_enabled:
        return enable_plugin(
            db,
            registry=registry,
            plugin_id=plugin_id,
            context_builder=context_builder,
            operation_context=operation_context,
        )
    return disable_plugin(
        db,
        registry=registry,
        plugin_id=plugin_id,
        context_builder=context_builder,
        operation_context=operation_context,
    )


def uninstall_core_plugin(
    db: Session,
    *,
    registry: PluginManifestRegistry,
    plugin_id: str,
    context_builder: Callable[[PluginManifest], PluginContext],
    action_context: CoreActionContext,
) -> PluginRegistry:
    return uninstall_plugin(
        db,
        registry=registry,
        plugin_id=plugin_id,
        context_builder=context_builder,
        operation_context=_to_plugin_operation_context(action_context),
    )


def migrate_core_plugin(
    db: Session,
    *,
    registry: PluginManifestRegistry,
    plugin_id: str,
    target_revision: str | None,
    action_context: CoreActionContext,
) -> PluginRegistry:
    if target_revision == "rollback":
        return rollback_plugin(
            db,
            registry=registry,
            plugin_id=plugin_id,
            operation_context=_to_plugin_operation_context(action_context),
        )

    return upgrade_plugin(
        db,
        registry=registry,
        plugin_id=plugin_id,
        target_revision=target_revision,
        operation_context=_to_plugin_operation_context(action_context),
    )


def _to_plugin_operation_context(context: CoreActionContext) -> PluginOperationContext:
    return PluginOperationContext(
        actor_user_id=context.actor_user_id,
        actor_type="user",
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
    )
