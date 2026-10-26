"""Publisher of a site's primary domain through the Traefik file provider.

The hosting plugin only knows the contract; this module is the domains side.
``publish`` writes one managed YAML for the container (no certificate wait and
no WordPress URL rewrite), while ``unpublish`` removes the domain and is
idempotent when the route is already gone.
"""

from __future__ import annotations

import logging

from systutor.contracts.site_domain_publisher import SiteDomainPublisher
from systutor.sdk.context import PluginContext

from plugins.domains.backend.proxy import ProxyError, ProxyOperations, build_runner
from plugins.domains.backend.settings import get_domains_settings

logger = logging.getLogger(__name__)


class ProxySitePublisher:
    """Adapt ``ProxyOperations`` to the ``SiteDomainPublisher`` contract."""

    def __init__(self, operations: ProxyOperations | None = None) -> None:
        self._operations = operations

    @property
    def operations(self) -> ProxyOperations:
        if self._operations is None:
            settings = get_domains_settings().proxy_settings()
            self._operations = ProxyOperations(settings, build_runner(settings))
        return self._operations

    def publish(self, container: str, domain: str) -> None:
        self.operations.add(
            container,
            domain,
            wait=0,
            set_wordpress_url=False,
        )

    def unpublish(self, domain: str) -> None:
        try:
            self.operations.remove(domain)
        except ProxyError as exc:
            # Deleting a site must not fail because its route was already
            # removed by an operator. Any other proxy error still surfaces.
            if "no está gestionado" in str(exc):
                logger.info("domains_unpublish_missing_route", extra={"domain": domain})
                return
            raise


def register_domains_publisher(context: PluginContext) -> None:
    app = context.router_registry
    if app is None:
        return
    from systutor.contracts.site_domain_publisher import register_site_domain_publisher

    register_site_domain_publisher(app, "domains", ProxySitePublisher())


__all__ = ["ProxySitePublisher", "register_domains_publisher", "SiteDomainPublisher"]
