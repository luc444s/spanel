"""OAuth flow: PKCE, state hashing, callback replay/expiry/denial and secrets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.cloudflare.client import (
    CloudflareCredentials,
    CloudflareNotFound,
    CloudflareUnavailable,
    OAuthTokens,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig
from plugins.domains.backend.cloudflare.crypto import CloudflareCipher, EncryptedValue
from plugins.domains.backend.cloudflare.oauth import (
    failure_page,
    generate_pkce,
    granted_scope_names,
    resolve_scope_ids,
    state_hash,
    success_page,
)
from plugins.domains.backend.cloudflare.service import (
    FLOW_TTL_SECONDS,
    PURPOSE_VERIFIER,
    CloudflareService,
)
from plugins.domains.backend.models import (
    CloudflareConnection,
    CloudflareOAuthFlow,
)
from systutor.core.database import Base
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.tenants.models import Tenant

ACCESS_CANARY = "access-token-canary"
REFRESH_CANARY = "refresh-token-canary"
SECRET_CANARY = "client-secret-canary"
BOOTSTRAP_CANARY = "bootstrap-token-canary"


def write_key(tmp_path: Path) -> Path:
    path = tmp_path / "cf_key"
    path.write_bytes(b"z" * 32)
    path.chmod(0o600)
    return path


def config_for(tmp_path: Path) -> CloudflareConfig:
    return CloudflareConfig(write_key(tmp_path), "https://spanel.lab")


class FakeCloudflare:
    def __init__(self) -> None:
        self.scopes = [
            {"id": "s1", "name": "Zone Read"},
            {"id": "s2", "name": "DNS Read"},
            {"id": "s3", "name": "DNS Write"},
        ]
        self.registered: list[dict] = []
        self.exchanges: list[dict] = []
        self.accounts = [{"id": "acct1", "name": "Lab"}]
        self.tokens = OAuthTokens(
            ACCESS_CANARY, REFRESH_CANARY, 3600, "zone.read dns.read dns.write"
        )
        self.exchange_error: Exception | None = None
        self.account_error: Exception | None = None
        self.revoked: list[str] = []

    def list_scopes(self, bootstrap_token):
        assert bootstrap_token == BOOTSTRAP_CANARY
        return list(self.scopes)

    def register_client(
        self,
        *,
        account_id,
        bootstrap_token,
        redirect_uri,
        scope_ids,
        client_name="PanelTutor",
    ):
        self.registered.append(
            {
                "account_id": account_id,
                "bootstrap_token": bootstrap_token,
                "redirect_uri": redirect_uri,
                "scope_ids": scope_ids,
            }
        )
        return "client-1", SECRET_CANARY

    def update_client(self, **kwargs):
        self.registered.append({"updated": True, **kwargs})

    def authorization_url(self, *, client_id, redirect_uri, state, code_challenge, scopes):
        return (
            "https://dash.cloudflare.com/oauth2/auth?"
            f"state={state}&client_id={client_id}&code_challenge={code_challenge}"
            f"&redirect_uri={redirect_uri}"
        )

    def exchange_code(self, **kwargs):
        self.exchanges.append(kwargs)
        if self.exchange_error is not None:
            raise self.exchange_error
        return self.tokens

    def list_accounts(self, credentials: CloudflareCredentials, refresh=None):
        assert credentials.access_token == self.tokens.access_token
        if self.account_error is not None:
            raise self.account_error
        return list(self.accounts)

    def refresh(self, **kwargs):
        return OAuthTokens("new-access", "new-refresh", 3600, "")

    def revoke(self, **kwargs):
        self.revoked.append(kwargs["token"])

    def list_zones(self, credentials, refresh=None):
        return []

    def count_records(self, credentials, zone_id, refresh=None):
        return 0

    def get_zone(self, credentials, zone_id, refresh=None):
        raise CloudflareNotFound("zone not found")

    def list_records(self, credentials, zone_id, refresh=None):
        return []


def build(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'oauth.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="One", slug="one"))
    db.commit()
    fake = FakeCloudflare()
    config = config_for(tmp_path)
    cipher = CloudflareCipher(config.load_key())
    events: list[tuple] = []
    service = CloudflareService(
        db,
        None,
        fake,  # type: ignore[arg-type]
        config,
        cipher=cipher,
        publisher=lambda *args: events.append(args),
    )
    service.register(
        account_id="acct1",
        bootstrap_token=BOOTSTRAP_CANARY,
        redirect_uri=config.redirect_uri,
        apply=True,
    )
    return db, fake, config, cipher, service, events, engine


def start(service: CloudflareService) -> tuple[str, str]:
    payload = service.start_flow_for_tenant("t1")
    query = parse_qs(urlsplit(payload["authorization_url"]).query)
    return query["state"][0], str(payload["flow_id"])


def test_pkce_state_and_scope_helpers() -> None:
    verifier, challenge = generate_pkce()
    assert len(verifier) >= 43
    assert challenge != verifier
    assert state_hash("a") == state_hash("a")
    assert len(state_hash("a")) == 64
    assert granted_scope_names("zone.read  dns.read") == ["zone.read", "dns.read"]
    with pytest.raises(Exception, match="required OAuth scopes"):
        resolve_scope_ids([{"id": "x", "name": "Zone Read"}])
    assert resolve_scope_ids(
        [
            {"id": "1", "name": "Zone Read"},
            {"id": "2", "name": "DNS Read"},
            {"id": "3", "name": "DNS Write"},
        ]
    ) == ["1", "2", "3"]
    assert "token" not in success_page().lower()
    assert "tenant" not in failure_page().lower()


def test_callback_success_is_single_use_and_never_stores_plaintext(tmp_path: Path) -> None:
    db, fake, config, cipher, service, events, engine = build(tmp_path)
    try:
        state, flow_id = start(service)
        flow = db.get(CloudflareOAuthFlow, flow_id)
        assert flow is not None
        assert flow.state_hash == state_hash(state)
        assert state not in flow.state_hash
        assert flow.status == "pending"
        assert flow.expires_at - flow.created_at <= timedelta(seconds=FLOW_TTL_SECONDS + 1)
        verifier = cipher.decrypt(
            PURPOSE_VERIFIER,
            f"t1:{flow_id}",
            EncryptedValue(flow.verifier_ciphertext, flow.verifier_nonce, flow.key_version),
        )
        assert verifier and verifier.encode() not in flow.verifier_ciphertext

        result = service.complete_callback(state=state, code="auth-code")
        assert result.success is True
        assert fake.exchanges[0]["code"] == "auth-code"
        assert fake.exchanges[0]["code_verifier"] == verifier
        assert fake.exchanges[0]["redirect_uri"] == config.redirect_uri
        db.expire_all()
        flow = db.get(CloudflareOAuthFlow, flow_id)
        assert flow is not None and flow.status == "consumed" and flow.consumed_at
        connection = db.scalar(select(CloudflareConnection))
        assert connection is not None
        assert ACCESS_CANARY.encode() not in connection.access_ciphertext
        assert REFRESH_CANARY.encode() not in (connection.refresh_ciphertext or b"")
        assert connection.status == "active"
        assert connection.account_name == "Cloudflare"
        assert connection.scopes_json == ["Zone Read", "DNS Read", "DNS Write"]

        replay = service.complete_callback(state=state, code="auth-code")
        assert replay.success is False
        assert len(fake.exchanges) == 1
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "domains.dns.cloudflare_connected")
        )
        assert audit is not None
        assert ACCESS_CANARY not in str(audit.details)
        assert REFRESH_CANARY not in str(audit.details)
        assert events and events[-1][0] == "domains.dns.cloudflare_connected"
    finally:
        db.close()
        engine.dispose()


def test_unknown_expired_denied_and_mismatched_callbacks_do_not_exchange(tmp_path: Path) -> None:
    db, fake, config, _cipher, service, _events, engine = build(tmp_path)
    try:
        unknown = service.complete_callback(state="unknown-state", code="code")
        assert unknown.success is False
        assert fake.exchanges == []

        state, flow_id = start(service)
        flow = db.get(CloudflareOAuthFlow, flow_id)
        assert flow is not None
        flow.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        expired = service.complete_callback(state=state, code="code")
        assert expired.success is False
        db.expire_all()
        assert db.get(CloudflareOAuthFlow, flow_id).status == "expired"

        state, flow_id = start(service)
        denied = service.complete_callback(state=state, error="access_denied")
        assert denied.success is False
        db.expire_all()
        flow = db.get(CloudflareOAuthFlow, flow_id)
        assert flow is not None and flow.status == "denied"
        assert flow.error_code == "access_denied"

        state, flow_id = start(service)
        mismatch = service.complete_callback(
            state=state, code="code", redirect_uri="https://evil.example/cb"
        )
        assert mismatch.success is False
        db.expire_all()
        assert db.get(CloudflareOAuthFlow, flow_id).status == "pending"

        state, flow_id = start(service)
        missing = service.complete_callback(state=state)
        assert missing.success is False
        assert fake.exchanges == []
    finally:
        db.close()
        engine.dispose()


def test_failed_reconnect_keeps_previous_connection(tmp_path: Path) -> None:
    db, fake, _config, _cipher, service, _events, engine = build(tmp_path)
    try:
        state, _flow_id = start(service)
        assert service.complete_callback(state=state, code="first").success is True
        original = db.scalar(select(CloudflareConnection))
        assert original is not None
        original_access = original.access_ciphertext

        state, _flow_id = start(service)
        fake.exchange_error = CloudflareUnavailable("down")
        failed = service.complete_callback(state=state, code="second")
        assert failed.success is False
        db.expire_all()
        remaining = db.scalar(select(CloudflareConnection))
        assert remaining is not None
        assert remaining.access_ciphertext == original_access

        state, _flow_id = start(service)
        fake.exchange_error = None
        fake.tokens = OAuthTokens("second-access", "second-refresh", 1800, "dns.read")
        assert service.complete_callback(state=state, code="third").success is True
        db.expire_all()
        replaced = db.scalar(select(CloudflareConnection))
        assert replaced is not None
        assert replaced.access_ciphertext != original_access
        assert replaced.scopes_json == ["Zone Read", "DNS Read", "DNS Write"]
    finally:
        db.close()
        engine.dispose()
