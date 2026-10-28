"""Contrato HTTP del gestor de certificados (A.SPEC 0035)."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.domains.backend.certificates.config import CertificatesConfig
from plugins.domains.backend.certificates.service import CertificatesService
from plugins.domains.backend.router import _get_certificate_service
from plugins.domains.backend.router import router as domains_router
from systutor.api.deps import get_db_session
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext

DEFAULT = "cf-mail"
BASE = "/api/v1/plugins/domains/certificates"


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def get(self, model, key):
        return None

    def add(self, row) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def write_state(path, certs) -> None:
    now = iso(datetime.now(UTC))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "generated_at": now, "agent": {"last_seen": now}, "certificates": certs}
        ),
        encoding="utf-8",
    )


def cert(hostname: str) -> dict:
    now = datetime.now(UTC)
    return {
        "hostname": hostname,
        "name": hostname,
        "source": "certbot",
        "deployment": "default" if hostname == DEFAULT else "sni",
        "status": "active",
        "sans": [hostname],
        "issuer": "CN=Test",
        "not_before": iso(now - timedelta(days=1)),
        "not_after": iso(now + timedelta(days=90)),
        "cert_path": f"/etc/ssl/spanel/mail/{hostname}/fullchain.pem",
        "key_path": f"/etc/ssl/spanel/mail/{hostname}/privkey.pem",
        "last_checked_at": iso(now),
    }


def config_for(tmp_path) -> CertificatesConfig:
    return CertificatesConfig(
        state_path=tmp_path / "state.json",
        requests_dir=tmp_path / "requests",
        jobs_dir=tmp_path / "jobs",
        default_name=DEFAULT,
    )


@pytest.fixture()
def api(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    write_state(
        config.state_path,
        [cert(DEFAULT), cert("mail.systutor.com"), cert("mail.other.com")],
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(domains_router, prefix="/api/v1/plugins/domains")

    rows = [
        OwnedSite("s1", "t1", "site-1", "systutor.com"),
        OwnedSite("s2", "t2", "site-2", "other.com"),
    ]

    def provider(db, tenant_id):
        return rows if tenant_id is None else [row for row in rows if row.tenant_id == tenant_id]

    register_site_provider(app, "hosting", provider)
    service = CertificatesService(FakeDb(), app, config=config)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_audit", lambda *args, **kwargs: None)

    def build(permissions: tuple[str, ...], tenant_id: str = "t1") -> TestClient:
        user = SimpleNamespace(id="user-1", tenant_id=tenant_id, branch_id=None)
        context = TenantContext(tenant_id, None, "user-1", permissions, None)
        app.dependency_overrides[get_db_session] = lambda: FakeDb()
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_tenant_context] = lambda: context
        app.dependency_overrides[_get_certificate_service] = lambda: service
        return TestClient(app)

    return build, config


def test_list_filters_tenant_and_requires_read(api):
    build, _ = api
    response = build(("domains.certificates.read",)).get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert [item["hostname"] for item in body["items"]] == ["mail.systutor.com"]
    assert body["stale"] is False

    assert build(()).get(BASE).status_code == 403


def test_detail_hides_other_tenant_and_global(api):
    build, _ = api
    client = build(("domains.certificates.read",))
    assert client.get(f"{BASE}/mail.systutor.com").status_code == 200
    assert client.get(f"{BASE}/mail.other.com").status_code == 404
    assert client.get(f"{BASE}/{DEFAULT}").status_code == 404


def test_platform_admin_sees_global(api):
    build, _ = api
    client = build(("domains.certificates.read", "core.platform.admin"))
    body = client.get(BASE).json()
    assert {item["hostname"] for item in body["items"]} == {
        DEFAULT,
        "mail.systutor.com",
        "mail.other.com",
    }


def test_request_requires_manage_and_returns_202(api):
    build, config = api
    payload = {"action": "ensure", "reason": "manual"}

    assert build(("domains.certificates.read",)).post(
        f"{BASE}/mail.systutor.com/request", json=payload
    ).status_code == 403

    response = build(
        ("domains.certificates.read", "domains.certificates.manage")
    ).post(f"{BASE}/mail.systutor.com/request", json=payload)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    assert body["hostname"] == "mail.systutor.com"
    request_path = config.requests_dir / f"{body['job_id']}.json"
    assert request_path.exists()
    assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
    assert body["poll_url"].endswith(
        f"/api/v1/plugins/domains/certificates/mail.systutor.com/jobs/{body['job_id']}"
    )


def test_second_request_returns_409_with_existing_job(api):
    build, _ = api
    client = build(("domains.certificates.read", "domains.certificates.manage"))
    first = client.post(f"{BASE}/mail.systutor.com/request", json={"action": "ensure"})
    assert first.status_code == 202
    job_id = first.json()["job_id"]

    second = client.post(f"{BASE}/mail.systutor.com/request", json={"action": "force"})
    assert second.status_code == 409
    body = second.json()
    assert body["job_id"] == job_id
    assert body["error"]["code"] == "domains.certificate_job_active"


def test_job_endpoint_returns_queued(api):
    build, _ = api
    client = build(("domains.certificates.read", "domains.certificates.manage"))
    job_id = client.post(
        f"{BASE}/mail.systutor.com/request", json={"action": "ensure"}
    ).json()["job_id"]

    response = client.get(f"{BASE}/mail.systutor.com/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert client.get(f"{BASE}/mail.other.com/jobs/{job_id}").status_code == 404
    assert client.get(f"{BASE}/mail.systutor.com/jobs/not-a-job").status_code == 422


def test_cf_mail_request_is_conflict(api):
    build, _ = api
    client = build(
        ("domains.certificates.read", "domains.certificates.manage", "core.platform.admin")
    )
    response = client.post(f"{BASE}/{DEFAULT}/request", json={"action": "ensure"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "domains.certificate_global"


def test_invalid_hostname_and_extra_payload_are_rejected(api):
    build, _ = api
    client = build(("domains.certificates.read", "domains.certificates.manage"))
    assert client.get(f"{BASE}/bad_host!").status_code == 422
    response = client.post(
        f"{BASE}/mail.systutor.com/request",
        json={"action": "ensure", "args": ["--force-renewal"]},
    )
    assert response.status_code == 422


def test_refresh_requires_manage_and_writes_marker(api):
    build, config = api
    payload = {}
    assert build(("domains.certificates.read",)).post(
        f"{BASE}/refresh", json=payload
    ).status_code == 403

    response = build(
        ("domains.certificates.read", "domains.certificates.manage")
    ).post(f"{BASE}/refresh", json=payload)
    assert response.status_code == 200
    assert response.json()["requested"] is True
    assert len(list(config.requests_dir.glob("refresh-*.json"))) == 1


def test_state_with_private_key_is_not_served(api, tmp_path):
    build, config = api
    config.state_path.write_text(
        '{"certificates": [], "note": "-----BEGIN PRIVATE KEY-----"}',
        encoding="utf-8",
    )
    response = build(("domains.certificates.read",)).get(BASE)
    assert response.status_code == 503
    assert "PRIVATE KEY" not in response.text
