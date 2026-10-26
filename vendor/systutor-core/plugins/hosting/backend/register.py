from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.router import router
from plugins.hosting.backend.settings import get_hosting_settings, register_hosting_settings
from plugins.hosting.backend.static import files_root_for
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.contracts.file_targets import FileTarget, register_file_target
from systutor.sdk.context import PluginContext


def resolve_hosting_sites(db: Session, tenant_id: str | None) -> list[OwnedSite]:
    """Primary domains for one tenant, or for every tenant when `tenant_id` is None."""
    stmt = select(HostingSite)
    if tenant_id is not None:
        stmt = stmt.where(HostingSite.tenant_id == tenant_id)
    return [
        OwnedSite(
            site_id=site.id,
            tenant_id=site.tenant_id,
            container=site.container_name,
            primary_domain=site.domain,
        )
        for site in db.scalars(stmt)
    ]


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
    return FileTarget(
        site.container_name,
        files_root_for(site.kind, get_hosting_settings().files_root),
        site.id,
    )


def register(context: PluginContext) -> None:
    register_hosting_settings()
    context.register_router(router)
    if context.router_registry is not None:
        register_file_target(context.router_registry, "hosting", resolve_hosting_files)
        register_site_provider(context.router_registry, "hosting", resolve_hosting_sites)
