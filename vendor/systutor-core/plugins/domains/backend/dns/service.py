"""Tenant-aware DNS use cases.

PowerDNS remains the RRset authority.  PostgreSQL owns tenant-to-zone
ownership and the public nameserver configuration.  This module orchestrates
both, enforces optimistic versions and records audit/event metadata without
ever exposing the API key or raw TXT values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.errors import (
    DnsConflictError,
    DnsError,
    DnsNotFoundError,
    DnsPermissionError,
    DnsValidationError,
    PowerDnsNotFound,
)
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.nameservers import (
    PublicNameserverSettings,
    build_settings,
    seed_rrsets,
)
from plugins.domains.backend.dns.nameservers_service import (
    apply_nameservers as apply_nameservers_impl,
)
from plugins.domains.backend.dns.nameservers_service import (
    plan_nameservers as plan_nameservers_impl,
)
from plugins.domains.backend.dns.resolver import verify_nameservers
from plugins.domains.backend.dns.store import (
    get_ownership,
    get_public_settings,
    list_ownerships,
)
from plugins.domains.backend.dns.validation import (
    normalize_owner,
    normalize_rrset,
    normalize_zone_name,
    plan_changes,
    rrset_hash,
    validate_zone_changes,
    zone_version,
)
from plugins.domains.backend.dns.zonefile import export_zone, parse_zonefile
from plugins.domains.backend.models import DnsZoneOwnership
from systutor.contracts.domain_namespace import list_tenant_domains
from systutor.core.errors import AppError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext

Publisher = Callable[[str, dict[str, object], str, str], None]


def _translate(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except DnsError as exc:
            raise AppError(str(exc), status_code=exc.status_code, code=exc.code) from exc

    return wrapper


class DnsService:
    def __init__(
        self,
        db: Session,
        app: FastAPI | None,
        client: PowerDnsClient,
        *,
        publisher: Publisher | None = None,
    ) -> None:
        self._db = db
        self._app = app
        self._client = client
        self._publisher = publisher

    @property
    def client(self) -> PowerDnsClient:
        return self._client

    # -- ownership and audit helpers --------------------------------------
    def _tenant_id(self, context: TenantContext) -> str:
        if not context.current_tenant_id:
            raise DnsPermissionError("an active tenant is required")
        return context.current_tenant_id

    def _owned(
        self, zone: str, context: TenantContext, *, for_update: bool = False
    ) -> DnsZoneOwnership:
        row = get_ownership(
            self._db, zone, tenant_id=self._tenant_id(context), for_update=for_update
        )
        if row is None:
            raise DnsNotFoundError("zone not found")
        return row

    def _read(self, zone: str) -> DnsZone:
        return self._client.get_zone(zone)

    def _read_if_present(self, zone: str) -> DnsZone | None:
        try:
            return self._read(zone)
        except PowerDnsNotFound:
            return None

    def _audit(
        self,
        action: str,
        *,
        tenant_id: str | None,
        entity_id: str,
        details: dict,
        context: TenantContext | None = None,
    ) -> None:
        actor_user_id = context.current_user_id if context is not None else None
        actor_type = "user" if context is not None else "system"
        branch_id = context.current_branch_id if context is not None else None
        if context is not None:
            tenant_id = context.current_tenant_id
        record_audit(
            self._db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            module="domains",
            action=action,
            entity_type="dns",
            entity_id=entity_id,
            result="success",
            correlation_id=None,
            request_id=None,
            details=details,
        )

    def _publish(
        self,
        event_name: str,
        payload: dict[str, object],
        tenant_id: str | None,
        entity_id: str,
    ) -> None:
        if self._publisher is not None:
            self._publisher(event_name, payload, tenant_id, entity_id)
            return
        if self._app is None:
            return
        context = getattr(self._app.state, "domains_context", None)
        if context is None:
            return
        try:
            context.publish_event(
                event_name=event_name,
                payload=payload,
                tenant_id=tenant_id,
                entity_type="dns",
                entity_id=entity_id,
            )
        except Exception:  # noqa: BLE001 - audit already recorded the write
            pass

    @staticmethod
    def _audit_rrsets(rrsets: Sequence[DnsRRset]) -> list[dict[str, object]]:
        return [
            {
                "name": rrset.name,
                "type": rrset.type,
                "ttl": rrset.ttl,
                "records": len(rrset.values()),
                "hash": rrset_hash(rrset),
            }
            for rrset in sorted(rrsets, key=lambda item: (item.name, item.type))
        ]

    @staticmethod
    def _serialize(zone: DnsZone) -> dict[str, object]:
        return {
            "name": zone.name,
            "kind": zone.kind,
            "version": zone_version(zone),
            "serial": zone.serial,
            "rrsets": [rrset.to_dict() for rrset in zone.rrsets],
        }

    # -- zones ------------------------------------------------------------
    @_translate
    def list_zones(self, context: TenantContext) -> list[dict[str, object]]:
        tenant_id = self._tenant_id(context)
        return [
            {
                "name": row.zone_name,
                "kind": row.kind,
                "version": row.observed_version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in list_ownerships(self._db, tenant_id=tenant_id)
        ]

    @_translate
    def get_zone(self, zone: str, context: TenantContext) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        self._owned(zone_name, context)
        return self._serialize(self._read(zone_name))

    @_translate
    def resolve(self, domain: str, context: TenantContext) -> dict[str, object]:
        from systutor.contracts.domain_namespace import normalize_domain_name

        needle = normalize_domain_name(domain)
        tenant_id = self._tenant_id(context)
        matches = [
            row.zone_name
            for row in list_ownerships(self._db, tenant_id=tenant_id)
            if needle == row.zone_name or needle.endswith(f".{row.zone_name}")
        ]
        if not matches:
            raise DnsNotFoundError("domain is not inside an owned zone")
        zone = max(matches, key=len)
        relative = needle[: -(len(zone) + 1)] if needle != zone else "@"
        return {"domain": needle, "zone": zone, "relative_name": relative}

    @_translate
    def create_zone(self, zone: str, context: TenantContext) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        tenant_id = self._tenant_id(context)
        registered = list_tenant_domains(self._app, self._db, tenant_id)
        if zone_name not in registered:
            raise DnsPermissionError("the domain is not registered to the active tenant")
        if get_ownership(self._db, zone_name) is not None:
            raise DnsConflictError("zone already belongs to a tenant")
        settings = get_public_settings(self._db)
        if settings is None:
            raise DnsValidationError("public nameserver settings are not configured")
        if self._read_if_present(zone_name) is not None:
            raise DnsConflictError("zone already exists in PowerDNS")

        created = False
        try:
            self._client.create_zone(
                zone_name,
                nameservers=[settings.ns1_hostname, settings.ns2_hostname],
            )
            created = True
            seed = seed_rrsets(zone_name, settings)
            self._client.patch_rrsets(zone_name, seed)
            zone_state = self._read(zone_name)
            row = DnsZoneOwnership(
                tenant_id=tenant_id,
                zone_name=zone_name,
                kind=zone_state.kind,
                observed_version=zone_version(zone_state),
            )
            self._db.add(row)
            self._db.flush()
            self._audit(
                "domains.dns.zone_created",
                tenant_id=tenant_id,
                entity_id=zone_name,
                details={"zone": zone_name, "kind": zone_state.kind},
                context=context,
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            if created:
                try:
                    self._client.delete_zone(zone_name)
                except Exception:  # noqa: BLE001 - compensation is best effort
                    pass
            raise
        self._publish("domains.dns.zone_created", {"zone": zone_name}, tenant_id, zone_name)
        return self._serialize(zone_state)

    @_translate
    def delete_zone(
        self, zone: str, context: TenantContext, *, expected_version: str | None = None
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        row = self._owned(zone_name, context, for_update=True)
        current = self._read_if_present(zone_name)
        if current is not None:
            if expected_version is None:
                raise DnsConflictError(
                    "expected_version is required to replace an existing zone"
                )
            if zone_version(current) != expected_version:
                raise DnsConflictError("zone changed since it was read; refresh and try again")
        if current is not None:
            self._client.delete_zone(zone_name)
        self._db.delete(row)
        self._audit(
            "domains.dns.zone_deleted",
            tenant_id=row.tenant_id,
            entity_id=zone_name,
            details={"zone": zone_name, "powerdns_zone_present": current is not None},
            context=context,
        )
        self._db.commit()
        self._publish("domains.dns.zone_deleted", {"zone": zone_name}, row.tenant_id, zone_name)
        return {"zone": zone_name, "deleted": True, "released_ownership": True}

    # -- RRsets -----------------------------------------------------------
    @_translate
    def list_rrsets(self, zone: str, context: TenantContext) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        self._owned(zone_name, context)
        return self._serialize(self._read(zone_name))

    def _normalize_request(
        self, zone: str, rrsets: Sequence[dict[str, Any]], *, allow_soa: bool = False
    ) -> list[DnsRRset]:
        normalized: list[DnsRRset] = []
        keys: set[tuple[str, str]] = set()
        for item in rrsets:
            rrset = normalize_rrset(
                zone=zone,
                name=str(item.get("name", "")),
                type=str(item.get("type", "")),
                ttl=item.get("ttl", 300),
                values=[str(value) for value in item.get("values", [])],
                allow_soa=allow_soa,
            )
            key = (rrset.name, rrset.type)
            if key in keys:
                raise DnsValidationError("duplicate RRset in one request")
            keys.add(key)
            normalized.append(rrset)
        return normalized

    @_translate
    def plan_rrsets(
        self,
        zone: str,
        rrsets: Sequence[dict[str, Any]],
        context: TenantContext,
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        self._owned(zone_name, context)
        current = self._read(zone_name)
        desired = self._normalize_request(zone_name, rrsets)
        validate_zone_changes(current, desired)
        changes = plan_changes(current, {(item.name, item.type): item for item in desired})
        return {
            "zone": zone_name,
            "version": zone_version(current),
            "drift": bool(changes),
            "changes": changes,
            "not_physically_redundant": True,
        }

    @_translate
    def apply_rrsets(
        self,
        zone: str,
        rrsets: Sequence[dict[str, Any]],
        expected_version: str,
        context: TenantContext,
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        row = self._owned(zone_name, context, for_update=True)
        current = self._read(zone_name)
        current_version = zone_version(current)
        if not expected_version or current_version != expected_version:
            raise DnsConflictError("zone changed since it was read; refresh and try again")
        desired = self._normalize_request(zone_name, rrsets)
        validate_zone_changes(current, desired)
        changes = plan_changes(current, {(item.name, item.type): item for item in desired})
        if not changes:
            return self._serialize(current)
        self._client.patch_rrsets(zone_name, desired)
        updated = self._read(zone_name)
        row.observed_version = zone_version(updated)
        self._audit(
            "domains.dns.rrsets_changed",
            tenant_id=row.tenant_id,
            entity_id=zone_name,
            details={"zone": zone_name, "rrsets": self._audit_rrsets(desired)},
            context=context,
        )
        self._db.commit()
        self._publish(
            "domains.dns.rrsets_changed",
            {"zone": zone_name, "rrsets": self._audit_rrsets(desired)},
            row.tenant_id,
            zone_name,
        )
        return self._serialize(updated)

    @_translate
    def delete_rrset(
        self,
        zone: str,
        *,
        name: str,
        type: str,
        expected_version: str,
        context: TenantContext,
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        type_ = type.strip().upper()
        owner = normalize_owner(name, zone_name)
        if type_ == "SOA":
            raise DnsValidationError("the apex SOA cannot be deleted through the RRset endpoint")
        if type_ == "NS" and owner == zone_name:
            raise DnsValidationError("apex NS records cannot be deleted through the RRset endpoint")
        row = self._owned(zone_name, context, for_update=True)
        current = self._read(zone_name)
        if zone_version(current) != expected_version:
            raise DnsConflictError("zone changed since it was read; refresh and try again")
        existing = current.rrset(owner, type_)
        if existing is None:
            raise DnsNotFoundError("RRset not found")
        self._client.delete_rrset(zone_name, owner, type_)
        updated = self._read(zone_name)
        row.observed_version = zone_version(updated)
        self._audit(
            "domains.dns.rrsets_changed",
            tenant_id=row.tenant_id,
            entity_id=zone_name,
            details={"zone": zone_name, "deleted": self._audit_rrsets([existing])},
            context=context,
        )
        self._db.commit()
        self._publish(
            "domains.dns.rrsets_changed",
            {"zone": zone_name, "deleted": self._audit_rrsets([existing])},
            row.tenant_id,
            zone_name,
        )
        return self._serialize(updated)

    # -- zone files -------------------------------------------------------
    @_translate
    def export_zone(self, zone: str, context: TenantContext) -> str:
        zone_name = normalize_zone_name(zone)
        self._owned(zone_name, context)
        return export_zone(self._read(zone_name))

    @_translate
    def import_zone(
        self,
        zone: str,
        content: str,
        context: TenantContext,
        *,
        expected_version: str | None = None,
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        row = self._owned(zone_name, context, for_update=True)
        current = self._read_if_present(zone_name)
        if current is not None:
            if expected_version is None:
                raise DnsConflictError(
                    "expected_version is required to replace an existing zone"
                )
            if zone_version(current) != expected_version:
                raise DnsConflictError("zone changed since it was read; refresh and try again")
        settings = get_public_settings(self._db)
        default_ttl = settings.default_ttl if settings is not None else 300
        parsed = parse_zonefile(content, origin=zone_name, default_ttl=default_ttl)
        if not any(rrset.type == "SOA" and rrset.name == zone_name for rrset in parsed):
            raise DnsValidationError("the imported zone must contain an apex SOA")
        if not any(rrset.type == "NS" and rrset.name == zone_name for rrset in parsed):
            raise DnsValidationError("the imported zone must contain apex NS records")
        if current is None:
            self._client.create_zone(zone_name)
        else:
            validate_zone_changes(current, parsed, allow_soa=True)
        self._client.patch_rrsets(zone_name, parsed)
        updated = self._read(zone_name)
        row.observed_version = zone_version(updated)
        self._audit(
            "domains.dns.rrsets_changed",
            tenant_id=row.tenant_id,
            entity_id=zone_name,
            details={"zone": zone_name, "imported": self._audit_rrsets(parsed)},
            context=context,
        )
        self._db.commit()
        self._publish(
            "domains.dns.rrsets_changed",
            {"zone": zone_name, "imported": self._audit_rrsets(parsed)},
            row.tenant_id,
            zone_name,
        )
        return self._serialize(updated)

    # -- public nameservers ----------------------------------------------
    @_translate
    def nameserver_settings(self) -> dict[str, object]:
        settings = get_public_settings(self._db)
        if settings is None:
            return {
                "configured": False,
                "physical_redundancy": False,
                "warning": "public nameserver settings are not configured",
            }
        return {"configured": True, **settings.to_dict()}

    @_translate
    def plan_nameservers(
        self,
        desired: PublicNameserverSettings,
        *,
        zones: Sequence[str] | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, object]:
        return plan_nameservers_impl(
            self, desired, zones=zones, tenant_id=tenant_id
        )

    @_translate
    def apply_nameservers(
        self,
        desired: PublicNameserverSettings,
        *,
        zones: Sequence[str] | None,
        expected_versions: Mapping[str, str] | None = None,
        expected_version: int | None = None,
        context: TenantContext | None = None,
    ) -> dict[str, object]:
        return apply_nameservers_impl(
            self,
            desired,
            zones=zones,
            expected_versions=expected_versions,
            expected_version=expected_version,
            context=context,
        )

    @_translate
    def verify_nameservers(
        self, zone: str, *, resolver: str, port: int = 53, timeout: float = 5.0
    ) -> dict[str, object]:
        zone_name = normalize_zone_name(zone)
        return verify_nameservers(zone_name, resolver=resolver, port=port, timeout=timeout)


__all__ = ["DnsService", "PublicNameserverSettings", "build_settings"]
