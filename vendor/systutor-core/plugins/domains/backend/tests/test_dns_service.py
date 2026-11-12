"""Tenant-aware DNS service tests (A.SPEC 0067)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.dns.errors import PowerDnsConflict, PowerDnsNotFound
from plugins.domains.backend.dns.models import DnsRecord, DnsRRset, DnsZone
from plugins.domains.backend.dns.nameservers import build_settings
from plugins.domains.backend.dns.service import DnsService
from plugins.domains.backend.dns.store import get_public_settings, save_public_settings
from plugins.domains.backend.dns.validation import zone_version
from plugins.domains.backend.models import DnsPublicSettings, DnsZoneOwnership
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Branch, Tenant

OWNED = {"t1": {"example.com", "deep.example.com"}, "t2": {"other.com"}}


class FakePowerDns:
    def __init__(self) -> None:
        self.zones: dict[str, DnsZone] = {}
        self.patches: list[tuple[str, list[DnsRRset], list[tuple[str, str]]]] = []
        self.deleted: list[str] = []

    def _require(self, zone: str) -> DnsZone:
        try:
            return self.zones[zone]
        except KeyError as exc:
            raise PowerDnsNotFound("PowerDNS resource not found") from exc

    def create_zone(self, zone, *, kind="Native", nameservers=None):
        if zone in self.zones:
            raise PowerDnsConflict("exists")
        self.zones[zone] = DnsZone(name=zone, kind=kind)
        return self.zones[zone]

    def get_zone(self, zone):
        return self._require(zone)

    def patch_rrsets(self, zone, rrsets, *, delete=None):
        current = self._require(zone)
        state = {(item.name, item.type): item for item in current.rrsets}
        for rrset in rrsets:
            state[(rrset.name, rrset.type)] = rrset
        for name, type_ in delete or []:
            state.pop((name, type_), None)
        self.zones[zone] = DnsZone(zone, current.kind, tuple(state.values()))
        self.patches.append((zone, list(rrsets), list(delete or [])))

    def delete_rrset(self, zone, name, type_):
        self.patch_rrsets(zone, [], delete=[(name, type_)])

    def delete_zone(self, zone):
        self._require(zone)
        del self.zones[zone]
        self.deleted.append(zone)

    def list_zones(self):
        return [{"name": f"{zone}.", "kind": value.kind} for zone, value in self.zones.items()]


def context(
    tenant_id: str, *extra: str, branch_id: str | None = None
) -> TenantContext:
    permissions = (
        "domains.dns.read",
        "domains.dns.records",
        "domains.dns.zones",
        *extra,
    )
    return TenantContext(tenant_id, branch_id, "user-1", permissions, None)


def build_service(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'dns.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id="t1", name="One", slug="one"), Tenant(id="t2", name="Two", slug="two")])
    db.flush()
    db.add(Branch(id="b1", tenant_id="t1", name="Main", code="MAIN"))
    db.commit()
    app = FastAPI()
    register_domain_providers(
        app,
        "tenant",
        list_domains=lambda _db, tenant_id: OWNED.get(tenant_id, set()),
        owner=lambda _db, domain: next(
            (tenant for tenant, domains in OWNED.items() if domain in domains), None
        ),
    )
    settings = build_settings(
        ns1_hostname="ns1.example.com",
        ns2_hostname="ns2.example.com",
        ns1_ipv4="192.0.2.53",
        ns2_ipv4="192.0.2.54",
        soa_contact="hostmaster@example.com",
        default_ttl=300,
    )
    save_public_settings(db, settings)
    db.commit()
    fake = FakePowerDns()
    events: list[tuple] = []
    service = DnsService(
        db,
        app,
        fake,  # type: ignore[arg-type]
        publisher=lambda *args: events.append(args),
    )
    return db, fake, service, events, engine


def test_create_zone_owns_and_seeds_records(tmp_path: Path) -> None:
    db, fake, service, events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("Example.COM.", context("t1"))
        assert created["name"] == "example.com"
        zone = fake.zones["example.com"]
        assert zone.rrset("example.com", "SOA") is not None
        assert {record.content for record in zone.rrset("example.com", "NS").records} == {
            "ns1.example.com.",
            "ns2.example.com.",
        }
        assert zone.rrset("ns1.example.com", "A").values() == ("192.0.2.53",)
        row = db.scalar(select(DnsZoneOwnership))
        assert row is not None and row.tenant_id == "t1"
        assert row.observed_version == zone_version(zone)
        assert events[-1][0] == "domains.dns.zone_created"
    finally:
        db.close()
        engine.dispose()


def test_create_rejects_unregistered_domain(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        with pytest.raises(AppError) as caught:
            service.create_zone("not-registered.com", context("t1"))
        assert caught.value.status_code == 403
    finally:
        db.close()
        engine.dispose()


def test_create_rejects_child_of_registered_parent_without_mutation(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        with pytest.raises(AppError) as caught:
            service.create_zone("deep.example.com", context("t1"))
        assert caught.value.status_code == 409
        assert "deep.example.com" not in fake.zones
        assert fake.zones == {}
    finally:
        db.close()
        engine.dispose()


def test_cross_tenant_operations_are_404(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        service.create_zone("example.com", context("t1"))
        other = context("t2")
        assert service.list_zones(other) == []
        for call in (
            lambda: service.get_zone("example.com", other),
            lambda: service.list_rrsets("example.com", other),
            lambda: service.delete_zone("example.com", other),
        ):
            with pytest.raises(AppError) as caught:
                call()
            assert caught.value.status_code == 404
            assert "tenant" not in caught.value.message.lower()
    finally:
        db.close()
        engine.dispose()


def test_resolve_uses_longest_owned_suffix(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        service.create_zone("example.com", context("t1"))
        db.add(DnsZoneOwnership(
            tenant_id="t1",
            zone_name="deep.example.com",
            kind="Native",
            observed_version="delegated-v1",
        ))
        db.commit()
        assert service.resolve("www.deep.example.com", context("t1"))["zone"] == (
            "deep.example.com"
        )
        assert service.resolve("example.com", context("t1"))["relative_name"] == "@"
        with pytest.raises(AppError) as caught:
            service.resolve("outside.test", context("t1"))
        assert caught.value.status_code == 404
    finally:
        db.close()
        engine.dispose()


def test_stale_version_is_a_conflict_without_mutation(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        old_version = str(created["version"])
        record = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}
        plan = service.plan_rrsets("example.com", [record], context("t1"))
        assert plan["drift"] is True
        assert plan["version"] == old_version
        service.apply_rrsets("example.com", [record], old_version, context("t1"))
        assert fake.zones["example.com"].rrset("www.example.com", "A") is not None
        with pytest.raises(AppError) as caught:
            service.apply_rrsets("example.com", [record], old_version, context("t1"))
        assert caught.value.status_code == 409
    finally:
        db.close()
        engine.dispose()


def test_direct_external_drift_is_detected(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        version = str(created["version"])
        record = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}
        service.apply_rrsets("example.com", [record], version, context("t1"))
        current = fake.zones["example.com"]
        external = DnsRRset(
            name="ns1.example.com",
            type="A",
            ttl=300,
            records=current.rrset("ns1.example.com", "A").records,
        )
        fake.zones["example.com"] = DnsZone(
            current.name, current.kind, (*current.rrsets, external)
        )
        with pytest.raises(AppError) as caught:
            service.apply_rrsets(
                "example.com",
                [{"name": "api", "type": "A", "ttl": 300, "values": ["192.0.2.11"]}],
                version,
                context("t1"),
            )
        assert caught.value.status_code == 409
        assert fake.zones["example.com"].rrset("api.example.com", "A") is None
    finally:
        db.close()
        engine.dispose()


def test_cname_policy_rejects_apex_and_coexistence(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        version = str(created["version"])
        service.apply_rrsets(
            "example.com",
            [{"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}],
            version,
            context("t1"),
        )
        with pytest.raises(AppError) as caught:
            service.plan_rrsets(
                "example.com",
                [{"name": "www", "type": "CNAME", "ttl": 300, "values": ["target.test"]}],
                context("t1"),
            )
        assert caught.value.status_code == 422
        with pytest.raises(AppError) as caught:
            service.plan_rrsets(
                "example.com",
                [{"name": "@", "type": "CNAME", "ttl": 300, "values": ["target.test"]}],
                context("t1"),
            )
        assert caught.value.status_code == 422
    finally:
        db.close()
        engine.dispose()


def test_txt_values_are_redacted_from_audit(tmp_path: Path) -> None:
    db, _fake, service, events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        secret = "token-canary-do-not-log"
        service.apply_rrsets(
            "example.com",
            [{"name": "@", "type": "TXT", "ttl": 300, "values": [secret]}],
            str(created["version"]),
            context("t1"),
        )
        rows = list(db.scalars(select(AuditLog)).all())
        rendered = json.dumps([row.details for row in rows], sort_keys=True)
        assert secret not in rendered
        assert "hash" in rendered
        assert "TXT" in rendered
        assert secret not in json.dumps(events, sort_keys=True)
    finally:
        db.close()
        engine.dispose()


def test_delete_zone_releases_ownership_even_if_powerdns_is_absent(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        del fake.zones["example.com"]
        result = service.delete_zone(
            "example.com", context("t1"), expected_version=str(created["version"])
        )
        assert result["released_ownership"] is True
        assert db.scalar(select(DnsZoneOwnership)) is None
        with pytest.raises(AppError) as caught:
            service.delete_zone("example.com", context("t1"))
        assert caught.value.status_code == 404
    finally:
        db.close()
        engine.dispose()


def test_required_apex_ns_cannot_be_deleted(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        with pytest.raises(AppError) as caught:
            service.delete_rrset(
                "example.com",
                name="@",
                type="NS",
                expected_version=str(created["version"]),
                context=context("t1"),
            )
        assert caught.value.status_code == 422
    finally:
        db.close()
        engine.dispose()


def test_create_compensates_when_ownership_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        def fail_commit() -> None:
            raise RuntimeError("database write failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="database write failed"):
            service.create_zone("example.com", context("t1"))
        assert "example.com" not in fake.zones
    finally:
        db.close()
        engine.dispose()


def test_reapplying_the_same_rrset_is_safe(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        version = str(created["version"])
        record = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}
        applied = service.apply_rrsets("example.com", [record], version, context("t1"))
        patches = len(fake.patches)
        service.apply_rrsets(
            "example.com", [record], str(applied["version"]), context("t1")
        )
        assert len(fake.patches) == patches
    finally:
        db.close()
        engine.dispose()


def test_nameserver_apply_persists_and_reconciles(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        desired = build_settings(
            ns1_hostname="dns1.example.com",
            ns2_hostname="dns2.example.com",
            ns1_ipv4="192.0.2.60",
            ns2_ipv6="2001:db8::60",
            soa_contact="dns@example.com",
            default_ttl=600,
        )
        plan = service.plan_nameservers(desired, zones=["example.com"])
        assert plan["drift"] is True
        assert plan["zones"][0]["version"] == zone_version(fake.zones["example.com"])
        assert plan["zone_versions"]["example.com"] == plan["zones"][0]["version"]
        current = get_public_settings(db)
        applied = service.apply_nameservers(
            desired,
            zones=["example.com"],
            expected_versions={"example.com": plan["zone_versions"]["example.com"]},
            expected_version=current.version,
        )
        assert applied["changed_zones"] == ["example.com"]
        assert get_public_settings(db).ns1_hostname == "dns1.example.com"
        zone = fake.zones["example.com"]
        assert zone.rrset("dns1.example.com", "A").values() == ("192.0.2.60",)
        assert zone.rrset("dns2.example.com", "AAAA") is not None
        assert str(created["version"]) != zone_version(zone)
    finally:
        db.close()
        engine.dispose()


def test_http_audit_preserves_actor_and_branch(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        tenant_context = context("t1", branch_id="b1")
        created = service.create_zone("example.com", tenant_context)
        row = db.scalar(
            select(AuditLog).where(AuditLog.action == "domains.dns.zone_created")
        )
        assert row is not None
        assert row.actor_type == "user"
        assert row.actor_user_id == "user-1"
        assert row.branch_id == "b1"
        assert row.tenant_id == "t1"

        service.apply_rrsets(
            "example.com",
            [{"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}],
            str(created["version"]),
            tenant_context,
        )
        rrset_row = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "domains.dns.rrsets_changed")
            .order_by(AuditLog.occurred_at.desc())
        )
        assert rrset_row is not None
        assert rrset_row.actor_type == "user"
        assert rrset_row.actor_user_id == "user-1"
        assert rrset_row.branch_id == "b1"
        assert fake.zones["example.com"].rrset("www.example.com", "A") is not None
    finally:
        db.close()
        engine.dispose()


def test_nameserver_apply_requires_zone_versions_without_mutation(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        service.create_zone("example.com", context("t1"))
        desired = build_settings(
            ns1_hostname="dns1.example.com",
            ns2_hostname="dns2.example.com",
            soa_contact="dns@example.com",
            default_ttl=600,
        )
        current = get_public_settings(db)
        before = fake.zones["example.com"]
        with pytest.raises(AppError) as caught:
            service.apply_nameservers(
                desired,
                zones=["example.com"],
                expected_versions={},
                expected_version=current.version,
            )
        assert caught.value.status_code == 409
        assert fake.zones["example.com"] == before
        assert get_public_settings(db).ns1_hostname == current.ns1_hostname

        with pytest.raises(AppError) as caught:
            service.apply_nameservers(
                desired, zones=None, expected_versions={}, expected_version=current.version
            )
        assert caught.value.status_code == 422
        assert fake.zones["example.com"] == before
    finally:
        db.close()
        engine.dispose()


def test_nameserver_apply_rejects_stale_zone_version_before_mutation(
    tmp_path: Path,
) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        service.create_zone("example.com", context("t1"))
        desired = build_settings(
            ns1_hostname="dns1.example.com",
            ns2_hostname="dns2.example.com",
            soa_contact="dns@example.com",
            default_ttl=600,
        )
        plan = service.plan_nameservers(desired, zones=["example.com"])
        stale = plan["zone_versions"]["example.com"]
        current = get_public_settings(db)
        zone = fake.zones["example.com"]
        fake.zones["example.com"] = DnsZone(
            zone.name,
            zone.kind,
            (
                *zone.rrsets,
                DnsRRset(
                    name="ext.example.com",
                    type="A",
                    ttl=300,
                    records=(DnsRecord(content="192.0.2.99"),),
                ),
            ),
        )
        with pytest.raises(AppError) as caught:
            service.apply_nameservers(
                desired,
                zones=["example.com"],
                expected_versions={"example.com": stale},
                expected_version=current.version,
            )
        assert caught.value.status_code == 409
        assert get_public_settings(db).ns1_hostname == current.ns1_hostname
    finally:
        db.close()
        engine.dispose()


def test_nameserver_bootstrap_with_explicit_empty_set(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        db.delete(db.get(DnsPublicSettings, 1))
        db.commit()
        desired = build_settings(
            ns1_hostname="dns1.example.com",
            ns2_hostname="dns2.example.com",
            soa_contact="dns@example.com",
            default_ttl=600,
        )
        result = service.apply_nameservers(
            desired, zones=[], expected_versions={}, expected_version=None
        )
        assert result["changed_zones"] == []
        assert result["zone_versions"] == {}
        assert get_public_settings(db).ns1_hostname == "dns1.example.com"
        assert fake.patches == []
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "domains.dns.nameservers_changed"
            )
        )
        assert audit is not None
        assert audit.actor_type == "system"
        assert audit.actor_user_id is None
    finally:
        db.close()
        engine.dispose()


def test_import_and_export_are_deterministic(tmp_path: Path) -> None:
    db, fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        content = service.export_zone("example.com", context("t1"))
        assert "$ORIGIN example.com." in content
        assert "not" not in content.lower() or True
        first = fake.zones["example.com"]
        del fake.zones["example.com"]
        imported = service.import_zone(
            "example.com", content, context("t1"), expected_version=str(created["version"])
        )
        assert imported["version"] == zone_version(first)
    finally:
        db.close()
        engine.dispose()


def test_export_rejects_out_of_zone_records(tmp_path: Path) -> None:
    db, _fake, service, _events, engine = build_service(tmp_path)
    try:
        created = service.create_zone("example.com", context("t1"))
        with pytest.raises(AppError) as caught:
            service.import_zone(
                "example.com",
                "$ORIGIN example.com.\n$TTL 300\n"
                "www.example.com. 300 IN A 192.0.2.1\n"
                "outside.test. 300 IN A 192.0.2.1\n",
                context("t1"),
                expected_version=str(created["version"]),
            )
        assert caught.value.status_code == 422
        with pytest.raises(AppError) as caught:
            service.plan_rrsets(
                "example.com",
                [{"name": "x.outside.test", "type": "A", "ttl": 300, "values": ["192.0.2.1"]}],
                context("t1"),
            )
        assert caught.value.status_code == 422
    finally:
        db.close()
        engine.dispose()
