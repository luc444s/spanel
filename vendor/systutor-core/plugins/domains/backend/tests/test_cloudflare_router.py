"""Cloudflare router: public callback, RBAC, tenant isolation and normalized errors."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.cloudflare.client import (
    CloudflareNotFound,
    CloudflareRateLimit,
    CloudflareRecord,
    CloudflareUnavailable,
    CloudflareZone,
    OAuthTokens,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig
from plugins.domains.backend.cloudflare.crypto import CloudflareCipher
from plugins.domains.backend.cloudflare.service import (
    PURPOSE_ACCESS,
    PURPOSE_CLIENT_SECRET,
    PURPOSE_REFRESH,
)
from plugins.domains.backend.cloudflare_router import router
from plugins.domains.backend.models import CloudflareConnection, CloudflareOAuthClient
from systutor.api.deps import get_db_session
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Branch, Tenant


class FakeCloudflare:
    def __init__(self) -> None:
        self.zone_error: Exception | None = None
        self.record_error: Exception | None = None
        self.revoked: list[str] = []
        self.registered: list[dict[str, object]] = []
        self.zones = [
            CloudflareZone("z1", "example.com", "active", "acct1", "Lab", ("ns1",), None, None)
        ]
        self.records = [
            CloudflareRecord("r1", "example.com", "TXT", 120, False, None, None)
        ]

    def authorization_url(self, *, state, code_challenge, **_kwargs):
        return f"https://dash.cloudflare.com/oauth2/auth?state={state}"

    def list_zones(self, credentials, refresh=None):
        if self.zone_error is not None:
            raise self.zone_error
        return list(self.zones)

    def count_records(self, credentials, zone_id, refresh=None):
        if self.zone_error is not None:
            raise self.zone_error
        return 3

    def get_zone(self, credentials, zone_id, refresh=None):
        if self.record_error is not None:
            raise self.record_error
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        raise CloudflareNotFound("zone not found")

    def list_records(self, credentials, zone_id, refresh=None):
        if self.record_error is not None:
            raise self.record_error
        return list(self.records)

    def revoke(self, **kwargs):
        self.revoked.append(kwargs["token"])

    def list_token_accounts(self, token):
        return [{"id": "acct1", "name": "Lab"}]

    def list_scopes(self, token):
        return [
            {"id": "scope-zone-read", "name": "Zone Read"},
            {"id": "scope-dns-read", "name": "DNS Read"},
            {"id": "scope-dns-write", "name": "DNS Write"},
        ]

    def register_client(self, **kwargs):
        self.registered.append(kwargs)
        return "client-new", "secret-new"

    def refresh(self, **kwargs):
        return OAuthTokens("new", "new-refresh", 3600, "")


def context(tenant_id: str, *extra: str, platform: bool = False) -> TenantContext:
    permissions = [*extra]
    if platform:
        permissions.append("core.platform.admin")
    return TenantContext(tenant_id, "b1", "user-1", tuple(permissions), None)


def build_app(tmp_path: Path, monkeypatch):
    key = tmp_path / "key"
    key.write_bytes(b"w" * 32)
    key.chmod(0o600)
    monkeypatch.setenv("SPANEL_CLOUDFLARE_ENCRYPTION_KEY_FILE", str(key))
    monkeypatch.setenv("SPANEL_CLOUDFLARE_PUBLIC_URL", "https://spanel.lab")

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'router.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add_all(
        [
            Tenant(id="t1", name="One", slug="one"),
            Tenant(id="t2", name="Two", slug="two"),
            Branch(id="b1", tenant_id="t1", name="Main", code="MAIN"),
        ]
    )
    db.commit()
    config = CloudflareConfig(key, "https://spanel.lab")
    cipher = CloudflareCipher(config.load_key())

    secret = cipher.encrypt(PURPOSE_CLIENT_SECRET, "acct1:client-1", "secret-canary")
    db.add(
        CloudflareOAuthClient(
            id=1,
            account_id="acct1",
            client_id="client-1",
            secret_ciphertext=secret.ciphertext,
            secret_nonce=secret.nonce,
            key_version=secret.key_version,
            redirect_uri=config.redirect_uri,
            scopes_json=["Zone Read", "DNS Read", "DNS Write"],
        )
    )
    access = cipher.encrypt(PURPOSE_ACCESS, "t1", "access-canary")
    refresh = cipher.encrypt(PURPOSE_REFRESH, "t1", "refresh-canary")
    db.add(
        CloudflareConnection(
            tenant_id="t1",
            cloudflare_account_id="acct1",
            account_name="Lab",
            access_ciphertext=access.ciphertext,
            access_nonce=access.nonce,
            refresh_ciphertext=refresh.ciphertext,
            refresh_nonce=refresh.nonce,
            key_version=access.key_version,
            scopes_json=["Zone Read", "DNS Read", "DNS Write"],
            expires_at=datetime.now(UTC),
            status="active",
        )
    )
    db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1/plugins/domains")
    fake = FakeCloudflare()
    app.state.cloudflare_client = fake
    app.state.cloudflare_cipher = cipher
    holder = {"context": context("t1", "domains.dns.cloudflare", platform=True)}
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="user-1")
    app.dependency_overrides[get_current_tenant_context] = lambda: holder["context"]
    return app, fake, db, holder, cipher, engine


def test_status_requires_cloudflare_permission_and_tenant_scope(
    tmp_path: Path, monkeypatch
) -> None:
    app, _fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            holder["context"] = context("t1", "domains.dns.read")
            assert client.get("/api/v1/plugins/domains/dns/cloudflare/status").status_code == 403

            holder["context"] = context("t1", "domains.dns.cloudflare")
            response = client.get("/api/v1/plugins/domains/dns/cloudflare/status")
            assert response.status_code == 200
            payload = response.json()
            assert payload["connected"] is True
            assert payload["account_name"] == "Lab"
            assert "access-canary" not in response.text

            holder["context"] = context("t2", "domains.dns.cloudflare")
            missing = client.get("/api/v1/plugins/domains/dns/cloudflare/status")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "domains.dns.cloudflare.not_found"
    finally:
        db.close()
        engine.dispose()


def test_start_and_disconnect_are_tenant_scoped(tmp_path: Path, monkeypatch) -> None:
    app, fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            holder["context"] = context("t1", "domains.dns.read")
            assert (
                client.post("/api/v1/plugins/domains/dns/cloudflare/oauth/start").status_code
                == 403
            )
            assert (
                client.post("/api/v1/plugins/domains/dns/cloudflare/disconnect").status_code
                == 403
            )

            # A tenant administrator with the plugin permission no longer needs
            # core.platform.admin to authorize or remove its own connection.
            holder["context"] = context("t1", "domains.dns.cloudflare")
            started = client.post("/api/v1/plugins/domains/dns/cloudflare/oauth/start")
            assert started.status_code == 200
            body = started.json()
            assert body["authorization_url"].startswith("https://dash.cloudflare.com/oauth2/auth")
            assert body["flow_id"]
            assert "secret-canary" not in started.text

            holder["context"] = context("t2", "domains.dns.cloudflare")
            foreign = client.post("/api/v1/plugins/domains/dns/cloudflare/disconnect")
            assert foreign.status_code == 200
            assert foreign.json()["already_disconnected"] is True
            assert db.query(CloudflareConnection).count() == 1

            holder["context"] = context("t1", "domains.dns.cloudflare")
            planned = client.post(
                "/api/v1/plugins/domains/dns/cloudflare/disconnect", json={"apply": False}
            )
            assert planned.status_code == 200
            assert planned.json()["applied"] is False
            assert db.query(CloudflareConnection).count() == 1

            applied = client.post("/api/v1/plugins/domains/dns/cloudflare/disconnect")
            assert applied.status_code == 200
            assert applied.json()["disconnected"] is True
            assert fake.revoked
            assert db.query(CloudflareConnection).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_zones_records_redaction_and_error_mapping(tmp_path: Path, monkeypatch) -> None:
    app, fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            holder["context"] = context("t1", "domains.dns.cloudflare")
            zones = client.get("/api/v1/plugins/domains/dns/cloudflare/zones")
            assert zones.status_code == 200
            assert zones.json()["items"][0]["record_count"] == 3

            records = client.get(
                "/api/v1/plugins/domains/dns/cloudflare/zones/z1/records"
            )
            assert records.status_code == 200
            item = records.json()["items"][0]
            assert item["type"] == "TXT"
            assert "content" not in item
            assert "TXT" not in str(item.get("content", ""))

            fake.record_error = CloudflareNotFound("absent")
            assert (
                client.get(
                    "/api/v1/plugins/domains/dns/cloudflare/zones/missing/records"
                ).status_code
                == 404
            )

            fake.zone_error = CloudflareRateLimit("slow down")
            limited = client.get("/api/v1/plugins/domains/dns/cloudflare/zones")
            assert limited.status_code == 429
            assert limited.json()["error"]["code"] == "domains.dns.cloudflare.rate_limit"

            fake.zone_error = CloudflareUnavailable("down")
            unavailable = client.get("/api/v1/plugins/domains/dns/cloudflare/zones")
            assert unavailable.status_code == 503
    finally:
        db.close()
        engine.dispose()


def test_callback_is_public_and_opaque(tmp_path: Path, monkeypatch) -> None:
    app, _fake, db, _holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/plugins/domains/dns/cloudflare/oauth/callback",
                params={"state": "unknown", "code": "code", "tenant": "t1"},
            )
            assert response.status_code == 400
            assert "authorization failed" in response.text.lower()
            assert "t1" not in response.text
    finally:
        db.close()
        engine.dispose()


def test_oauth_client_registration_routes_are_platform_only(
    tmp_path: Path, monkeypatch
) -> None:
    app, _fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            holder["context"] = context("t1", "domains.dns.cloudflare")
            status_path = "/api/v1/plugins/domains/dns/cloudflare/oauth/client"
            assert client.get(status_path).status_code == 403
            assert (
                client.post(
                    "/api/v1/plugins/domains/dns/cloudflare/oauth/register",
                    json={"bootstrap_token": "token-canary"},
                ).status_code
                == 403
            )

            holder["context"] = context("t1", "domains.dns.cloudflare", platform=True)
            status = client.get("/api/v1/plugins/domains/dns/cloudflare/oauth/client")
            assert status.status_code == 200
            body = status.json()
            assert body["registered"] is True
            assert body["client_id"] == "client-1"
            assert "secret-canary" not in status.text
    finally:
        db.close()
        engine.dispose()


def test_platform_registers_client_from_pasted_token(tmp_path: Path, monkeypatch) -> None:
    app, fake, db, holder, _cipher, engine = build_app(tmp_path, monkeypatch)
    token = "bootstrap-token-canary"
    try:
        db.query(CloudflareOAuthClient).delete()
        db.commit()
        holder["context"] = context("t1", "domains.dns.cloudflare", platform=True)
        with TestClient(app) as client:
            invalid = client.post(
                "/api/v1/plugins/domains/dns/cloudflare/oauth/register",
                json={"bootstrap_token": token, "account_id": "not valid!"},
            )
            assert invalid.status_code == 422
            assert token not in invalid.text

            registered = client.post(
                "/api/v1/plugins/domains/dns/cloudflare/oauth/register",
                json={"bootstrap_token": token},
            )
            assert registered.status_code == 200
            payload = registered.json()
            assert payload["registered"] is True
            assert payload["client_id"] == "client-new"
            assert payload["account_id"] == "acct1"
            assert token not in registered.text
            assert "secret-new" not in registered.text
            assert fake.registered and fake.registered[0]["account_id"] == "acct1"

            conflict = client.post(
                "/api/v1/plugins/domains/dns/cloudflare/oauth/register",
                json={"bootstrap_token": token},
            )
            assert conflict.status_code == 409
            assert token not in conflict.text
    finally:
        db.close()
        engine.dispose()
