"""Application-local registry of primary-site domain publishers.

A site owner (hosting) publishes the primary domain of a site through a
publisher supplied by the proxy owner (domains). The registry keeps the two
plugins independent: hosting never imports domains, and domains never imports
hosting.

``publish`` receives the container and the domain; ``unpublish`` receives the
domain and must be safe to call when the route no longer exists.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import FastAPI


@runtime_checkable
class SiteDomainPublisher(Protocol):
    def publish(self, container: str, domain: str) -> None:
        """Create the route that points ``domain`` at ``container``."""
        ...

    def unpublish(self, domain: str) -> None:
        """Remove the route previously created for ``domain``, if any."""
        ...


def register_site_domain_publisher(
    app: FastAPI,
    name: str,
    publisher: SiteDomainPublisher,
) -> None:
    publishers: dict[str, SiteDomainPublisher] | None = getattr(
        app.state, "site_domain_publishers", None
    )
    if publishers is None:
        publishers = {}
        app.state.site_domain_publishers = publishers
    existing = publishers.get(name)
    if existing is not None and (
        (type(existing).__module__, type(existing).__qualname__)
        != (type(publisher).__module__, type(publisher).__qualname__)
    ):
        raise ValueError(f"Duplicate site domain publisher: {name}")
    publishers[name] = publisher


def get_site_domain_publisher(app: FastAPI | None) -> SiteDomainPublisher | None:
    """The first registered publisher, or ``None`` when no plugin publishes."""
    if app is None:
        return None
    publishers: dict[str, SiteDomainPublisher] = getattr(
        app.state, "site_domain_publishers", {}
    )
    return next(iter(publishers.values()), None)
