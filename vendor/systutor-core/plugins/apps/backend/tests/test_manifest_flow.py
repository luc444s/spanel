from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.crypto import EncryptedValue, TokenCipher
from plugins.apps.backend.manifest_flow import ManifestFlow
from plugins.apps.backend.models import (
    GithubInstallation,
    GithubProvider,
    Operation,
)
from plugins.apps.backend.schemas import ManifestStartRequest
from plugins.apps.backend.settings import AppsSettings
from plugins.apps.backend.states import INSTALLATION, REGISTRATION, OneTimeStates, StateInvalid
from systutor.core.database import Base
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant

PEM_PURPOSE = "github-app-private-key"


class Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, *, ex: int, nx: bool):
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    def getdel(self, name: str):
        return self.values.pop(name, None)


class Github:
    def __init__(self, pem: str, app_id: int = 4242, installation_id: int = 77) -> None:
        self.pem = pem
        self.app_id = app_id
        self.installation_id = installation_id
        self.calls: list[tuple] = []
        self.suspended = False
        self.wrong_app = False

    def exchange_manifest(self, code: str):
        self.calls.append(("exchange", code))
        return {
            "id": self.app_id,
            "slug": "spanel-apps-test",
            "name": "SPanel Apps Test",
            "pem": self.pem,
            "client_secret": "client-secret-canary",
            "webhook_secret": "webhook-secret-canary",
            "owner": {"login": "acme", "type": "Organization"},
        }

    def installation(self, jwt: str, installation_id: int):
        self.calls.append(("installation", jwt, installation_id))
        assert jwt.startswith("eyJ")
        return {
            "id": self.installation_id,
            "app_id": 9999 if self.wrong_app else self.app_id,
            "account": {"id": 5, "login": "acme", "type": "Organization"},
            "repository_selection": "selected",
            "permissions": {"contents": "read", "metadata": "read"},
            "suspended_at": "2026-01-01T00:00:00Z" if self.suspended else None,
        }

    def installation_token(self, *args, **kwargs):
        raise AssertionError("manifest callbacks must not create installation tokens")

    def installation_repositories(self, *args, **kwargs):
        raise AssertionError("manifest callbacks must not list repositories")


class Dispatcher:
    def __init__(self) -> None:
        self.args: list[str] = []

    def enqueue(self, operation_id: str) -> str:
        self.args.append(operation_id)
        return f"message-{operation_id}"


def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_pem() -> str:
    return key().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def settings(tmp_path: Path) -> AppsSettings:
    key_file = tmp_path / "apps_key"
    key_file.write_text("x" * 32, encoding="utf-8")
    return AppsSettings(
        github_client_id="", github_client_secret_file=tmp_path / "missing",
        encryption_key_file=key_file, oauth_callback_url="",
        public_url="https://panel.example", redis_url="redis://unused",
        state_root=tmp_path / "state",
    )


def database(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    db.add(Tenant(id="t2", name="Other", slug="other"))
    for user_id, tenant_id in (("u1", "t1"), ("u2", "t2")):
        db.add(User(
            id=user_id, tenant_id=tenant_id, email=f"{user_id}@example.test",
            full_name=user_id, password_hash="x",
        ))
    db.commit()
    return db


def flow(db: Session, tmp_path: Path, github: Github | None = None):
    pem = private_pem()
    store = Store()
    dispatcher = Dispatcher()
    client = github or Github(pem)
    if github is None:
        client.pem = pem
    instance = ManifestFlow(
        db, settings=settings(tmp_path), states=OneTimeStates(store),
        github=client, cipher=TokenCipher(b"k" * 32), dispatcher=dispatcher,
        production=True,
    )
    return instance, store, client, dispatcher


def installation_state(store: Store, response: dict) -> str:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(response["manifest"]["setup_url"]).query)
    return query["state"][0]


def test_manifest_start_binds_public_url_and_two_independent_states(tmp_path: Path) -> None:
    db = database(tmp_path)
    instance, store, _github, _dispatcher = flow(db, tmp_path)
    user = db.get(User, "u1")
    response = instance.start(user, ManifestStartRequest(owner_type="personal"))
    assert response["action_url"] == "https://github.com/settings/apps/new"
    manifest = response["manifest"]
    assert manifest["redirect_url"] == (
        "https://panel.example/api/v1/plugins/apps/github/manifest/callback"
    )
    assert manifest["setup_url"].startswith(
        "https://panel.example/api/v1/plugins/apps/github/install/callback?state="
    )
    assert manifest["public"] is False
    assert manifest["request_oauth_on_install"] is False
    assert manifest["default_permissions"] == {"contents": "read", "metadata": "read"}
    assert manifest["default_events"] == []
    assert len(manifest["name"]) <= 34
    assert response["state"] != installation_state(store, response)
    assert len(store.values) == 2
    assert all(key.startswith("apps:state:") for key in store.values)


def test_conversion_encrypts_pem_and_discards_returned_secrets(tmp_path: Path) -> None:
    db = database(tmp_path)
    instance, store, github, _dispatcher = flow(db, tmp_path)
    user = db.get(User, "u1")
    started = instance.start(user, ManifestStartRequest(owner_type="personal"))
    target = instance.conversion("code-once", started["state"])
    assert target == "https://github.com/apps/spanel-apps-test/installations/new"
    provider = db.scalar(select(GithubProvider))
    assert provider is not None and provider.status == "pending_installation"
    assert provider.tenant_id == "t1"
    assert b"PRIVATE KEY" not in provider.private_key_ciphertext
    assert b"client-secret-canary" not in provider.private_key_ciphertext
    pem = instance.cipher.decrypt_scoped(
        PEM_PURPOSE, provider.tenant_id, provider.id,
        EncryptedValue(
            provider.private_key_ciphertext, provider.private_key_nonce,
            provider.key_version,
        ),
    )
    assert pem == github.pem
    assert instance.conversion("code-once", started["state"]) == "/app/apps?github=error"


def test_install_callback_verifies_with_the_app_before_persisting(tmp_path: Path) -> None:
    db = database(tmp_path)
    instance, store, github, dispatcher = flow(db, tmp_path)
    user = db.get(User, "u1")
    started = instance.start(user, ManifestStartRequest(owner_type="personal"))
    instance.conversion("code", started["state"])
    state = installation_state(store, started)
    target = instance.installation("77", state, "install")
    assert target == "/app/apps?github=installed"
    installation = db.scalar(select(GithubInstallation))
    assert installation is not None and installation.status == "active"
    assert installation.github_installation_id == 77
    assert installation.tenant_id == "t1"
    assert db.get(GithubProvider, installation.provider_id).status == "active"
    operation = db.scalar(select(Operation))
    assert operation.kind == "github_sync"
    assert operation.subject_type == "installation"
    assert operation.subject_id == installation.id
    assert operation.application_id is None
    assert dispatcher.args == [operation.id]
    assert instance.installation("77", state, "install") == "/app/apps?github=error"


@pytest.mark.parametrize("mode", ["wrong_app", "suspended", "spoofed"])
def test_install_callback_fails_closed(tmp_path: Path, mode: str) -> None:
    db = database(tmp_path)
    instance, store, github, dispatcher = flow(db, tmp_path)
    user = db.get(User, "u1")
    started = instance.start(user, ManifestStartRequest(owner_type="personal"))
    instance.conversion("code", started["state"])
    state = installation_state(store, started)
    if mode == "wrong_app":
        github.wrong_app = True
        installation_id = "77"
    elif mode == "suspended":
        github.suspended = True
        installation_id = "77"
    else:
        installation_id = "88"
    assert instance.installation(installation_id, state, "install") == "/app/apps?github=error"
    assert db.scalar(select(GithubInstallation)) is None
    assert dispatcher.args == []


def test_cross_tenant_installation_conflict_is_rejected(tmp_path: Path) -> None:
    db = database(tmp_path)
    instance, store, github, dispatcher = flow(db, tmp_path)
    user = db.get(User, "u1")
    started = instance.start(user, ManifestStartRequest(owner_type="personal"))
    instance.conversion("code", started["state"])
    tenant_provider = GithubProvider(
        id="other-provider", tenant_id="t2", created_by_user_id="u2",
        github_app_id=1111, app_slug="other", name="Other",
        private_key_ciphertext=b"x", private_key_nonce=b"y", key_version=1,
        status="active",
    )
    db.add(tenant_provider)
    db.add(GithubInstallation(
        id="foreign", provider_id="other-provider", tenant_id="t2",
        github_installation_id=77, account_id="5", account_login="acme",
        repository_selection="selected", permissions={}, status="active",
    ))
    db.commit()
    state = installation_state(store, started)
    assert instance.installation("77", state, "install") == "/app/apps?github=error"
    assert db.scalar(select(GithubInstallation.id).where(
        GithubInstallation.id == "foreign"
    )) == "foreign"
    assert dispatcher.args == []


def test_organization_manifest_uses_exact_organization_host(tmp_path: Path) -> None:
    db = database(tmp_path)
    instance, _store, _github, _dispatcher = flow(db, tmp_path)
    response = instance.start(
        db.get(User, "u1"),
        ManifestStartRequest(owner_type="organization", organization="acme"),
    )
    assert response["action_url"] == (
        "https://github.com/organizations/acme/settings/apps/new"
    )


def test_states_are_tenant_bound_one_time_and_expire(tmp_path: Path) -> None:
    store = Store()
    states = OneTimeStates(store)
    state = states.issue(REGISTRATION, {"tenant_id": "t1", "user_id": "u1"})
    assert states.consume(REGISTRATION, state)["tenant_id"] == "t1"
    with pytest.raises(StateInvalid):
        states.consume(REGISTRATION, state)
    expired = states.issue(INSTALLATION, {"tenant_id": "t1"})
    store.values.clear()
    with pytest.raises(StateInvalid):
        states.consume(INSTALLATION, expired)
