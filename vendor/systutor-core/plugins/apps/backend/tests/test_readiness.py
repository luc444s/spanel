from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.models import GithubConnection
from plugins.apps.backend.readiness import github_status
from plugins.apps.backend.settings import AppsSettings
from systutor.core.database import Base
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant


def _settings(
    tmp_path: Path, *, key: bool = True, public: str = "https://panel.example"
) -> AppsSettings:
    key_file = tmp_path / "apps_key"
    if key:
        key_file.write_text("x" * 32, encoding="utf-8")
    return AppsSettings(
        github_client_id="",
        github_client_secret_file=tmp_path / "missing",
        encryption_key_file=key_file,
        oauth_callback_url="",
        public_url=public,
        redis_url="redis://unused",
        state_root=tmp_path / "state",
    )


def _db(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'apps.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    session.add(User(
        id="u1", tenant_id="t1", email="u@example.test",
        full_name="U", password_hash="x",
    ))
    session.commit()
    return session


def test_status_works_when_the_encryption_key_is_missing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        result = github_status(
            db, tenant_id="t1", user_id="u1", settings=_settings(tmp_path, key=False)
        )
        assert result["state"] == "error"
        assert result["configured"] is False
        assert result["connected"] is False
    finally:
        db.close()


def test_status_is_unconfigured_not_disconnected_without_a_connection(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        result = github_status(
            db, tenant_id="t1", user_id="u1", settings=_settings(tmp_path)
        )
        assert result["state"] == "unconfigured"
        assert result["connected"] is False
        assert result["reconnect_required"] is False
    finally:
        db.close()


def test_status_reads_only_database_state(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        db.add(GithubConnection(
            user_id="u1", github_user_id="42", login="octocat", scopes=["repo"],
            access_ciphertext=b"cipher", access_nonce=b"nonce",
        ))
        db.commit()
        result = github_status(
            db, tenant_id="t1", user_id="u1", settings=_settings(tmp_path)
        )
        assert result["state"] == "connected"
        assert result["login"] == "octocat"
    finally:
        db.close()
