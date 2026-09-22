from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.kernel.audit.service import record_audit
from systutor.kernel.plugins.models import PluginRegistry, utc_now
from systutor.kernel.plugins.persistent import (
    get_plugin_registry_record_by_plugin_id,
    list_plugin_registry_records,
)
from systutor.kernel.plugins.runtime import LoadedPlugin


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
