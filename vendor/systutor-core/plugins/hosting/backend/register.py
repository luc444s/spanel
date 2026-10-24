from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.router import router
from plugins.hosting.backend.settings import get_hosting_settings, register_hosting_settings
from systutor.contracts.file_targets import FileTarget, register_file_target
from systutor.sdk.context import PluginContext


def resolve_hosting_files(
    db: Session, tenant_id: str, resource_id: str, write: bool, permissions: tuple[str, ...]
) -> FileTarget | None:
    permission = "hosting.sites.manage" if write else "hosting.sites.read"
    if permission not in permissions:
        return None
    site = db.scalar(
        select(HostingSite).where(HostingSite.id == resource_id, HostingSite.tenant_id == tenant_id)
    )
    if site is None:
        return None
    return FileTarget(site.container_name, get_hosting_settings().files_root, site.id)


def register(context: PluginContext) -> None:
    register_hosting_settings()
    context.register_router(router)
    if context.router_registry is not None:
        register_file_target(context.router_registry, "hosting", resolve_hosting_files)
