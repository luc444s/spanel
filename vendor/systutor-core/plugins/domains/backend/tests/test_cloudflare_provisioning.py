"""Cloudflare provisioning policy: validation, drift, replacement and compensation."""

from __future__ import annotations

import pytest

from plugins.domains.backend.cloudflare.client import (
    CloudflareConflict,
    CloudflareCredentials,
    CloudflareRecord,
    CloudflareValidation,
    CloudflareZone,
)
from plugins.domains.backend.cloudflare.provisioning import CloudflareProvisioningService


def zone(id_: str, name: str) -> CloudflareZone:
    return CloudflareZone(
        id_, name, "active", "acct1", "Lab", ("ns1", "ns2"), None, None
    )


def record(
    id_: str,
    name: str,
    type_: str,
    content: str,
    ttl: int = 1,
    proxied: bool = False,
) -> CloudflareRecord:
    return CloudflareRecord(
        id_, name, type_, content, ttl, proxied, None, None, None
    )


class FakeClient:
    def __init__(self) -> None:
        self.zones = [zone("parent", "example.com"), zone("child", "sub.example.com")]
        self.records: dict[str, list[CloudflareRecord]] = {"parent": [], "child": []}
        self.deleted_zones: list[str] = []
        self.fail_record = False
        self.serial = 0

    def list_zones(self, credentials, refresh=None):
        return list(self.zones)

    def list_records(self, credentials, zone_id, refresh=None):
        return list(self.records.get(zone_id, []))

    def get_zone(self, credentials, zone_id, refresh=None):
        return next(item for item in self.zones if item.id == zone_id)

    def create_zone(self, credentials, *, account_id, name, refresh=None):
        created = zone("created", name)
        created = CloudflareZone(
            created.id,
            created.name,
            "pending",
            account_id,
            created.account_name,
            created.name_servers,
            None,
            None,
        )
        self.zones.append(created)
        self.records[created.id] = []
        return created

    def delete_zone(self, credentials, zone_id, refresh=None):
        self.deleted_zones.append(zone_id)
        self.zones = [item for item in self.zones if item.id != zone_id]

    def create_record(self, credentials, zone_id, payload, refresh=None):
        if self.fail_record:
            raise RuntimeError("write failed")
        self.serial += 1
        created = record(
            f"new-{self.serial}",
            str(payload["name"]),
            str(payload["type"]),
            str(payload["content"]),
            int(payload["ttl"]),
            bool(payload["proxied"]),
        )
        self.records[zone_id].append(created)
        return created

    def delete_record(self, credentials, zone_id, record_id, refresh=None):
        self.records[zone_id] = [
            item for item in self.records[zone_id] if item.id != record_id
        ]


class FakeService:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.audits: list[dict[str, object]] = []

    def provisioning_access(self, tenant_id):
        assert tenant_id == "t1"
        return CloudflareCredentials("token"), lambda: CloudflareCredentials("token"), "acct1"

    def record_provisioning(self, **values):
        self.audits.append(values)


def provisioner():
    service = FakeService()
    return CloudflareProvisioningService(service), service  # type: ignore[arg-type]


def test_plan_normalizes_and_uses_longest_zone_without_mutation() -> None:
    provisioning, service = provisioner()
    plan = provisioning.plan_for_tenant(
        "t1", domain="WWW.Sub.Example.COM.", ipv4="192.0.2.10", www=True, replace=False
    )
    assert plan["domain"] == "www.sub.example.com"
    assert plan["zone"]["id"] == "child"
    assert plan["zone_action"] == "reuse"
    assert [item["type"] for item in plan["desired_records"]] == ["A", "CNAME"]
    assert all(item["proxied"] is False for item in plan["desired_records"])
    assert service.client.records["child"] == []


def test_conflict_requires_replace_then_apply_is_idempotent() -> None:
    provisioning, service = provisioner()
    service.client.records["parent"] = [
        record("old", "example.com", "AAAA", "2001:db8::1"),
        record("txt", "example.com", "TXT", "unrelated"),
    ]
    arguments = dict(domain="example.com", ipv4="192.0.2.44", www=False)
    blocked = provisioning.plan_for_tenant("t1", **arguments, replace=False)
    assert [item["id"] for item in blocked["conflicts"]] == ["old"]
    with pytest.raises(CloudflareConflict):
        provisioning.apply_for_tenant("t1", **arguments, replace=False)

    applied = provisioning.apply_for_tenant("t1", **arguments, replace=True)
    assert applied["applied"] is True
    assert applied["drift"] is False
    assert [item.type for item in service.client.records["parent"]] == ["TXT", "A"]
    converged = provisioning.plan_for_tenant("t1", **arguments, replace=False)
    assert converged["drift"] is False
    provisioning.apply_for_tenant("t1", **arguments, replace=False)
    assert [item.type for item in service.client.records["parent"]].count("A") == 1
    assert len(service.audits) == 2


def test_new_zone_is_compensated_when_record_write_fails() -> None:
    provisioning, service = provisioner()
    service.client.fail_record = True
    with pytest.raises(RuntimeError, match="write failed"):
        provisioning.apply_for_tenant(
            "t1", domain="new.test", ipv4="192.0.2.5", www=False, replace=False
        )
    assert service.client.deleted_zones == ["created"]
    assert service.audits == []


@pytest.mark.parametrize("domain,ipv4", [("bad", "192.0.2.1"), ("example.com", "::1")])
def test_validation(domain: str, ipv4: str) -> None:
    provisioning, _service = provisioner()
    with pytest.raises(CloudflareValidation):
        provisioning.plan_for_tenant(
            "t1", domain=domain, ipv4=ipv4, www=False, replace=False
        )
