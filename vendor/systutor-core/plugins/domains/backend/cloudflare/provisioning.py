"""Tenant-scoped Cloudflare zone and origin-record provisioning policy."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import cast

from plugins.domains.backend.cloudflare.client import (
    CloudflareConflict,
    CloudflareRecord,
    CloudflareValidation,
    CloudflareZone,
)
from plugins.domains.backend.cloudflare.service import CloudflareService
from systutor.kernel.tenants.context import TenantContext

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CONFLICTING_TYPES = {"A", "AAAA", "CNAME"}


def normalize_domain(value: str) -> str:
    raw = (value or "").strip().rstrip(".")
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CloudflareValidation("the domain is not a valid DNS hostname") from exc
    if not domain or len(domain) > 253 or "." not in domain:
        raise CloudflareValidation("the domain is not a valid DNS hostname")
    if any(not _LABEL.fullmatch(label) for label in domain.split(".")):
        raise CloudflareValidation("the domain is not a valid DNS hostname")
    return domain


def normalize_ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address((value or "").strip()))
    except ipaddress.AddressValueError as exc:
        raise CloudflareValidation("a valid IPv4 address is required") from exc


def _record_dict(record: CloudflareRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "name": record.name.rstrip(".").lower(),
        "type": record.type,
        "content": record.content,
        "ttl": record.ttl,
        "proxied": record.proxied,
    }


def _desired(domain: str, ipv4: str, www: bool) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [
        {"name": domain, "type": "A", "content": ipv4, "ttl": 1, "proxied": False}
    ]
    if www:
        records.append(
            {
                "name": f"www.{domain}",
                "type": "CNAME",
                "content": domain,
                "ttl": 1,
                "proxied": False,
            }
        )
    return records


def _same(record: CloudflareRecord, desired: dict[str, object]) -> bool:
    content = record.content.rstrip(".").lower() if record.type == "CNAME" else record.content
    expected = str(desired["content"]).rstrip(".").lower()
    return (
        record.type == desired["type"]
        and content == expected
        and record.ttl == desired["ttl"]
        and record.proxied is False
    )


@dataclass(frozen=True)
class _State:
    zone: CloudflareZone | None
    records: list[CloudflareRecord]


class CloudflareProvisioningService:
    """Plan and apply only the explicitly managed apex and optional www owner."""

    def __init__(self, service: CloudflareService) -> None:
        self._service = service
        self._client = service.client

    def _access(self, tenant_id: str):
        return self._service.provisioning_access(tenant_id)

    @staticmethod
    def _select_zone(domain: str, zones: list[CloudflareZone]) -> CloudflareZone | None:
        candidates = [
            zone
            for zone in zones
            if domain == zone.name.rstrip(".").lower()
            or domain.endswith("." + zone.name.rstrip(".").lower())
        ]
        return max(candidates, key=lambda item: len(item.name.rstrip(".")), default=None)

    def _state(self, tenant_id: str, domain: str) -> _State:
        credentials, refresh, _account_id = self._access(tenant_id)
        zone = self._select_zone(domain, self._client.list_zones(credentials, refresh))
        records = (
            self._client.list_records(credentials, zone.id, refresh) if zone is not None else []
        )
        return _State(zone, records)

    @staticmethod
    def _build_plan(
        domain: str,
        ipv4: str,
        www: bool,
        replace: bool,
        state: _State,
    ) -> dict[str, object]:
        desired = _desired(domain, ipv4, www)
        managed = {str(item["name"]): item for item in desired}
        relevant = [
            record
            for record in state.records
            if record.name.rstrip(".").lower() in managed
            and record.type in _CONFLICTING_TYPES
        ]
        identical: set[str] = set()
        conflicts: list[CloudflareRecord] = []
        for record in relevant:
            owner = record.name.rstrip(".").lower()
            if _same(record, managed[owner]):
                identical.add(owner)
            else:
                conflicts.append(record)

        actions: list[dict[str, object]] = []
        if state.zone is None:
            actions.append({"action": "create_zone", "name": domain, "type": "full"})
        if replace:
            actions.extend(
                {"action": "delete_record", **_record_dict(record)} for record in conflicts
            )
        for record in desired:
            owner = str(record["name"])
            if owner not in identical and (replace or not any(
                item.name.rstrip(".").lower() == owner for item in conflicts
            )):
                actions.append({"action": "create_record", **record})

        zone_payload = state.zone.to_dict() if state.zone is not None else None
        return {
            "domain": domain,
            "ipv4": ipv4,
            "www": www,
            "replace": replace,
            "zone_action": "reuse" if state.zone is not None else "create",
            "zone": zone_payload,
            "nameservers": list(state.zone.name_servers) if state.zone is not None else [],
            "desired_records": desired,
            "conflicts": [_record_dict(record) for record in conflicts],
            "actions": actions,
            "drift": state.zone is None or bool(conflicts) or len(identical) != len(desired),
        }

    def plan_for_tenant(
        self, tenant_id: str, *, domain: str, ipv4: str, www: bool, replace: bool
    ) -> dict[str, object]:
        normalized_domain = normalize_domain(domain)
        normalized_ipv4 = normalize_ipv4(ipv4)
        return self._build_plan(
            normalized_domain,
            normalized_ipv4,
            www,
            replace,
            self._state(tenant_id, normalized_domain),
        )

    def plan(
        self, context: TenantContext, *, domain: str, ipv4: str, www: bool, replace: bool
    ) -> dict[str, object]:
        return self.plan_for_tenant(
            self._service._tenant_id(context),  # noqa: SLF001 - shared tenant guard
            domain=domain,
            ipv4=ipv4,
            www=www,
            replace=replace,
        )

    def apply_for_tenant(
        self,
        tenant_id: str,
        *,
        domain: str,
        ipv4: str,
        www: bool,
        replace: bool,
        context: TenantContext | None = None,
    ) -> dict[str, object]:
        plan = self.plan_for_tenant(
            tenant_id, domain=domain, ipv4=ipv4, www=www, replace=replace
        )
        if plan["conflicts"] and not replace:
            raise CloudflareConflict(
                "conflicting Cloudflare records exist; plan and apply with replace"
            )
        credentials, refresh, account_id = self._access(tenant_id)
        zone_payload = plan["zone"]
        zone_id = str(zone_payload["id"]) if isinstance(zone_payload, dict) else ""
        created_zone = False
        try:
            if not zone_id:
                zone = self._client.create_zone(
                    credentials,
                    account_id=account_id,
                    name=str(plan["domain"]),
                    refresh=refresh,
                )
                zone_id = zone.id
                created_zone = True
            actions = cast(list[dict[str, object]], plan["actions"])
            for action in actions:
                if action["action"] == "delete_record":
                    self._client.delete_record(
                        credentials, zone_id, str(action["id"]), refresh
                    )
                elif action["action"] == "create_record":
                    payload = {
                        key: action[key]
                        for key in ("name", "type", "content", "ttl", "proxied")
                    }
                    self._client.create_record(credentials, zone_id, payload, refresh)
        except Exception:
            if created_zone and zone_id:
                try:
                    self._client.delete_zone(credentials, zone_id, refresh)
                except Exception:  # noqa: BLE001 - compensation is best effort
                    pass
            raise

        zone = self._client.get_zone(credentials, zone_id, refresh)
        desired_records = cast(list[dict[str, object]], plan["desired_records"])
        owners = {str(item["name"]) for item in desired_records}
        records = [
            _record_dict(record)
            for record in self._client.list_records(credentials, zone_id, refresh)
            if record.name.rstrip(".").lower() in owners
            and record.type in _CONFLICTING_TYPES
        ]
        self._service.record_provisioning(
            tenant_id=tenant_id,
            domain=str(plan["domain"]),
            zone_id=zone_id,
            www=www,
            replaced=replace,
            context=context,
        )
        return {
            **plan,
            "applied": True,
            "drift": False,
            "zone": zone.to_dict(),
            "nameservers": list(zone.name_servers),
            "records": records,
        }

    def apply(
        self, context: TenantContext, *, domain: str, ipv4: str, www: bool, replace: bool
    ) -> dict[str, object]:
        return self.apply_for_tenant(
            self._service._tenant_id(context),  # noqa: SLF001 - shared tenant guard
            domain=domain,
            ipv4=ipv4,
            www=www,
            replace=replace,
            context=context,
        )
