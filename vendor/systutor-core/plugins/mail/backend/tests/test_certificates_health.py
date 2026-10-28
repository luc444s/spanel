"""Salud, alertas y acciones administrativas (A.SPEC 0036)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.mail.backend.certificates.config import CertificatesConfig
from plugins.mail.backend.certificates.router import _get_certificate_service
from plugins.mail.backend.certificates.router import router as certificates_router
from plugins.mail.backend.certificates.service import CertificatesService
from systutor.api.deps import get_db_session
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext

DEFAULT = "cf-mail"
BASE = "/api/v1/plugins/mail/certificates"
ADMIN = ("mail.certificates.read", "mail.certificates.manage", "core.platform.admin")
TENANT = ("mail.certificates.read",)


class FakeDb:
    def get(self, model, key):
        return None

    def add(self, row) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def cert(hostname: str, *, days: int = 90, **extra) -> dict:
    now = datetime.now(UTC)
    payload = {
        "hostname": hostname,
        "name": hostname,
        "source": "certbot",
        "deployment": "default" if hostname == DEFAULT else "sni",
        "status": "active",
        "sans": [hostname],
        "issuer": "CN=Test",
        "not_before": iso(now - timedelta(days=1)),
        "not_after": iso(now + timedelta(days=days)),
        "serial": "ABC123",
        "cert_path": f"/etc/ssl/spanel/mail/{hostname}/fullchain.pem",
        "key_path": f"/etc/ssl/spanel/mail/{hostname}/privkey.pem",
        "last_checked_at": iso(now),
    }
    payload.update(extra)
    return payload


def health_entry(hostname: str, *, days: int = 60, ok: bool = True, **extra) -> dict:
    payload = {
        "hostname": hostname,
        "serial": "ABC123",
        "served_serial": "ABC123",
        "no_sni_serial": "CFSERIAL",
        "not_after": iso(datetime.now(UTC) + timedelta(days=days)),
        "days_left": days,
        "sni": {"993": True, "465": True, "587": True},
        "no_sni": True,
        "ok": ok,
        "last_verified_at": iso(datetime.now(UTC)),
        "last_ok_at": iso(datetime.now(UTC)) if ok else None,
        "consecutive_failures": 0 if ok else 2,
        "last_error": None if ok else "probe falló",
    }
    payload.update(extra)
    return payload


def write_state(path, certificates, health=None) -> None:
    now = iso(datetime.now(UTC))
    payload = {
        "version": 1,
        "generated_at": now,
        "agent": {"last_seen": now},
        "certificates": certificates,
    }
    if health is not None:
        payload["health"] = health
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_alerts(path, alerts) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"generated_at": iso(datetime.now(UTC)), "alerts": alerts}),
        encoding="utf-8",
    )


def config_for(tmp_path) -> CertificatesConfig:
    return CertificatesConfig(
        state_path=tmp_path / "state.json",
        alerts_path=tmp_path / "alerts.json",
        requests_dir=tmp_path / "requests",
        jobs_dir=tmp_path / "jobs",
        default_name=DEFAULT,
    )


@pytest.fixture()
def api(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    health = {
        "generated_at": iso(datetime.now(UTC)),
        "agent": {"state": "degraded", "last_run_at": iso(datetime.now(UTC))},
        "default": {"name": DEFAULT, "serial": "CFSERIAL", "no_sni_serial": "CFSERIAL", "ok": True},
        "certificates": [
            health_entry("mail.systutor.com", ok=False, days=5, consecutive_failures=2),
            health_entry("mail.other.com"),
        ],
    }
    write_state(
        config.state_path,
        [cert(DEFAULT), cert("mail.systutor.com", days=5), cert("mail.other.com")],
        health,
    )
    write_alerts(
        config.alerts_path,
        [
            {
                "code": "certificate_expiry_critical",
                "severity": "critical",
                "hostname": "mail.systutor.com",
                "message": "Vence en 5 días",
                "created_at": iso(datetime.now(UTC)),
            },
            {
                "code": "postfix_failure",
                "severity": "critical",
                "hostname": "mail.other.com",
                "message": "Postfix falla",
                "created_at": iso(datetime.now(UTC)),
            },
            {
                "code": "agent_stale",
                "severity": "critical",
                "hostname": None,
                "message": "Agente viejo",
                "created_at": iso(datetime.now(UTC)),
            },
        ],
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(certificates_router, prefix="/api/v1/plugins/mail")

    rows = [
        OwnedSite("s1", "t1", "site-1", "systutor.com"),
        OwnedSite("s2", "t2", "site-2", "other.com"),
    ]

    def provider(db, tenant_id):
        return rows if tenant_id is None else [row for row in rows if row.tenant_id == tenant_id]

    register_site_provider(app, "hosting", provider)
    service = CertificatesService(FakeDb(), app, config=config)
    monkeypatch.setattr(service, "_audit", lambda *args, **kwargs: None)

    def build(permissions, tenant_id="t1") -> TestClient:
        user = SimpleNamespace(id="user-1", tenant_id=tenant_id, branch_id=None)
        context = TenantContext(tenant_id, None, "user-1", permissions, None)
        app.dependency_overrides[get_db_session] = lambda: FakeDb()
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_tenant_context] = lambda: context
        app.dependency_overrides[_get_certificate_service] = lambda: service
        return TestClient(app)

    return build, config


# ---- lectura ------------------------------------------------------------


def test_health_is_filtered_per_tenant_and_hides_cf_mail(api):
    build, _ = api
    tenant = build(TENANT).get(f"{BASE}/health")
    assert tenant.status_code == 200
    body = tenant.json()
    assert [item["hostname"] for item in body["certificates"]] == ["mail.systutor.com"]
    assert body["default"] is None
    assert body["agent_state"] == "degraded"

    other = build(TENANT, tenant_id="t2").get(f"{BASE}/health").json()
    assert [item["hostname"] for item in other["certificates"]] == ["mail.other.com"]
    assert other["default"] is None


def test_admin_sees_default_and_all_health(api):
    build, _ = api
    body = build(ADMIN).get(f"{BASE}/health").json()
    assert {item["hostname"] for item in body["certificates"]} == {
        "mail.systutor.com",
        "mail.other.com",
    }
    assert body["default"]["name"] == DEFAULT
    assert body["default"]["ok"] is True
    assert body["critical_count"] >= 1


def test_alerts_are_filtered_and_global_alert_is_admin_only(api):
    build, _ = api
    tenant = build(TENANT).get(f"{BASE}/alerts")
    assert tenant.status_code == 200
    codes = [alert["code"] for alert in tenant.json()["alerts"]]
    assert codes == ["certificate_expiry_critical"]

    other = build(TENANT, tenant_id="t2").get(f"{BASE}/alerts").json()
    assert [alert["hostname"] for alert in other["alerts"]] == ["mail.other.com"]

    admin = build(ADMIN).get(f"{BASE}/alerts").json()
    assert {alert["code"] for alert in admin["alerts"]} == {
        "certificate_expiry_critical",
        "postfix_failure",
        "agent_stale",
    }


def test_health_without_agent_health_block_falls_back_to_inventory(api, tmp_path):
    build, config = api
    write_state(config.state_path, [cert("mail.systutor.com", days=5)])
    body = build(TENANT).get(f"{BASE}/health").json()
    item = body["certificates"][0]
    assert item["ok"] is None
    assert item["days_left"] is not None and item["days_left"] <= 5
    assert body["default"] is None


def test_health_requires_read_permission(api):
    build, _ = api
    assert build(()).get(f"{BASE}/health").status_code == 403
    assert build(()).get(f"{BASE}/alerts").status_code == 403


# ---- acciones administrativas ------------------------------------------


@pytest.mark.parametrize("action", ["verify", "sync", "install", "rollback"])
def test_tenant_cannot_run_admin_actions(api, action):
    build, config = api
    response = build(TENANT).post(
        f"{BASE}/mail.systutor.com/{action}", json={"reason": "manual"}
    )
    assert response.status_code == 403
    assert not config.requests_dir.exists() or not list(config.requests_dir.glob("*.json"))


@pytest.mark.parametrize("action", ["verify", "sync", "install", "rollback"])
def test_admin_can_queue_admin_action(api, action):
    build, config = api
    response = build(ADMIN).post(
        f"{BASE}/mail.systutor.com/{action}", json={"reason": "manual"}
    )
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    request_file = config.requests_dir / f"{body['job_id']}.json"
    payload = json.loads(request_file.read_text(encoding="utf-8"))
    assert payload["action"] == action
    assert payload["hostname"] == "mail.systutor.com"


def test_admin_cannot_install_or_rollback_global_default(api):
    build, _ = api
    client = build(ADMIN)
    assert client.post(f"{BASE}/{DEFAULT}/install", json={}).status_code == 409
    assert client.post(f"{BASE}/{DEFAULT}/rollback", json={}).status_code == 409
    assert client.post(f"{BASE}/{DEFAULT}/verify", json={}).status_code == 202


def test_tenant_admin_of_other_tenant_cannot_touch_foreign_hostname(api):
    build, _ = api
    response = build(
        ("mail.certificates.read", "mail.certificates.manage"), tenant_id="t2"
    ).post(f"{BASE}/mail.systutor.com/verify", json={})
    assert response.status_code == 404


def test_admin_action_rejects_active_job(api):
    build, _ = api
    client = build(ADMIN)
    first = client.post(f"{BASE}/mail.systutor.com/verify", json={})
    assert first.status_code == 202
    second = client.post(f"{BASE}/mail.systutor.com/sync", json={})
    assert second.status_code == 409
    assert second.json()["job_id"] == first.json()["job_id"]
