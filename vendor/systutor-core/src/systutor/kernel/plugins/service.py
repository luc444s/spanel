from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.kernel.audit.service import record_audit
from systutor.kernel.permissions.models import Permission, RolePermission
from systutor.kernel.permissions.service import (
    ensure_permission,
    get_permission_by_name,
)
from systutor.kernel.plugins.models import PluginRegistry, utc_now
from systutor.kernel.plugins.persistent import (
    get_plugin_registry_record_by_plugin_id,
    list_plugin_registry_records,
)
from systutor.kernel.plugins.runtime import LoadedPlugin

# A.SPEC 0045: platform-wide capability decided by RBAC, never by a flag.
PLATFORM_ADMIN_PERMISSION = "core.platform.admin"


def _sync_manifest_permissions(
    db: Session,
    manifest,
    *,
    correlation_id: str | None,
) -> None:
    """Migrate one manifest's declared permissions at boot (additive-only).

    Ensures every declared permission exists in the catalog and grants
    `platform_permissions` to every role already holding `core.platform.admin`
    (found via role_permissions, not a role name). Never revokes; audits only
    what was actually added.
    """
    added: list[str] = []

    for permission_name in manifest.permissions:
        if get_permission_by_name(db, permission_name=permission_name) is None:
            added.append(permission_name)
        ensure_permission(
            db,
            permission_name=permission_name,
            description=f"Plugin permission {permission_name} declared by {manifest.id}",
        )

    permission_ids_by_name: dict[str, str] = {}
    for permission_name in manifest.platform_permissions:
        permission = ensure_permission(
            db,
            permission_name=permission_name,
            description=f"Plugin permission {permission_name} declared by {manifest.id}",
        )
        permission_ids_by_name[permission_name] = permission.id

    platform_admin_permission = get_permission_by_name(
        db, permission_name=PLATFORM_ADMIN_PERMISSION
    )
    platform_role_ids: list[str]
    if platform_admin_permission is None or not permission_ids_by_name:
        platform_role_ids = []
    else:
        platform_role_ids = sorted(
            set(
                db.scalars(
                    select(RolePermission.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(Permission.name == PLATFORM_ADMIN_PERMISSION)
                )
            )
        )

    for role_id in platform_role_ids:
        owned_permission_ids = set(
            db.scalars(
                select(RolePermission.permission_id).where(
                    RolePermission.role_id == role_id,
                    RolePermission.permission_id.in_(permission_ids_by_name.values()),
                )
            )
        )
        for permission_name, permission_id in permission_ids_by_name.items():
            if permission_id in owned_permission_ids:
                continue
            db.add(RolePermission(role_id=role_id, permission_id=permission_id))
            if permission_name not in added:
                added.append(permission_name)

    if added:
        record_audit(
            db,
            tenant_id=None,
            branch_id=None,
            actor_user_id=None,
            actor_type="system",
            module="core",
            action="plugin.permissions.synced",
            entity_type="plugin",
            entity_id=manifest.id,
            result="success",
            correlation_id=correlation_id,
            request_id=None,
            details={
                "plugin_id": manifest.id,
                "added": sorted(added),
            },
        )


def sync_plugin_registry(
    db: Session,
    plugins: list[LoadedPlugin],
    *,
    correlation_id: str | None = None,
) -> None:
    for plugin in plugins:
        manifest = plugin.manifest
        if manifest is None:
            continue

        record = get_plugin_registry_record_by_plugin_id(db, plugin_id=manifest.id)
        if record is None:
            record = PluginRegistry(
                plugin_id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                api_version=manifest.api_version,
                state=plugin.status,
                is_enabled=plugin.status == "enabled",
                backend_entrypoint=manifest.backend_entrypoint,
                frontend_entrypoint=manifest.frontend_entrypoint,
                requires_json=list(manifest.requires),
                permissions_json=list(manifest.permissions),
                events_json=list(manifest.events),
                description=manifest.description,
            )

        record.name = manifest.name
        record.version = manifest.version
        record.api_version = manifest.api_version
        record.backend_entrypoint = manifest.backend_entrypoint
        record.frontend_entrypoint = manifest.frontend_entrypoint
        record.requires_json = list(manifest.requires)
        record.permissions_json = list(manifest.permissions)
        record.events_json = list(manifest.events)
        record.description = manifest.description
        record.state = plugin.status
        record.is_enabled = plugin.status == "enabled"
        record.last_error = plugin.error_message
        if plugin.status == "enabled" and record.enabled_at is None:
            record.enabled_at = utc_now()
            record.installed_at = record.installed_at or utc_now()
        if plugin.status == "disabled" and record.disabled_at is None:
            record.disabled_at = utc_now()

        db.add(record)

        # A.SPEC 0045: runtime data migration — catalog + platform grants.
        _sync_manifest_permissions(
            db,
            manifest,
            correlation_id=correlation_id,
        )

        if plugin.status in {"enabled", "failed", "disabled"}:
            record_audit(
                db,
                tenant_id=None,
                branch_id=None,
                actor_user_id=None,
                actor_type="system",
                module="core",
                action=f"plugin.{plugin.status}",
                entity_type="plugin",
                entity_id=manifest.id,
                result="success" if plugin.status != "failed" else "failure",
                correlation_id=correlation_id,
                request_id=None,
                details={
                    "plugin_id": manifest.id,
                    "version": manifest.version,
                    "api_version": manifest.api_version,
                    "error": plugin.error_message,
                },
            )

    db.flush()


__all__ = [
    "get_plugin_registry_record_by_plugin_id",
    "list_plugin_registry_records",
    "sync_plugin_registry",
]
