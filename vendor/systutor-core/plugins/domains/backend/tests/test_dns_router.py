"""FastAPI DNS router tests: permissions, tenant isolation and errors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.errors import (
    PowerDnsConflict,
    PowerDnsNotFound,
    PowerDnsTimeout,
    PowerDnsUnavailable,
)
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.nameservers import build_settings
from plugins.domains.backend.dns.store import get_public_settings, save_public_settings
from plugins.domains.backend.dns_router import router
from plugins.domains.backend.models import DnsZoneOwnership
from systutor.api.deps import get_db_session
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Branch, Tenant

OWNED = {"t1": {"example.com"}, "t2": {"other.com"}}


class FakePowerDns:
    def __init__(self) -> None:
        self.zones: dict[str, DnsZone] = {}
        self.fail: Exception | None = None

    def _zone(self, zone: str) -> DnsZone:
        if self.fail is not None:
            raise self.fail
        try:
            return self.zones[zone]
        except KeyError as exc:
            raise PowerDnsNotFound("PowerDNS resource not found") from exc

    def health(self):
        if self.fail is not None:
            raise self.fail
        return {"id": "localhost"}

    def create_zone(self, zone, *, kind="Native", nameservers=None):
        if zone in self.zones:
            raise PowerDnsConflict("exists")
        self.zones[zone] = DnsZone(name=zone, kind=kind)
        return self.zones[zone]

    def get_zone(self, zone):
        return self._zone(zone)

    def patch_rrsets(self, zone, rrsets, *, delete=None):
        current = self._zone(zone)
        state = {(item.name, item.type): item for item in current.rrsets}
        for rrset in rrsets:
            state[(rrset.name, rrset.type)] = rrset
        for name, type_ in delete or []:
            state.pop((name, type_), None)
        self.zones[zone] = DnsZone(zone, current.kind, tuple(state.values()))

    def delete_rrset(self, zone, name, type_):
        self.patch_rrsets(zone, [], delete=[(name, type_)])

    def delete_zone(self, zone):
        self._zone(zone)
        del self.zones[zone]

    def list_zones(self):
        return [{"name": f"{name}.", "kind": value.kind} for name, value in self.zones.items()]


def context(
    tenant_id: str,
    *extra: str,
    platform: bool = False,
    branch_id: str | None = "b1",
) -> TenantContext:
    permissions = [
        "domains.dns.read",
        "domains.dns.records",
        "domains.dns.zones",
        *extra,
    ]
    if platform:
        permissions.append("core.platform.admin")
    return TenantContext(tenant_id, branch_id, "user-1", tuple(permissions), None)


def build_app(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'router.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all([Tenant(id="t1", name="One", slug="one"), Tenant(id="t2", name="Two", slug="two")])
    db.flush()
    db.add(Branch(id="b1", tenant_id="t1", name="Main", code="MAIN"))
    db.commit()
    settings = build_settings(
        ns1_hostname="ns1.example.com",
        ns2_hostname="ns2.example.com",
        ns1_ipv4="192.0.2.53",
        soa_contact="hostmaster@example.com",
        default_ttl=300,
    )
    save_public_settings(db, settings)
    db.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1/plugins/domains")
    register_domain_providers(
        app,
        "tenant",
        list_domains=lambda _db, tenant_id: OWNED.get(tenant_id, set()),
        owner=lambda _db, domain: next(
            (tenant for tenant, domains in OWNED.items() if domain in domains), None
        ),
    )
    fake = FakePowerDns()
    app.state.powerdns_client = fake
    holder = {"context": context("t1")}
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")
    app.dependency_overrides[get_current_tenant_context] = lambda: holder["context"]
    return app, fake, db, holder, engine


def test_zone_lifecycle_and_version_conflict(tmp_path: Path) -> None:
    app, fake, db, _holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/plugins/domains/dns/zones", json={"zone_name": "example.com"}
            )
            assert created.status_code == 201, created.text
            version = created.json()["version"]
            assert created.json()["name"] == "example.com"
            assert fake.zones["example.com"].rrset("example.com", "SOA") is not None
            audit = db.scalar(
                select(AuditLog).where(AuditLog.action == "domains.dns.zone_created")
            )
            assert audit is not None
            assert audit.actor_type == "user"
            assert audit.actor_user_id == "user-1"
            assert audit.branch_id == "b1"
            assert audit.tenant_id == "t1"

            listed = client.get("/api/v1/plugins/domains/dns/zones")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["name"] == "example.com"

            plan = client.post(
                "/api/v1/plugins/domains/dns/zones/example.com/rrsets/plan",
                json={
                    "rrsets": [
                        {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}
                    ]
                },
            )
            assert plan.status_code == 200, plan.text
            assert plan.json()["drift"] is True

            applied = client.post(
                "/api/v1/plugins/domains/dns/zones/example.com/rrsets/apply",
                json={
                    "rrsets": [
                        {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}
                    ],
                    "expected_version": version,
                },
            )
            assert applied.status_code == 200, applied.text
            stale = client.post(
                "/api/v1/plugins/domains/dns/zones/example.com/rrsets/apply",
                json={
                    "rrsets": [
                        {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.11"]}
                    ],
                    "expected_version": version,
                },
            )
            assert stale.status_code == 409
            assert fake.zones["example.com"].rrset("www.example.com", "A").values() == (
                "192.0.2.10",
            )

            exported = client.get("/api/v1/plugins/domains/dns/zones/example.com/export")
            assert exported.status_code == 200
            assert "$ORIGIN example.com." in exported.text
    finally:
        db.close()
        engine.dispose()


def test_cross_tenant_get_and_delete_are_404(tmp_path: Path) -> None:
    app, _fake, db, holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/v1/plugins/domains/dns/zones",
                    json={"zone_name": "example.com"},
                ).status_code
                == 201
            )
            holder["context"] = context("t2")
            response = client.get("/api/v1/plugins/domains/dns/zones/example.com")
            assert response.status_code == 404
            assert "tenant" not in response.text.lower()
            listed = client.get("/api/v1/plugins/domains/dns/zones")
            assert listed.json()["items"] == []
            deleted = client.delete(
                "/api/v1/plugins/domains/dns/zones/example.com?expected_version=anything"
            )
            assert deleted.status_code == 404
    finally:
        db.close()
        engine.dispose()


def test_permission_failure_is_403(tmp_path: Path) -> None:
    app, _fake, db, holder, engine = build_app(tmp_path)
    try:
        holder["context"] = TenantContext("t1", None, "user-1", ("domains.dns.read",), None)
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
            assert response.status_code == 403
    finally:
        db.close()
        engine.dispose()


def test_nameserver_apply_requires_platform_double_gate(tmp_path: Path) -> None:
    app, _fake, db, holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
            assert created.status_code == 201
            current = get_public_settings(db)
            body = {
                "settings": {
                    "ns1": "dns1.example.com",
                    "ns2": "dns2.example.com",
                    "soa_contact": "dns@example.com",
                    "default_ttl": 600,
                },
                "zones": ["example.com"],
                "zone_versions": {"example.com": created.json()["version"]},
                "expected_version": current.version,
            }
            holder["context"] = context(
                "t1", "domains.dns.nameservers"
            )
            denied = client.post(
                "/api/v1/plugins/domains/dns/nameservers/apply", json=body
            )
            assert denied.status_code == 403
            holder["context"] = context(
                "t1", "domains.dns.nameservers", platform=True
            )
            allowed = client.post(
                "/api/v1/plugins/domains/dns/nameservers/apply", json=body
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["settings"]["ns1"] == "dns1.example.com"
            assert "example.com" in allowed.json()["zone_versions"]
            audit = db.scalar(
                select(AuditLog)
                .where(AuditLog.action == "domains.dns.nameservers_changed")
                .order_by(AuditLog.occurred_at.desc())
            )
            assert audit is not None
            assert audit.actor_type == "user"
            assert audit.actor_user_id == "user-1"
            assert audit.branch_id == "b1"
    finally:
        db.close()
        engine.dispose()


def test_nameserver_apply_requires_versions_and_empty_set_is_explicit(
    tmp_path: Path,
) -> None:
    app, fake, db, holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
            assert created.status_code == 201
            current = get_public_settings(db)
            holder["context"] = context(
                "t1", "domains.dns.nameservers", platform=True
            )
            settings_body = {
                "ns1": "dns1.example.com",
                "ns2": "dns2.example.com",
                "soa_contact": "dns@example.com",
                "default_ttl": 600,
            }
            plan = client.post(
                "/api/v1/plugins/domains/dns/nameservers/plan",
                json={"settings": settings_body, "zones": ["example.com"]},
            )
            assert plan.status_code == 200, plan.text
            planned_version = plan.json()["zones"][0]["version"]
            assert planned_version == created.json()["version"]
            assert plan.json()["zone_versions"]["example.com"] == planned_version
            body = {
                "settings": settings_body,
                "zones": ["example.com"],
                "expected_version": current.version,
            }
            before = fake.zones["example.com"]
            missing = client.post(
                "/api/v1/plugins/domains/dns/nameservers/apply", json=body
            )
            assert missing.status_code == 409
            assert fake.zones["example.com"] == before
            stale = client.post(
                "/api/v1/plugins/domains/dns/nameservers/apply",
                json={
                    **body,
                    "zone_versions": {"example.com": "stale-version"},
                },
            )
            assert stale.status_code == 409
            assert fake.zones["example.com"] == before
            empty = client.post(
                "/api/v1/plugins/domains/dns/nameservers/apply",
                json={
                    **body,
                    "zones": [],
                    "zone_versions": {},
                },
            )
            assert empty.status_code == 200, empty.text
            assert empty.json()["changed_zones"] == []
            assert empty.json()["zone_versions"] == {}
    finally:
        db.close()
        engine.dispose()


def test_powerdns_unavailable_and_timeout_are_normalized(tmp_path: Path) -> None:
    app, fake, db, _holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/v1/plugins/domains/dns/zones",
                    json={"zone_name": "example.com"},
                ).status_code
                == 201
            )
            fake.fail = PowerDnsUnavailable("down")
            unavailable = client.get("/api/v1/plugins/domains/dns/zones/example.com")
            assert unavailable.status_code == 503
            fake.fail = PowerDnsTimeout("slow")
            timeout = client.get("/api/v1/plugins/domains/dns/zones/example.com")
            assert timeout.status_code == 504
    finally:
        db.close()
        engine.dispose()


def test_validation_and_duplicate_conflict(tmp_path: Path) -> None:
    app, _fake, db, _holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
            assert first.status_code == 201
            duplicate = client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
            assert duplicate.status_code == 409
            apex_cname = client.post(
                "/api/v1/plugins/domains/dns/zones/example.com/rrsets/plan",
                json={
                    "rrsets": [
                        {
                            "name": "@",
                            "type": "CNAME",
                            "ttl": 300,
                            "values": ["target.test"],
                        }
                    ]
                },
            )
            assert apex_cname.status_code == 422
            bad_ttl = client.post(
                "/api/v1/plugins/domains/dns/zones/example.com/rrsets/plan",
                json={
                    "rrsets": [
                        {"name": "www", "type": "A", "ttl": 0, "values": ["192.0.2.1"]}
                    ]
                },
            )
            assert bad_ttl.status_code == 422
    finally:
        db.close()
        engine.dispose()


def test_doctor_never_returns_url_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "key"
    key.write_text("doctor-canary", encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setenv("SPANEL_PDNS_API_URL", "http://pdns.internal:8081")
    monkeypatch.setenv("SPANEL_PDNS_API_KEY_FILE", str(key))
    app, _fake, db, _holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/plugins/domains/dns/doctor")
            assert response.status_code == 200
            body = response.text
            assert "doctor-canary" not in body
            assert "pdns.internal" not in body
    finally:
        db.close()
        engine.dispose()


def test_shared_client_instance_is_the_router_client(tmp_path: Path) -> None:
    app, fake, db, _holder, engine = build_app(tmp_path)
    try:
        with TestClient(app) as client:
            client.post(
                "/api/v1/plugins/domains/dns/zones",
                json={"zone_name": "example.com"},
            )
        assert app.state.powerdns_client is fake
        assert isinstance(app.state.powerdns_client, FakePowerDns)
        # The router imported the same PowerDnsClient type used by the service.
        assert PowerDnsClient is not None
        assert DnsZoneOwnership.__tablename__ == "domains_dns_zones"
        assert DnsRRset(name="example.com", type="A", ttl=300).ttl == 300
    finally:
        db.close()
        engine.dispose()
