"""Nameserver settings planning and application for ``DnsService``.

Kept separate from the zone/RRset service so every file stays below the
project review limit while the public methods remain on ``DnsService``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from plugins.domains.backend.dns.errors import (
    DnsConflictError,
    DnsNotFoundError,
    DnsValidationError,
)
from plugins.domains.backend.dns.models import DnsRRset
from plugins.domains.backend.dns.nameservers import (
    PublicNameserverSettings,
    reconcile_rrsets,
    settings_drift,
)
from plugins.domains.backend.dns.store import (
    get_ownership,
    get_public_settings,
    list_ownerships,
    save_public_settings,
)
from plugins.domains.backend.dns.validation import (
    normalize_zone_name,
    plan_changes,
    zone_version,
)
from plugins.domains.backend.models import DnsZoneOwnership
from systutor.kernel.tenants.context import TenantContext


def normalize_zone_set(zones: Sequence[str]) -> list[str]:
    approved: list[str] = []
    for zone in zones:
        normalized = normalize_zone_name(zone)
        if normalized in approved:
            raise DnsValidationError("duplicate zone in the approved set")
        approved.append(normalized)
    return approved


def approved_zone_rows(
    db, zones: Sequence[str] | None, *, tenant_id: str | None = None
) -> list[DnsZoneOwnership]:
    rows = list_ownerships(db, tenant_id=tenant_id)
    if zones is None:
        return rows
    approved = normalize_zone_set(zones)
    return [row for row in rows if row.zone_name in approved]


def plan_nameservers(
    service,
    desired: PublicNameserverSettings,
    *,
    zones: Sequence[str] | None = None,
    tenant_id: str | None = None,
) -> dict[str, object]:
    current = get_public_settings(service._db)
    drift = settings_drift(current, desired)
    zone_plans: list[dict[str, object]] = []
    versions: dict[str, str] = {}
    for row in approved_zone_rows(service._db, zones, tenant_id=tenant_id):
        zone = service._read(row.zone_name)
        version = zone_version(zone)
        changes = plan_changes(
            zone,
            {
                (rrset.name, rrset.type): rrset
                for rrset in reconcile_rrsets(zone, desired, serial=zone.serial)
            },
        )
        versions[row.zone_name] = version
        zone_plans.append(
            {
                "zone": row.zone_name,
                "version": version,
                "drift": bool(changes),
                "changes": changes,
            }
        )
    return {
        "settings_version": current.version if current else 0,
        "settings_changed": bool(drift),
        "settings_fields": drift,
        "zones": zone_plans,
        "zone_versions": versions,
        "drift": bool(drift or any(item["drift"] for item in zone_plans)),
        "physical_redundancy": False,
        "warning": desired.warning,
    }


def apply_nameservers(
    service,
    desired: PublicNameserverSettings,
    *,
    zones: Sequence[str] | None,
    expected_versions: Mapping[str, str] | None = None,
    expected_version: int | None = None,
    context: TenantContext | None = None,
) -> dict[str, object]:
    if zones is None:
        raise DnsValidationError(
            "an explicit approved zone set is required (use an empty set for bootstrap)"
        )
    approved = normalize_zone_set(zones)
    current = get_public_settings(service._db)
    if current is not None:
        if expected_version is None:
            raise DnsConflictError(
                "expected_version is required to change existing nameserver settings"
            )
        if current.version != int(expected_version):
            raise DnsConflictError(
                "nameserver settings were changed; refresh and try again"
            )
    supplied_versions = {
        normalize_zone_name(zone): version
        for zone, version in (expected_versions or {}).items()
    }
    planned: list[tuple[DnsZoneOwnership, list[DnsRRset], list[dict[str, object]]]] = []
    # Validate every approved zone before touching settings or PowerDNS.
    for zone_name in approved:
        row = get_ownership(service._db, zone_name)
        if row is None:
            raise DnsNotFoundError("zone not found")
        expected = supplied_versions.get(zone_name)
        if not expected:
            raise DnsConflictError(
                f"expected version is required for approved zone {zone_name}"
            )
        zone = service._read(zone_name)
        if zone_version(zone) != expected:
            raise DnsConflictError(
                f"zone {zone_name} changed since it was read; refresh and try again"
            )
        rrsets = reconcile_rrsets(zone, desired, serial=zone.serial)
        changes = plan_changes(
            zone, {(item.name, item.type): item for item in rrsets}
        )
        planned.append((row, rrsets, changes))

    saved = save_public_settings(
        service._db, desired, expected_version=expected_version
    )
    changed: list[str] = []
    versions: dict[str, str] = {}
    for row, rrsets, changes in planned:
        if not changes:
            versions[row.zone_name] = row.observed_version
            continue
        service._client.patch_rrsets(row.zone_name, rrsets)
        updated = service._read(row.zone_name)
        row.observed_version = zone_version(updated)
        versions[row.zone_name] = row.observed_version
        changed.append(row.zone_name)
    tenant_id = context.current_tenant_id if context is not None else None
    service._audit(
        "domains.dns.nameservers_changed",
        tenant_id=tenant_id,
        entity_id="domains_dns_settings",
        details={
            "version": saved.version,
            "settings": saved.to_dict(),
            "zones": sorted(changed),
        },
        context=context,
    )
    service._db.commit()
    service._publish(
        "domains.dns.nameservers_changed",
        {"version": saved.version, "zones": sorted(changed)},
        tenant_id,
        "domains_dns_settings",
    )
    return {
        "settings": saved.to_dict(),
        "changed_zones": sorted(changed),
        "zone_versions": versions,
        "physical_redundancy": False,
        "warning": saved.warning,
    }
