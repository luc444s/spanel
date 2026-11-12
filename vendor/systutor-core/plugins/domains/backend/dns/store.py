"""Persistence helpers for DNS ownership and public nameserver settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.domains.backend.dns.errors import DnsConflictError
from plugins.domains.backend.dns.nameservers import PublicNameserverSettings
from plugins.domains.backend.models import DnsPublicSettings, DnsZoneOwnership


def get_ownership(
    db: Session,
    zone_name: str,
    *,
    tenant_id: str | None = None,
    for_update: bool = False,
) -> DnsZoneOwnership | None:
    statement = select(DnsZoneOwnership).where(DnsZoneOwnership.zone_name == zone_name)
    if tenant_id is not None:
        statement = statement.where(DnsZoneOwnership.tenant_id == tenant_id)
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def get_ownership_any_tenant(
    db: Session, zone_name: str, *, for_update: bool = False
) -> DnsZoneOwnership | None:
    return get_ownership(db, zone_name, for_update=for_update)


def list_ownerships(db: Session, *, tenant_id: str | None = None) -> list[DnsZoneOwnership]:
    statement = select(DnsZoneOwnership).order_by(DnsZoneOwnership.zone_name.asc())
    if tenant_id is not None:
        statement = statement.where(DnsZoneOwnership.tenant_id == tenant_id)
    return list(db.scalars(statement).all())


def get_settings_row(db: Session, *, for_update: bool = False) -> DnsPublicSettings | None:
    if not for_update:
        return db.get(DnsPublicSettings, 1)
    return db.scalar(
        select(DnsPublicSettings)
        .where(DnsPublicSettings.id == 1)
        .with_for_update()
    )


def settings_from_row(row: DnsPublicSettings) -> PublicNameserverSettings:
    return PublicNameserverSettings(
        ns1_hostname=row.ns1_hostname,
        ns2_hostname=row.ns2_hostname,
        ns1_ipv4=row.ns1_ipv4,
        ns1_ipv6=row.ns1_ipv6,
        ns2_ipv4=row.ns2_ipv4,
        ns2_ipv6=row.ns2_ipv6,
        soa_contact=row.soa_contact,
        default_ttl=row.default_ttl,
        version=row.version,
    )


def get_public_settings(db: Session) -> PublicNameserverSettings | None:
    row = get_settings_row(db)
    return settings_from_row(row) if row is not None else None


def save_public_settings(
    db: Session,
    desired: PublicNameserverSettings,
    *,
    expected_version: int | None = None,
) -> PublicNameserverSettings:
    row = get_settings_row(db, for_update=True)
    if row is None:
        if expected_version not in {None, 0}:
            raise DnsConflictError("nameserver settings were changed; refresh and try again")
        row = DnsPublicSettings(
            id=1,
            ns1_hostname=desired.ns1_hostname,
            ns2_hostname=desired.ns2_hostname,
            ns1_ipv4=desired.ns1_ipv4,
            ns1_ipv6=desired.ns1_ipv6,
            ns2_ipv4=desired.ns2_ipv4,
            ns2_ipv6=desired.ns2_ipv6,
            soa_contact=desired.soa_contact,
            default_ttl=desired.default_ttl,
            version=1,
        )
        db.add(row)
    else:
        if expected_version is not None and row.version != int(expected_version):
            raise DnsConflictError("nameserver settings were changed; refresh and try again")
        row.ns1_hostname = desired.ns1_hostname
        row.ns2_hostname = desired.ns2_hostname
        row.ns1_ipv4 = desired.ns1_ipv4
        row.ns1_ipv6 = desired.ns1_ipv6
        row.ns2_ipv4 = desired.ns2_ipv4
        row.ns2_ipv6 = desired.ns2_ipv6
        row.soa_contact = desired.soa_contact
        row.default_ttl = desired.default_ttl
        row.version += 1
    db.flush()
    return settings_from_row(row)
