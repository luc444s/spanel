"""Inventario, jobs y servicio del gestor de certificados (A.SPEC 0035)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI

from plugins.mail.backend.certificates import service as service_module
from plugins.mail.backend.certificates.config import CertificatesConfig
from plugins.mail.backend.certificates.inventory import InventoryError, load_inventory
from plugins.mail.backend.certificates.jobs import (
    ActiveJobError,
    JobStore,
    atomic_write_json,
)
from plugins.mail.backend.certificates.models import Certificate
from plugins.mail.backend.certificates.schemas import CertificateItem
from plugins.mail.backend.certificates.service import CertificatesService
from plugins.mail.backend.certificates.validation import (
    CertificateValidationError,
    validate_emission_request,
)
from systutor.contracts.domain_ownership import OwnedSite, register_site_provider
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.context import TenantContext

DEFAULT = "cf-mail"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def write_state(
    path: Path,
    certificates: list[dict],
    *,
    generated_at: str | None = None,
    interval: int | None = None,
) -> None:
    payload = {
        "version": 1,
        "generated_at": generated_at or iso(datetime.now(UTC)),
        "agent": {"last_seen": generated_at or iso(datetime.now(UTC))},
        "certificates": certificates,
    }
    if interval is not None:
        payload["agent"]["interval_seconds"] = interval
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def cert_payload(hostname: str, *, days: int = 120, **extra) -> dict:
    now = datetime.now(UTC)
    payload = {
        "hostname": hostname,
        "name": hostname,
        "source": "certbot",
        "deployment": "sni" if hostname != DEFAULT else "default",
        "status": "active",
        "sans": [hostname],
        "issuer": "CN=Test CA",
        "subject": f"CN={hostname}",
        "serial": "ABC123",
        "fingerprint": "aa:bb",
        "not_before": iso(now - timedelta(days=10)),
        "not_after": iso(now + timedelta(days=days)),
        "cert_path": f"/etc/ssl/spanel/mail/{hostname}/fullchain.pem",
        "key_path": f"/etc/ssl/spanel/mail/{hostname}/privkey.pem",
        "last_checked_at": iso(now),
    }
    payload.update(extra)
    return payload


def config_for(tmp_path: Path) -> CertificatesConfig:
    return CertificatesConfig(
        state_path=tmp_path / "state.json",
        requests_dir=tmp_path / "requests",
        jobs_dir=tmp_path / "jobs",
        default_name=DEFAULT,
        renew_days=30,
        state_max_age_seconds=900,
    )


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def app_with_sites(*sites: OwnedSite) -> FastAPI:
    app = FastAPI()

    def provider(db, tenant_id):
        return (
            sites
            if tenant_id is None
            else [site for site in sites if site.tenant_id == tenant_id]
        )

    register_site_provider(app, "hosting", provider)
    return app


def ctx(tenant_id: str = "t1", permissions: tuple[str, ...] = ()) -> TenantContext:
    return TenantContext(tenant_id, None, "user-1", permissions, None)


@pytest.fixture()
def fake_audit(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    def record(db, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(service_module, "record_audit", record)
    return calls


# ---- inventario ---------------------------------------------------------


def test_state_parses_default_and_sni_without_keys(tmp_path):
    config = config_for(tmp_path)
    write_state(
        config.state_path,
        [cert_payload(DEFAULT), cert_payload("mail.systutor.com")],
    )

    inventory = load_inventory(config)

    assert [cert.hostname for cert in inventory.certificates] == [DEFAULT, "mail.systutor.com"]
    default = inventory.find(DEFAULT)
    assert default is not None
    assert default.scope == "global"
    assert default.domain is None
    assert default.deployment == "default"
    sni = inventory.find("mail.systutor.com")
    assert sni is not None
    assert sni.scope == "tenant"
    assert sni.domain == "systutor.com"
    assert inventory.stale is False
    serialized = json.dumps(default.to_dict())
    assert "PRIVATE KEY" not in serialized
    assert default.key_path and default.key_path.endswith("privkey.pem")


def test_state_with_private_key_is_rejected(tmp_path):
    config = config_for(tmp_path)
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(
        '{"certificates": [], "note": "-----BEGIN PRIVATE KEY-----"}',
        encoding="utf-8",
    )

    with pytest.raises(InventoryError):
        load_inventory(config)


def test_missing_state_is_stale_and_empty(tmp_path):
    inventory = load_inventory(config_for(tmp_path))
    assert inventory.certificates == []
    assert inventory.stale is True
    assert inventory.error == "state_missing"


def test_old_state_is_stale(tmp_path):
    config = config_for(tmp_path)
    old = iso(datetime.now(UTC) - timedelta(hours=2))
    write_state(config.state_path, [cert_payload("mail.systutor.com")], generated_at=old)

    assert load_inventory(config).stale is True


def test_agent_interval_in_health_extends_the_staleness_window(tmp_path):
    config = config_for(tmp_path)
    recent = iso(datetime.now(UTC) - timedelta(minutes=18))
    payload = {
        "version": 1,
        "generated_at": recent,
        "agent": {"last_seen": recent},
        "health": {"agent": {"interval_seconds": 600}},
        "certificates": [cert_payload("mail.systutor.com")],
    }
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_inventory(config).stale is False


def test_agent_interval_extends_the_staleness_window(tmp_path):
    config = config_for(tmp_path)
    recent = iso(datetime.now(UTC) - timedelta(minutes=18))
    write_state(
        config.state_path,
        [cert_payload("mail.systutor.com")],
        generated_at=recent,
        interval=600,
    )

    # 2 × 600 s = 1200 s > los 900 s configurados.
    assert load_inventory(config).stale is False


def test_health_is_recomputed_from_dates(tmp_path):
    config = config_for(tmp_path)
    write_state(
        config.state_path,
        [
            cert_payload("mail.expiring.com", days=4),
            cert_payload("mail.expired.com", days=-1),
            cert_payload("mail.missing.com", status="missing", not_after=None),
        ],
    )
    inventory = load_inventory(config)
    assert inventory.find("mail.expiring.com").status == "expiring"
    assert inventory.find("mail.expired.com").status == "expired"
    assert inventory.find("mail.missing.com").status == "missing"


def test_certificate_derives_domain_only_for_mail_hostname():
    now = datetime.now(UTC)
    certificate = Certificate.from_payload(
        {
            "hostname": "smtp.example.com",
            "scope": "tenant",
            "domain": "ignored.example",
            "source": "static",
            "deployment": "none",
            "status": "error",
        },
        default_name=DEFAULT,
        now=now,
        renew_days=30,
    )
    assert certificate.domain == "ignored.example"

    mail = Certificate.from_payload(
        {"hostname": "mail.example.com", "scope": "tenant"},
        default_name=DEFAULT,
        now=now,
        renew_days=30,
    )
    assert mail.domain == "example.com"


# ---- jobs ---------------------------------------------------------------


def test_create_request_is_atomic_private_and_detects_active_job(tmp_path):
    config = config_for(tmp_path)
    store = JobStore(config)

    job_id = store.create_request(
        "mail.systutor.com", action="ensure", requested_by="user-1", reason="manual"
    )
    path = config.requests_dir / f"{job_id}.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["hostname"] == "mail.systutor.com"
    assert payload["action"] == "ensure"
    assert not list(config.requests_dir.glob("*.tmp"))

    with pytest.raises(ActiveJobError) as exc:
        store.create_request(
            "mail.systutor.com", action="force", requested_by="user-1", reason=None
        )
    assert exc.value.job_id == job_id


def test_job_lookup_reads_jobs_dir_and_request_queue(tmp_path):
    config = config_for(tmp_path)
    store = JobStore(config)
    job_id = store.create_request(
        "mail.systutor.com", action="force", requested_by="u", reason=None
    )

    queued = store.job("mail.systutor.com", job_id)
    assert queued is not None
    assert queued.state == "queued"
    assert queued.action == "force"

    atomic_write_json(
        config.jobs_dir / f"{job_id}.json",
        {
            "id": job_id,
            "hostname": "mail.systutor.com",
            "state": "succeeded",
            "message": "certificado instalado",
            "finished_at": iso(datetime.now(UTC)),
        },
        mode=0o640,
    )
    done = store.job("mail.systutor.com", job_id)
    assert done is not None
    assert done.state == "succeeded"
    assert store.active_job("mail.systutor.com") is None

    assert store.job("mail.other.com", job_id) is None


def test_job_never_returns_private_key_material(tmp_path):
    config = config_for(tmp_path)
    store = JobStore(config)
    job_id = store.create_request(
        "mail.systutor.com", action="ensure", requested_by="u", reason=None
    )
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    (config.jobs_dir / f"{job_id}.json").write_text(
        json.dumps(
            {
                "id": job_id,
                "hostname": "mail.systutor.com",
                "state": "failed",
                "error": "-----BEGIN PRIVATE KEY----- abc",
                "message": "---BEGIN RSA PRIVATE KEY---",
            }
        ),
        encoding="utf-8",
    )
    job = store.job("mail.systutor.com", job_id)
    assert job is not None
    assert job.error == "Detalle no disponible"
    assert job.message == "Detalle no disponible"


def test_create_request_rejects_invalid_hostname(tmp_path):
    store = JobStore(config_for(tmp_path))
    with pytest.raises(CertificateValidationError):
        store.create_request("../../etc/passwd", action="ensure", requested_by="u", reason=None)


def test_payload_rejects_extra_certbot_flags():
    with pytest.raises(CertificateValidationError):
        validate_emission_request(
            {
                "hostname": "mail.systutor.com",
                "action": "ensure",
                "args": ["--force-renewal"],
            }
        )
    with pytest.raises(CertificateValidationError):
        validate_emission_request({"hostname": "mail.systutor.com", "action": "delete"})
    valid = validate_emission_request(
        {"hostname": "MAIL.Systutor.com.", "action": "ensure", "reason": "manual"}
    )
    assert valid["hostname"] == "mail.systutor.com"


# ---- servicio -----------------------------------------------------------


def test_tenant_only_sees_own_domains_and_admin_sees_all(tmp_path, fake_audit):
    config = config_for(tmp_path)
    write_state(
        config.state_path,
        [
            cert_payload(DEFAULT),
            cert_payload("mail.systutor.com"),
            cert_payload("mail.other.com"),
            cert_payload("mail.unknown.com"),
        ],
    )
    app = app_with_sites(
        OwnedSite("s1", "t1", "site-1", "systutor.com"),
        OwnedSite("s2", "t2", "site-2", "other.com"),
    )
    service = CertificatesService(FakeDb(), app, config=config)

    tenant = service.list_certificates(ctx("t1"))
    assert [item.hostname for item in tenant.items] == ["mail.systutor.com"]

    other = service.list_certificates(ctx("t2"))
    assert [item.hostname for item in other.items] == ["mail.other.com"]

    platform = service.list_certificates(
        TenantContext("t1", None, "admin", ("core.platform.admin",), None)
    )
    assert {item.hostname for item in platform.items} == {
        DEFAULT,
        "mail.systutor.com",
        "mail.other.com",
        "mail.unknown.com",
    }

    with pytest.raises(NotFoundError):
        service.get_certificate(ctx("t1"), "mail.other.com")
    with pytest.raises(NotFoundError):
        service.get_certificate(ctx("t1"), DEFAULT)

    # La vista de Correos pide tenant_only: incluso el admin de plataforma ve
    # solo el dominio del tenant actual.
    scoped = service.list_certificates(
        TenantContext("t1", None, "admin", ("core.platform.admin",), None),
        tenant_only=True,
    )
    assert [item.hostname for item in scoped.items] == ["mail.systutor.com"]


def test_request_creates_job_and_audits(tmp_path, fake_audit):
    config = config_for(tmp_path)
    write_state(config.state_path, [cert_payload(DEFAULT), cert_payload("mail.systutor.com")])
    app = app_with_sites(OwnedSite("s1", "t1", "site-1", "systutor.com"))
    service = CertificatesService(FakeDb(), app, config=config)

    response = service.request_certificate(
        TenantContext("t1", None, "u1", ("mail.certificates.manage",), None),
        "mail.systutor.com",
        action="force",
        reason="manual",
    )
    assert response.state == "queued"
    assert (config.requests_dir / f"{response.job_id}.json").exists()
    assert fake_audit[-1]["action"] == "mail.certificate.requested"
    assert fake_audit[-1]["details"]["action"] == "force"

    with pytest.raises(ActiveJobError):
        service.request_certificate(
            TenantContext("t1", None, "u2", ("mail.certificates.manage",), None),
            "mail.systutor.com",
        )


def test_cf_mail_cannot_be_requested(tmp_path, fake_audit):
    config = config_for(tmp_path)
    write_state(config.state_path, [cert_payload(DEFAULT)])
    app = app_with_sites(OwnedSite("s1", "t1", "site-1", "systutor.com"))
    service = CertificatesService(FakeDb(), app, config=config)

    with pytest.raises(AppError) as exc:
        service.request_certificate(
            TenantContext("t1", None, "admin", ("core.platform.admin",), None), DEFAULT
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "mail.certificate_global"
    assert not config.requests_dir.exists() or not list(config.requests_dir.glob("*.json"))


def test_refresh_writes_request(tmp_path, fake_audit):
    config = config_for(tmp_path)
    write_state(config.state_path, [cert_payload(DEFAULT)])
    app = app_with_sites()
    service = CertificatesService(FakeDb(), app, config=config)

    response = service.refresh(
        TenantContext("t1", None, "admin", ("mail.certificates.manage",), None)
    )
    assert response.requested is True
    files = list(config.requests_dir.glob("refresh-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["action"] == "refresh"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert fake_audit[-1]["action"] == "mail.certificate.refreshed"


def test_inventory_error_is_503(tmp_path, fake_audit):
    config = config_for(tmp_path)
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(
        '{"certificates": [], "note": "-----BEGIN EC PRIVATE KEY-----"}',
        encoding="utf-8",
    )
    app = app_with_sites()
    service = CertificatesService(FakeDb(), app, config=config)

    with pytest.raises(InventoryError) as exc:
        service.list_certificates(ctx("t1"))
    assert exc.value.status_code == 503


def test_schema_accepts_serialized_model():
    item = CertificateItem(
        **Certificate.from_payload(
            cert_payload("mail.systutor.com"),
            default_name=DEFAULT,
            now=datetime.now(UTC),
            renew_days=30,
        ).to_dict()
    )
    assert item.hostname == "mail.systutor.com"
    assert "PRIVATE KEY" not in item.model_dump_json()


def test_state_file_mode_is_not_written_by_inventory(tmp_path):
    config = config_for(tmp_path)
    write_state(config.state_path, [cert_payload(DEFAULT)])
    before = os.stat(config.state_path).st_mtime_ns
    load_inventory(config)
    assert os.stat(config.state_path).st_mtime_ns == before
