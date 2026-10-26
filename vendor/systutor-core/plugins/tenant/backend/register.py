from __future__ import annotations

from sqlalchemy.orm import Session

from plugins.tenant.backend.router import router
from plugins.tenant.backend.service import get_site_suffix_for_tenant
from systutor.contracts.site_namespace import register_site_suffix_provider
from systutor.sdk.context import PluginContext


def site_suffix_provider(db: Session, tenant_id: str) -> str | None:
    """Provider que `domains` consume sin importar este servicio."""
    return get_site_suffix_for_tenant(db, tenant_id)


def register(context: PluginContext) -> None:
    context.register_router(router)
    app = context.router_registry
    if app is not None:
        register_site_suffix_provider(app, "tenant", site_suffix_provider)
