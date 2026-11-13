"""Cloudflare service: registration, persistence, refresh, discovery, disconnect."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.cloudflare.client import (
    CloudflareNotFound,
    CloudflareRecord,
    CloudflareUnavailable,
    CloudflareZone,
    OAuthTokens,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig
from plugins.domains.backend.cloudflare.crypto import CloudflareCipher, EncryptedValue
from plugins.domains.backend.cloudflare.service import (
    PURPOSE_ACCESS,
    PURPOSE_CLIENT_SECRET,
    PURPOSE_REFRESH,
    PURPOSE_VERIFIER,
    CloudflareService,
)
from plugins.domains.backend.models import (
    CloudflareConnection,
    CloudflareOAuthClient,
    CloudflareOAuthFlow,
)
from systutor.core.database import Base
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.models import Tenant

BOOTSTRAP = "bootstrap-canary"
CLIENT_SECRET = "client-secret-canary"
ACCESS = "access-canary"
REFRESH = "refresh-canary"


def make_config(tmp_path: Path) -> CloudflareConfig:
    path = tmp_path / "key"
    path.write_bytes(b"q" * 32)
    path.chmod(0o600)
    return CloudflareConfig(path, "https://spanel.lab")


class FakeCloudflare:
    def __init__(self) -> None:
        self.scope_calls = 0
        self.register_calls = 0
        self.update_calls = 0
        self.authorization_scopes: list[str] = []
        self.exchange_calls = 0
        self.refresh_calls = 0
        self.revoked: list[str] = []
        self.revoke_error: Exception | None = None
        self.tokens = OAuthTokens(ACCESS, REFRESH, 3600, "Zone Read DNS Read DNS Write")
        self.accounts = [{"id": "acct1", "name": "Lab"}]
        self.zones = [
            CloudflareZone("z1", "example.com", "active", "acct1", "Lab", ("ns1",), None, None),
            CloudflareZone("z2", "other.test", "pending", "acct1", "Lab", (), None, None),
        ]
        self.counts = {"z1": 12, "z2": 0}
        self.records = {
            "z1": [
                CloudflareRecord(
                    "r1", "example.com", "TXT", "record-canary", 120, False,
                    None, None, None,
                ),
                CloudflareRecord(
                    "r2", "www.example.com", "A", "192.0.2.1", 300, True,
                    None, None, None,
                ),
            ]
        }

    def list_scopes(self, bootstrap_token):
        self.scope_calls += 1
        assert bootstrap_token == BOOTSTRAP
        return [
            {"id": "1", "name": "Zone Read"},
            {"id": "2", "name": "DNS Read"},
            {"id": "3", "name": "DNS Write"},
        ]

    def register_client(self, **kwargs):
        self.register_calls += 1
        assert kwargs["bootstrap_token"] == BOOTSTRAP
        return "client-1", CLIENT_SECRET

    def update_client(self, **kwargs):
        self.update_calls += 1
        assert kwargs["bootstrap_token"] == BOOTSTRAP

    def authorization_url(self, *, state, code_challenge, scopes, **_kwargs):
        self.authorization_scopes = list(scopes)
        return f"https://dash.cloudflare.com/oauth2/auth?state={state}&code_challenge={code_challenge}"

    def exchange_code(self, **kwargs):
        self.exchange_calls += 1
        return self.tokens

    def list_accounts(self, credentials, refresh=None):
        raise AssertionError("OAuth discovery must not require Account Settings Read")

    def refresh(self, **kwargs):
        self.refresh_calls += 1
        return OAuthTokens("rotated-access", "rotated-refresh", 3600, "")

    def revoke(self, *, token, **_kwargs):
        if self.revoke_error is not None:
            raise self.revoke_error
        self.revoked.append(token)

    def list_zones(self, credentials, refresh=None):
        if credentials.access_token == "old-access" and refresh is not None:
            refreshed = refresh()
            assert refreshed.access_token == "rotated-access"
        return list(self.zones)

    def count_records(self, credentials, zone_id, refresh=None):
        return self.counts[zone_id]

    def get_zone(self, credentials, zone_id, refresh=None):
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        raise CloudflareNotFound("zone not found")

    def list_records(self, credentials, zone_id, refresh=None):
        return list(self.records.get(zone_id, []))


def build(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'service.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="One", slug="one"))
    db.commit()
    config = make_config(tmp_path)
    cipher = CloudflareCipher(config.load_key())
    fake = FakeCloudflare()
    events: list[tuple] = []
    service = CloudflareService(
        db,
        None,
        fake,  # type: ignore[arg-type]
        config,
        cipher=cipher,
        publisher=lambda *args: events.append(args),
    )
    return db, fake, cipher, service, events, engine


def register(service: CloudflareService, config: CloudflareConfig) -> None:
    service.register(
        account_id="acct1",
        bootstrap_token=BOOTSTRAP,
        redirect_uri=config.redirect_uri,
        apply=True,
    )


def connect(service: CloudflareService) -> None:
    payload = service.start_flow_for_tenant("t1")
    state = parse_qs(urlsplit(payload["authorization_url"]).query)["state"][0]
    assert service.complete_callback(state=state, code="code").success is True


def seed_connection(
    db: Session, cipher: CloudflareCipher, *, access="old-access"
) -> CloudflareConnection:
    access_value = cipher.encrypt(PURPOSE_ACCESS, "t1", access)
    refresh_value = cipher.encrypt(PURPOSE_REFRESH, "t1", REFRESH)
    row = CloudflareConnection(
        tenant_id="t1",
        cloudflare_account_id="acct1",
        account_name="Lab",
        access_ciphertext=access_value.ciphertext,
        access_nonce=access_value.nonce,
        refresh_ciphertext=refresh_value.ciphertext,
        refresh_nonce=refresh_value.nonce,
        key_version=access_value.key_version,
        scopes_json=["Zone Read", "DNS Read", "DNS Write"],
        expires_at=datetime.now(UTC),
        status="active",
    )
    db.add(row)
    db.commit()
    return row


def test_register_dry_run_makes_no_remote_or_sql_mutation(tmp_path: Path) -> None:
    db, fake, _cipher, service, _events, engine = build(tmp_path)
    config = service.config
    try:
        plan = service.register(
            account_id="acct1",
            bootstrap_token=BOOTSTRAP,
            redirect_uri=config.redirect_uri,
            apply=False,
        )
        assert plan["applied"] is False
        assert plan["client_secret"] == "<redacted>"
        assert fake.scope_calls == 0
        assert fake.register_calls == 0
        assert db.scalar(select(CloudflareOAuthClient)) is None
    finally:
        db.close()
        engine.dispose()


def test_register_encrypts_secret_and_repairs_on_reregistration(tmp_path: Path) -> None:
    db, fake, cipher, service, _events, engine = build(tmp_path)
    config = service.config
    try:
        with pytest.raises(CloudflareNotFound):
            service.status_for_tenant("t1")
        register(service, config)
        row = db.scalar(select(CloudflareOAuthClient))
        assert row is not None
        assert CLIENT_SECRET.encode() not in row.secret_ciphertext
        decrypted = cipher.decrypt(
            PURPOSE_CLIENT_SECRET,
            "acct1:client-1",
            EncryptedValue(row.secret_ciphertext, row.secret_nonce, row.key_version),
        )
        assert decrypted == CLIENT_SECRET
        assert BOOTSTRAP not in str(row.__dict__)
        assert fake.register_calls == 1
        assert fake.update_calls == 0

        register(service, config)
        assert fake.register_calls == 1
        assert fake.update_calls == 1
        assert service.client_status()["needs_repair"] is False
    finally:
        db.close()
        engine.dispose()


def test_register_repairs_legacy_scope_names_without_rotating_client(tmp_path: Path) -> None:
    db, fake, _cipher, service, _events, engine = build(tmp_path)
    try:
        register(service, service.config)
        row = db.scalar(select(CloudflareOAuthClient))
        assert row is not None
        row.scopes_json = ["Zone Read", "DNS Read", "DNS Write"]
        db.commit()

        repaired = service.register(
            account_id="acct1",
            bootstrap_token=BOOTSTRAP,
            redirect_uri=service.config.redirect_uri,
            apply=True,
        )
        assert repaired["client_id"] == "client-1"
        assert fake.register_calls == 1
        assert row.scopes_json == [
            {"id": "1", "name": "Zone Read"},
            {"id": "2", "name": "DNS Read"},
            {"id": "3", "name": "DNS Write"},
            {"id": "offline_access", "name": "Offline Access"},
        ]
        assert fake.update_calls == 1
    finally:
        db.close()
        engine.dispose()


def test_start_flow_persists_only_hashed_state_and_encrypted_verifier(tmp_path: Path) -> None:
    db, fake, cipher, service, _events, engine = build(tmp_path)
    try:
        register(service, service.config)
        payload = service.start_flow_for_tenant("t1")
        assert fake.authorization_scopes == ["1", "2", "3", "offline_access"]
        flow = db.scalar(select(CloudflareOAuthFlow))
        assert flow is not None
        state = parse_qs(urlsplit(payload["authorization_url"]).query)["state"][0]
        assert flow.state_hash != state
        assert len(flow.state_hash) == 64
        verifier = cipher.decrypt(
            PURPOSE_VERIFIER,
            f"t1:{flow.id}",
            EncryptedValue(flow.verifier_ciphertext, flow.verifier_nonce, flow.key_version),
        )
        assert verifier
        assert flow.expires_at > datetime.now(UTC).replace(tzinfo=None)
        assert service.flow_status(flow.id)["status"] == "pending"
    finally:
        db.close()
        engine.dispose()


def test_zones_refresh_once_under_lock_and_records_are_redacted(tmp_path: Path) -> None:
    db, fake, cipher, service, events, engine = build(tmp_path)
    try:
        register(service, service.config)
        row = seed_connection(db, cipher)
        old_ciphertext = row.access_ciphertext
        zones = service.zones_for_tenant("t1")
        assert [(zone["name"], zone["record_count"]) for zone in zones] == [
            ("example.com", 12),
            ("other.test", 0),
        ]
        assert fake.refresh_calls == 1
        db.expire_all()
        stored = db.scalar(select(CloudflareConnection))
        assert stored is not None
        assert stored.access_ciphertext != old_ciphertext
        rotated = cipher.decrypt(
            PURPOSE_ACCESS,
            "t1",
            EncryptedValue(stored.access_ciphertext, stored.access_nonce, stored.key_version),
        )
        assert rotated == "rotated-access"

        records = service.records_for_tenant("t1", "z1")
        assert [record["type"] for record in records] == ["TXT", "A"]
        assert all("content" not in record for record in records)
        with pytest.raises(CloudflareNotFound):
            service.records_for_tenant("t1", "missing")
        assert events == []
    finally:
        db.close()
        engine.dispose()


def test_absent_connection_is_not_found(tmp_path: Path) -> None:
    db, _fake, _cipher, service, _events, engine = build(tmp_path)
    try:
        with pytest.raises(CloudflareNotFound):
            service.status_for_tenant("t1")
        with pytest.raises(CloudflareNotFound):
            service.zones_for_tenant("t1")
        with pytest.raises(CloudflareNotFound):
            service.records_for_tenant("t1", "z1")
    finally:
        db.close()
        engine.dispose()


def test_disconnect_dry_run_apply_audit_events_and_idempotence(tmp_path: Path) -> None:
    db, fake, cipher, service, events, engine = build(tmp_path)
    try:
        seed_connection(db, cipher)
        flow = CloudflareOAuthFlow(
            tenant_id="t1",
            state_hash="a" * 64,
            verifier_ciphertext=b"x",
            verifier_nonce=b"y",
            key_version=1,
            redirect_uri=service.config.redirect_uri,
            status="pending",
            expires_at=datetime.now(UTC),
        )
        db.add(flow)
        db.commit()

        plan = service.disconnect_for_tenant("t1", apply=False)
        assert plan["applied"] is False
        assert plan["pending_flows"] == 1
        assert db.scalar(select(CloudflareConnection)) is not None
        assert fake.revoked == []

        fake.revoke_error = CloudflareUnavailable("down")
        applied = service.disconnect_for_tenant("t1", apply=True)
        assert applied["disconnected"] is True
        assert applied["revoked"] is False
        assert applied["cancelled_flows"] == 1
        assert fake.revoked == []
        assert db.scalar(select(CloudflareConnection)) is None
        db.expire_all()
        assert db.get(CloudflareOAuthFlow, flow.id).status == "cancelled"
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "domains.dns.cloudflare_disconnected")
        )
        assert audit is not None
        assert ACCESS not in str(audit.details)
        assert events[-1][0] == "domains.dns.cloudflare_disconnected"

        again = service.disconnect_for_tenant("t1", apply=True)
        assert again["already_disconnected"] is True
        assert again["applied"] is True
    finally:
        db.close()
        engine.dispose()


def test_callback_persists_encrypted_tokens_and_status(tmp_path: Path) -> None:
    db, fake, cipher, service, _events, engine = build(tmp_path)
    try:
        register(service, service.config)
        connect(service)
        row = db.scalar(select(CloudflareConnection))
        assert row is not None
        assert ACCESS.encode() not in row.access_ciphertext
        assert REFRESH.encode() not in (row.refresh_ciphertext or b"")
        assert cipher.decrypt(
            PURPOSE_ACCESS,
            "t1",
            EncryptedValue(row.access_ciphertext, row.access_nonce, row.key_version),
        ) == ACCESS
        status = service.status_for_tenant("t1")
        assert status["connected"] is True
        assert status["account_name"] == "Lab"
        assert status["scopes"] == ["Zone Read", "DNS Read", "DNS Write"]
        assert fake.exchange_calls == 1
    finally:
        db.close()
        engine.dispose()
