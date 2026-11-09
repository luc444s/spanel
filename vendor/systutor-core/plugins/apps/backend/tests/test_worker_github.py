from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend import worker as worker_module
from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.github import InstallationToken
from plugins.apps.backend.models import (
    Application,
    Deployment,
    GithubInstallation,
    GithubProvider,
    GithubRepository,
    Operation,
)
from plugins.apps.backend.worker import AppsWorker
from systutor.core.database import Base
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant

TOKEN = "canary-installation-token-value"


def private_pem() -> str:
    return rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def database(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    db.add(User(
        id="u1", tenant_id="t1", email="u@example.test", full_name="U", password_hash="x",
    ))
    db.commit()
    return db


class FakeGithub:
    def __init__(self) -> None:
        self.pages: dict[int, list[dict]] = {}
        self.token_calls: list[dict] = []
        self.list_tokens: list[str] = []

    def exchange_manifest(self, code: str):
        raise AssertionError("not used")

    def installation(self, jwt: str, installation_id: int):
        raise AssertionError("not used")

    def installation_token(self, jwt: str, installation_id: int, **kwargs):
        assert jwt.startswith("eyJ")
        self.token_calls.append({"installation_id": installation_id, **kwargs})
        return InstallationToken(TOKEN, datetime.now(UTC) + timedelta(minutes=55))

    def installation_repositories(self, token: str, page: int, per_page: int = 100):
        assert token == TOKEN
        self.list_tokens.append(token)
        return {"repositories": self.pages.get(page, [])}


def seed(db: Session, cipher: TokenCipher) -> None:
    pem = private_pem()
    encrypted = cipher.encrypt_scoped(
        "github-app-private-key", "t1", "p1", pem
    )
    db.add(GithubProvider(
        id="p1", tenant_id="t1", created_by_user_id="u1", github_app_id=4242,
        app_slug="one", name="One", private_key_ciphertext=encrypted.ciphertext,
        private_key_nonce=encrypted.nonce, key_version=encrypted.key_version,
        status="active",
    ))
    db.add(GithubInstallation(
        id="i1", provider_id="p1", tenant_id="t1", github_installation_id=77,
        account_id="5", account_login="acme", repository_selection="selected",
        permissions={"contents": "read"}, status="active",
    ))
    db.add(GithubRepository(
        id="r1", installation_id="i1", tenant_id="t1", github_repository_id=101,
        full_name="acme/repo", private=True, default_branch="main",
        archived=False, disabled=False,
    ))
    db.add(GithubRepository(
        id="r2", installation_id="i1", tenant_id="t1", github_repository_id=102,
        full_name="acme/removed", private=True, default_branch="main",
        archived=False, disabled=False,
    ))
    db.add(Application(
        id="a1", tenant_id="t1", name="Demo", slug="demo", repository="acme/repo",
        branch="main", source_connection_user_id="u1", source_installation_id="i1",
        source_repository_id="r1", source_status="connected", runtime_project="app-demo",
    ))
    db.add(Application(
        id="a2", tenant_id="t1", name="Removed", slug="removed", repository="acme/removed",
        branch="main", source_connection_user_id="u1", source_installation_id="i1",
        source_repository_id="r2", source_status="connected", runtime_project="app-removed",
    ))
    db.commit()


def test_run_logs_internal_failure_with_operation_context(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    seed(db, cipher)
    db.add(Operation(
        id="op-failed", application_id="a1", tenant_id="t1", actor_user_id="u1",
        kind="deploy", status="queued", idempotency_key="failed", request_hash="h",
    ))
    db.commit()
    worker = AppsWorker(lambda: Session(db.get_bind()), cipher, github=FakeGithub())

    def fail_source(*_args, **_kwargs):
        raise RuntimeError("build exploded")

    monkeypatch.setattr(worker, "_source_operation", fail_source)
    with caplog.at_level(logging.ERROR, logger=worker_module.__name__):
        worker.run("op-failed")

    record = next(
        item for item in caplog.records if item.message.startswith("apps_operation_failed")
    )
    assert "operation_id=op-failed" in record.getMessage()
    assert record.operation_id == "op-failed"
    assert record.application_id == "a1"
    assert record.operation_kind == "deploy"
    assert record.error_type == "RuntimeError"
    assert "build exploded" in caplog.text
    db.expire_all()
    operation = db.get(Operation, "op-failed")
    assert operation.status == "failed"
    assert operation.public_error == "Application operation failed"


def test_sync_paginates_tombstones_and_never_persists_the_token(tmp_path: Path) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    seed(db, cipher)
    db.add(Operation(
        id="op1", application_id=None, tenant_id="t1", actor_user_id="u1",
        subject_type="installation", subject_id="i1", kind="github_sync",
        status="running", idempotency_key="k", request_hash="h",
    ))
    db.commit()
    github = FakeGithub()
    github.pages[1] = [
        {"id": 101, "full_name": "acme/repo", "private": True, "default_branch": "main"},
        {"id": 103, "full_name": "acme/new", "private": False, "default_branch": "dev"},
    ]
    worker = AppsWorker(lambda: Session(db.get_bind()), cipher, github=github)
    result = worker._sync_operation("op1", "installation", "i1")
    assert result["repositories"] == 2
    assert set(github.list_tokens) == {TOKEN}
    assert len(github.list_tokens) == 2
    assert github.token_calls == [{
        "installation_id": 77, "permissions": {"metadata": "read"},
    }]
    db.expire_all()
    assert db.get(GithubRepository, "r1").removed_at is None
    assert db.get(GithubRepository, "r2").removed_at is not None
    assert db.scalar(select(GithubRepository.id).where(
        GithubRepository.github_repository_id == 103
    )) is not None
    assert db.get(Application, "a2").source_status == "source_reconnect_required"
    assert db.get(GithubInstallation, "i1").last_synced_at is not None
    _assert_token_absent(db)


def test_source_operation_requests_a_single_repository_token(
    tmp_path: Path, monkeypatch
) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    seed(db, cipher)
    github = FakeGithub()
    captured: dict[str, str] = {}

    def fake_clone(repo, branch, destination, runner):
        captured["repo"] = repo
        captured["branch"] = branch
        captured["token"] = runner.environ["GH_TOKEN"]
        captured["prompt"] = runner.environ["GH_PROMPT_DISABLED"]

    def fake_audit(root, compose, non_root=True):
        root_path = Path(root)
        return SimpleNamespace(
            root=root_path, compose=root_path / "compose.yaml",
            findings=[], errors=[], data={"services": {"web": {}}}, dockerfiles={},
        )

    monkeypatch.setattr(worker_module, "clone_repo", fake_clone)
    monkeypatch.setattr(worker_module, "audit_project", fake_audit)
    worker = AppsWorker(
        lambda: Session(db.get_bind()), cipher, github=github,
        runner_factory=lambda transport, environ=None: SimpleNamespace(
            transport=transport, environ=environ or {}
        ),
    )
    report = worker._source_operation("op2", "a1", "inspect")
    assert report["blocking"] is False
    assert report["services"] == ["web"]
    assert captured == {
        "repo": "acme/repo", "branch": "main",
        "token": TOKEN, "prompt": "1",
    }
    assert github.token_calls == [{
        "installation_id": 77, "repository_id": 101,
        "permissions": {"contents": "read"},
    }]
    _assert_token_absent(db)


def test_successful_inspect_updates_application_summary(tmp_path: Path) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    seed(db, cipher)
    app = db.get(Application, "a1")
    app.active_operation_id = "op-inspect"
    db.add(Operation(
        id="op-inspect", application_id="a1", tenant_id="t1", actor_user_id="u1",
        kind="inspect", status="running", idempotency_key="inspect", request_hash="h",
    ))
    db.commit()
    worker = AppsWorker(lambda: Session(db.get_bind()), cipher, github=FakeGithub())
    worker._finish("op-inspect", {
        "compose": "docker-compose.yml", "services": ["api", "web"],
        "findings": [], "blocking": False,
    })
    db.expire_all()
    operation = db.get(Operation, "op-inspect")
    assert operation.status == "succeeded"
    assert app.status == "inspected"
    assert app.compose_file == "docker-compose.yml"
    assert app.active_operation_id is None


def _assert_token_absent(db: Session) -> None:
    for operation in db.scalars(select(Operation)).all():
        assert TOKEN not in json.dumps({
            "report": operation.report, "result": operation.result,
            "error": operation.public_error,
        })
    for deployment in db.scalars(select(Deployment)).all():
        assert TOKEN not in json.dumps(deployment.manifest or {})
    for provider in db.scalars(select(GithubProvider)).all():
        assert TOKEN.encode() not in provider.private_key_ciphertext
