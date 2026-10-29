from __future__ import annotations

from sqlalchemy.orm import Session

from plugins.tenant.backend.router import router
from plugins.tenant.backend.service import (
    claim_domain_for_tenant,
    get_managed_domain_owner,
    get_site_suffix_for_tenant,
    list_managed_domains,
    release_domain_for_tenant,
)
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.site_namespace import register_site_suffix_provider
from systutor.sdk.context import PluginContext


def site_suffix_provider(db: Session, tenant_id: str) -> str | None:
    """Provider que `domains` consume sin importar este servicio."""
    return get_site_suffix_for_tenant(db, tenant_id)


def managed_domains_provider(db: Session, tenant_id: str) -> list[str]:
    """Provider del contrato: dominios propiedad explícita del tenant."""
    return list_managed_domains(db, tenant_id)


def domain_owner_provider(db: Session, domain: str) -> str | None:
    """Provider del contrato: dueño global de un dominio."""
    return get_managed_domain_owner(db, domain)


def domain_claim_provider(db: Session, tenant_id: str, domain: str) -> None:
    """Provider del contrato: reclama un dominio libre para el tenant."""
    claim_domain_for_tenant(db, tenant_id, domain)


def domain_release_provider_factory(app: object | None):
    """Provider del contrato: libera una reserva del tenant.

    Se construye con la app para que `remove_managed_domain` pueda respetar
    primarios de sitios y dominios publicados en uso.
    """

    def release_provider(db: Session, tenant_id: str, domain: str) -> None:
        release_domain_for_tenant(db, tenant_id, domain, app=app)  # type: ignore[arg-type]

    return release_provider


def register(context: PluginContext) -> None:
    context.register_router(router)
    app = context.router_registry
    if app is not None:
        register_site_suffix_provider(app, "tenant", site_suffix_provider)
        register_domain_providers(
            app,
            "tenant",
            list_domains=managed_domains_provider,
            owner=domain_owner_provider,
            claim=domain_claim_provider,
            release=domain_release_provider_factory(app),
        )
