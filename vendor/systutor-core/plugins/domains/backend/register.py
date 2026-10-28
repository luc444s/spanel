"""Registro del plugin de dominios."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from plugins.domains.backend.proxy import ProxyOperations, build_runner
from plugins.domains.backend.publisher import register_domains_publisher
from plugins.domains.backend.router import router
from plugins.domains.backend.service import published_domain_usage
from plugins.domains.backend.settings import get_domains_settings, register_domains_settings
from systutor.contracts.domain_namespace import register_domain_usage
from systutor.contracts.domain_ownership import register_extra_claims
from systutor.sdk.context import PluginContext

logger = logging.getLogger(__name__)


def check_extra_claim(db: Session, domain: str) -> bool:
    """¿El dominio ya lo publica un archivo `spanel-*.yml` gestionado?

    Si el proxy no está configurado o no se puede leer, devuelve False: la
    ventana queda declarada en la A.SPEC (Hosting no puede ver extras sin el
    plugin operativo). Un error se registra, nunca se silencia del todo.
    """
    try:
        settings = get_domains_settings().proxy_settings()
        operations = ProxyOperations(settings, build_runner(settings))
        needle = domain.strip().lower()
        return any(row.domain.lower() == needle for row in operations.managed_domains())
    except Exception as exc:  # noqa: BLE001 - no bloquear Hosting por el proxy
        logger.warning("domains_claim_check_failed", extra={"error": str(exc)})
        return False


def register(context: PluginContext) -> None:
    register_domains_settings()
    context.register_router(router)
    app = context.router_registry
    if app is not None:
        app.state.domains_context = context
        register_extra_claims(app, "domains", check_extra_claim)
        register_domain_usage(app, "domains", published_domain_usage)
        register_domains_publisher(context)
