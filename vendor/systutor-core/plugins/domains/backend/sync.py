"""Build and atomically activate the observed Traefik domain inventory."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from plugins.domains.backend.models import DomainSyncState, ObservedDomain
from plugins.domains.backend.proxy import (
    ManagedDocument,
    ProxyError,
    ProxyOperations,
    document_container,
    document_domains,
    document_resolver,
    is_managed_document,
    parse_site_document,
)
from systutor.contracts.domain_namespace import domain_owner
from systutor.contracts.domain_ownership import OwnedSite, list_sites
from systutor.kernel.auth.models import utc_now


@dataclass(frozen=True)
class Observation:
    domain: str
    tenant_id: str
    site_id: str
    container: str
    resolver: str
    source_file: str
    document_hash: str


@dataclass
class SyncPlan:
    generation: str
    documents: int
    observations: list[Observation] = field(default_factory=list)
    additions: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.conflicts or self.orphans or self.invalid)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["ok"] = self.ok
        payload["domains"] = len(self.observations)
        return payload


def _sites_by_container(app: FastAPI, db: Session) -> dict[str, list[OwnedSite]]:
    result: dict[str, list[OwnedSite]] = {}
    for site in list_sites(app, db, None):  # type: ignore[arg-type]
        result.setdefault(site.container, []).append(site)
    return result


def _document_observations(
    db: Session,
    app: FastAPI,
    document: ManagedDocument,
    sites: dict[str, list[OwnedSite]],
    plan: SyncPlan,
) -> list[Observation]:
    name = document.path.name
    if not is_managed_document(document.text):
        plan.invalid.append(f"{name}: missing ownership marker")
        return []
    try:
        data = parse_site_document(document.text)
    except ProxyError as exc:
        plan.invalid.append(f"{name}: {exc}")
        return []
    container = document_container(data)
    domains = document_domains(data)
    if not container or not domains:
        plan.invalid.append(f"{name}: missing container or domains")
        return []
    candidates = sites.get(container, [])
    if len(candidates) != 1:
        detail = "unregistered container" if not candidates else "ambiguous container"
        plan.orphans.append(f"{name}: {container}: {detail}")
        return []
    site = candidates[0]
    digest = hashlib.sha256(document.text.encode()).hexdigest()
    resolver = document_resolver(data) or "?"
    result: list[Observation] = []
    for domain in domains:
        owner = domain_owner(app, db, domain)
        if owner is not None and owner != site.tenant_id:
            plan.conflicts.append(
                f"{domain}: administrative owner {owner} differs from site tenant {site.tenant_id}"
            )
            continue
        result.append(
            Observation(
                domain=domain,
                tenant_id=site.tenant_id,
                site_id=site.site_id,
                container=container,
                resolver=resolver,
                source_file=name,
                document_hash=digest,
            )
        )
    return result


def prepare_sync(
    db: Session,
    app: FastAPI,
    operations: ProxyOperations,
    *,
    site_overrides: dict[str, str] | None = None,
) -> SyncPlan:
    """Read one complete proxy snapshot and prepare a no-write reconciliation."""
    documents = operations.managed_documents()
    plan = SyncPlan(generation=str(uuid4()), documents=len(documents))
    sites = _sites_by_container(app, db)
    for container, site_id in (site_overrides or {}).items():
        sites[container] = [
            site for site in sites.get(container, []) if site.site_id == site_id
        ]
    by_domain: dict[str, Observation] = {}
    for document in documents:
        for row in _document_observations(db, app, document, sites, plan):
            previous = by_domain.get(row.domain)
            if previous is not None:
                plan.conflicts.append(
                    f"{row.domain}: published by both {previous.source_file} and {row.source_file}"
                )
                continue
            by_domain[row.domain] = row
    plan.observations = sorted(by_domain.values(), key=lambda row: row.domain)

    state = db.get(DomainSyncState, 1)
    current: dict[str, ObservedDomain] = {}
    if state is not None and state.active_generation:
        current = {
            row.domain: row
            for row in db.scalars(
                select(ObservedDomain).where(
                    ObservedDomain.generation == state.active_generation
                )
            )
        }
    incoming = {row.domain: row for row in plan.observations}
    plan.additions = sorted(incoming.keys() - current.keys())
    plan.removals = sorted(current.keys() - incoming.keys())
    for domain in sorted(incoming.keys() & current.keys()):
        new = incoming[domain]
        old = current[domain]
        if any(
            (
                new.tenant_id != old.tenant_id,
                new.site_id != old.site_id,
                new.container != old.container,
                new.resolver != old.resolver,
                new.source_file != old.source_file,
                new.document_hash != old.document_hash,
            )
        ):
            plan.changes.append(domain)
    return plan


def apply_sync(db: Session, plan: SyncPlan) -> DomainSyncState:
    """Activate a fully prepared generation in the caller's transaction."""
    if not plan.ok:
        raise ValueError("cannot apply an incomplete or ambiguous domain snapshot")
    observed_at = utc_now()
    for row in plan.observations:
        db.add(
            ObservedDomain(
                domain=row.domain,
                tenant_id=row.tenant_id,
                site_id=row.site_id,
                container=row.container,
                resolver=row.resolver,
                source_file=row.source_file,
                document_hash=row.document_hash,
                generation=plan.generation,
                observed_at=observed_at,
            )
        )
    db.flush()
    state = db.get(DomainSyncState, 1)
    if state is None:
        state = DomainSyncState(id=1)
        db.add(state)
    state.active_generation = plan.generation
    state.synced_at = observed_at
    state.document_count = plan.documents
    state.domain_count = len(plan.observations)
    state.conflict_count = 0
    state.orphan_count = 0
    state.invalid_count = 0
    state.last_error = None
    db.flush()
    db.execute(delete(ObservedDomain).where(ObservedDomain.generation != plan.generation))
    return state


def mark_sync_error(db: Session, message: str, plan: SyncPlan | None = None) -> None:
    """Record a bounded diagnostic without moving the active generation."""
    state = db.get(DomainSyncState, 1)
    if state is None:
        state = DomainSyncState(id=1)
        db.add(state)
    state.last_error = message[:1000]
    if plan is not None:
        state.conflict_count = len(plan.conflicts)
        state.orphan_count = len(plan.orphans)
        state.invalid_count = len(plan.invalid)
    db.flush()
