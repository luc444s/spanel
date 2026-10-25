"""Application-local registry of site domains supplied by host plugins.

The registry lets two plugins cooperate on the same namespace without importing
each other's models:

- a *site provider* (hosting) answers which domains are primary for a site;
- an *extra claim checker* (domains) answers whether a domain is already owned
  as an extra file-provider route.

Domain names are global: both lookups intentionally ignore tenant boundaries so
a domain can never be claimed twice across tenants.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OwnedSite:
    site_id: str
    tenant_id: str
    container: str
    primary_domain: str | None


SiteProvider = Callable[[Session, str | None], Sequence[OwnedSite]]
ExtraClaimChecker = Callable[[Session, str], bool]


def register_site_provider(app: FastAPI, name: str, provider: SiteProvider) -> None:
    providers: dict[str, SiteProvider] | None = getattr(app.state, "site_domain_providers", None)
    if providers is None:
        providers = {}
        app.state.site_domain_providers = providers
    existing = providers.get(name)
    if existing is not None and (
        existing.__module__, existing.__qualname__
    ) != (provider.__module__, provider.__qualname__):
        raise ValueError(f"Duplicate site provider: {name}")
    providers[name] = provider


def register_extra_claims(app: FastAPI, name: str, checker: ExtraClaimChecker) -> None:
    checkers: dict[str, ExtraClaimChecker] | None = getattr(
        app.state, "extra_domain_claimers", None
    )
    if checkers is None:
        checkers = {}
        app.state.extra_domain_claimers = checkers
    existing = checkers.get(name)
    if existing is not None and (
        existing.__module__, existing.__qualname__
    ) != (checker.__module__, checker.__qualname__):
        raise ValueError(f"Duplicate extra claim checker: {name}")
    checkers[name] = checker


def list_sites(app: FastAPI, db: Session, tenant_id: str) -> list[OwnedSite]:
    """Sites owned by one tenant, with their primary domain when they have one."""
    providers: dict[str, SiteProvider] = getattr(app.state, "site_domain_providers", {})
    rows: list[OwnedSite] = []
    for provider in providers.values():
        rows.extend(provider(db, tenant_id))
    return rows


def resolve_site(app: FastAPI, db: Session, tenant_id: str, site_id: str) -> OwnedSite | None:
    for site in list_sites(app, db, tenant_id):
        if site.site_id == site_id:
            return site
    return None


def is_primary_domain(app: FastAPI, db: Session, domain: str) -> bool:
    """True when any site, in any tenant, already owns the domain as primary."""
    providers: dict[str, SiteProvider] = getattr(app.state, "site_domain_providers", {})
    needle = domain.strip().lower()
    for provider in providers.values():
        if any(
            (site.primary_domain or "").strip().lower() == needle
            for site in provider(db, None)
        ):
            return True
    return False


def is_extra_claimed(app: FastAPI, db: Session, domain: str) -> bool:
    """True when any registered plugin owns the domain as an extra route."""
    checkers: dict[str, ExtraClaimChecker] = getattr(app.state, "extra_domain_claimers", {})
    return any(checker(db, domain) for checker in checkers.values())
