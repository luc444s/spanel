"""Operator CLI: register dry-run/apply/conflict, connect, status, discovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.cloudflare.client import (
    CloudflareNotFound,
    CloudflareRecord,
    CloudflareZone,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig
from plugins.domains.backend.cloudflare.crypto import CloudflareCipher
from plugins.domains.backend.cloudflare.service import PURPOSE_ACCESS
from plugins.domains.backend.dns.cli import run
from plugins.domains.backend.models import CloudflareConnection, CloudflareOAuthClient
from systutor.core.database import Base
from systutor.kernel.tenants.models import Tenant

SECRET = "secret-canary"
BOOTSTRAP = "bootstrap-canary"


class FakeCloudflare:
    def __init__(self) -> None:
        self.scope_calls = 0
        self.register_calls = 0
        self.update_calls = 0
        self.revoked: list[str] = []
        self.zones = [
            CloudflareZone("z1", "example.com", "active", "acct1", "Lab", ("ns1",), None, None)
        ]
        self.records = [
            CloudflareRecord(
                "r1", "example.com", "TXT", "record-canary", 120, False, None, None, None
            )
        ]

    def list_scopes(self, token):
        self.scope_calls += 1
        return [
            {"id": "1", "name": "Zone Read"},
            {"id": "2", "name": "DNS Read"},
            {"id": "3", "name": "DNS Write"},
        ]

    def register_client(self, **kwargs):
        self.register_calls += 1
        return "client-1", SECRET

    def update_client(self, **kwargs):
        self.update_calls += 1

    def authorization_url(self, *, state, code_challenge, **_kwargs):
        return f"https://dash.cloudflare.com/oauth2/auth?state={state}"

    def list_zones(self, credentials, refresh=None):
        return list(self.zones)

    def count_records(self, credentials, zone_id, refresh=None):
        return 7

    def get_zone(self, credentials, zone_id, refresh=None):
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        raise CloudflareNotFound("missing")

    def list_records(self, credentials, zone_id, refresh=None):
        return list(self.records)

    def revoke(self, **kwargs):
        self.revoked.append(kwargs["token"])


def build(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="One", slug="one"))
    db.commit()
    db.close()
    key = tmp_path / "key"
    key.write_bytes(b"e" * 32)
    key.chmod(0o600)
    config = CloudflareConfig(key, "https://spanel.lab")
    fake = FakeCloudflare()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, config, fake, factory


def register(engine, tmp_path, config, fake):
    token = tmp_path / "bootstrap"
    token.write_text(BOOTSTRAP + "\n", encoding="utf-8")
    token.chmod(0o600)
    code = run(
        [
            "cloudflare",
            "oauth",
            "register",
            "--account-id",
            "acct1",
            "--bootstrap-token-file",
            str(token),
            "--redirect-uri",
            config.redirect_uri,
            "--apply",
        ],
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        cloudflare_client=fake,
        cloudflare_config=config,
    )
    assert code == 0


def seed_connection(engine, config) -> None:
    cipher = CloudflareCipher(config.load_key())
    access = cipher.encrypt(PURPOSE_ACCESS, "t1", "access-canary")
    with Session(engine) as db:
        db.add(
            CloudflareConnection(
                tenant_id="t1",
                cloudflare_account_id="acct1",
                account_name="Lab",
                access_ciphertext=access.ciphertext,
                access_nonce=access.nonce,
                refresh_ciphertext=None,
                refresh_nonce=None,
                key_version=access.key_version,
                scopes_json=["Zone Read", "DNS Read", "DNS Write"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                status="active",
            )
        )
        db.commit()


def test_registration_dry_run_apply_and_conflict(tmp_path, capsys) -> None:
    engine, config, fake, factory = build(tmp_path)
    token = tmp_path / "bootstrap"
    token.write_text(BOOTSTRAP, encoding="utf-8")
    token.chmod(0o600)
    args = [
        "cloudflare",
        "oauth",
        "register",
        "--account-id",
        "acct1",
        "--bootstrap-token-file",
        str(token),
        "--redirect-uri",
        config.redirect_uri,
    ]
    try:
        assert (
            run(
                args,
                session_factory=factory,
                cloudflare_client=fake,
                cloudflare_config=config,
            )
            == 0
        )
        output = capsys.readouterr().out
        assert "registration plan" in output
        assert SECRET not in output
        assert fake.scope_calls == 0 and fake.register_calls == 0
        with Session(engine) as db:
            assert db.scalar(select(CloudflareOAuthClient)) is None

        assert (
            run(
                [*args, "--apply"],
                session_factory=factory,
                cloudflare_client=fake,
                cloudflare_config=config,
            )
            == 0
        )
        output = capsys.readouterr().out
        assert "SUCCESS" in output
        assert "client-1" in output
        assert SECRET not in output
        assert BOOTSTRAP not in output
        with Session(engine) as db:
            row = db.scalar(select(CloudflareOAuthClient))
            assert row is not None
            assert SECRET.encode() not in row.secret_ciphertext

        assert (
            run(
                [*args, "--apply"],
                session_factory=factory,
                cloudflare_client=fake,
                cloudflare_config=config,
            )
            == 0
        )
        repaired_output = capsys.readouterr().out
        assert "SUCCESS" in repaired_output
        assert SECRET not in repaired_output
        assert fake.update_calls == 1
    finally:
        engine.dispose()


def test_bootstrap_file_must_be_owner_only(tmp_path, capsys) -> None:
    engine, config, fake, factory = build(tmp_path)
    token = tmp_path / "bootstrap"
    token.write_text(BOOTSTRAP, encoding="utf-8")
    token.chmod(0o644)
    try:
        code = run(
            [
                "cloudflare",
                "oauth",
                "register",
                "--account-id",
                "acct1",
                "--bootstrap-token-file",
                str(token),
                "--redirect-uri",
                config.redirect_uri,
                "--apply",
            ],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        assert code == 2
        assert "owner-only" in capsys.readouterr().err
        assert fake.register_calls == 0
    finally:
        engine.dispose()


def test_connect_wait_zero_status_zones_and_records(tmp_path, capsys) -> None:
    engine, config, fake, factory = build(tmp_path)
    try:
        register(engine, tmp_path, config, fake)
        seed_connection(engine, config)

        code = run(
            [
                "cloudflare",
                "connect",
                "--tenant",
                "t1",
                "--wait-seconds",
                "0",
            ],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        output = capsys.readouterr().out
        assert code == 0
        assert "https://dash.cloudflare.com/oauth2/auth" in output
        assert "pending" in output

        code = run(
            ["cloudflare", "status", "--tenant", "t1"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        output = capsys.readouterr().out
        assert code == 0
        assert "Lab" in output

        code = run(
            ["cloudflare", "zones", "list", "--tenant", "t1", "--json"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["zones"][0]["record_count"] == 7

        code = run(
            ["cloudflare", "records", "list", "example.com", "--tenant", "t1", "--json"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["records"][0]["type"] == "TXT"
        assert "content" not in payload["records"][0]
    finally:
        engine.dispose()


def test_disconnect_plan_apply_and_idempotence(tmp_path, capsys) -> None:
    engine, config, fake, factory = build(tmp_path)
    try:
        register(engine, tmp_path, config, fake)
        seed_connection(engine, config)

        code = run(
            ["cloudflare", "disconnect", "--tenant", "t1"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        output = capsys.readouterr().out
        assert code == 0
        assert "disconnect plan" in output
        assert fake.revoked == []
        with Session(engine) as db:
            assert db.scalar(select(CloudflareConnection)) is not None

        code = run(
            ["cloudflare", "disconnect", "--tenant", "t1", "--apply"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        output = capsys.readouterr().out
        assert code == 0
        assert "disconnected" in output.lower()
        assert fake.revoked
        with Session(engine) as db:
            assert db.scalar(select(CloudflareConnection)) is None

        code = run(
            ["cloudflare", "disconnect", "--tenant", "t1", "--apply"],
            session_factory=factory,
            cloudflare_client=fake,
            cloudflare_config=config,
        )
        assert code == 0
        assert "already disconnected" in capsys.readouterr().out
    finally:
        engine.dispose()
