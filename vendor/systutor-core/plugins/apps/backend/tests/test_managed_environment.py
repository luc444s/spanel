from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.managed_environment import (
    MANAGED_DATABASE_NAMES,
    clear_managed_environment,
    managed_database_environment,
    merge_managed_environment,
)
from plugins.apps.backend.models import Application
from systutor.core.database import Base
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant


def database(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'managed.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Tenant(id="t1", name="Tenant", slug="tenant"))
    db.add(User(
        id="u1", tenant_id="t1", email="u@example.test", full_name="U", password_hash="x",
    ))
    for identifier in ("a1", "a2"):
        db.add(Application(
            id=identifier, tenant_id="t1", name=identifier, slug=identifier,
            repository=f"acme/{identifier}", branch="main", source_connection_user_id="u1",
            runtime_project=f"app-{identifier}",
        ))
    db.commit()
    return db


def test_managed_database_environment_is_stable_unique_and_encrypted(tmp_path: Path) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    first = db.get(Application, "a1")
    second = db.get(Application, "a2")

    values = managed_database_environment(db, first, cipher)
    again = managed_database_environment(db, first, cipher)
    other = managed_database_environment(db, second, cipher)

    assert values == again
    assert set(values) == set(MANAGED_DATABASE_NAMES)
    assert values["APP_DB_NAME"] == values["APP_DB_USER"]
    assert values["APP_DB_NAME"].startswith("app_")
    assert len(values["APP_DB_PASSWORD"]) == 64
    assert values != other
    stored = first.managed_env_ciphertext
    assert stored is not None
    assert all(value.encode() not in stored for value in values.values())


def test_merge_prepends_managed_values_and_rejects_overrides(tmp_path: Path) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    app = db.get(Application, "a1")
    merged = merge_managed_environment(db, app, cipher, [("SMTP_HOST", "mail")])
    assert [name for name, _value in merged[:3]] == list(MANAGED_DATABASE_NAMES)
    assert merged[-1] == ("SMTP_HOST", "mail")
    with pytest.raises(AppError) as error:
        merge_managed_environment(db, app, cipher, [("APP_DB_PASSWORD", "override")])
    assert error.value.status_code == 422
    assert error.value.code == "apps.environment_managed"


def test_explicit_clear_removes_only_encrypted_envelope(tmp_path: Path) -> None:
    db = database(tmp_path)
    cipher = TokenCipher(b"k" * 32)
    app = db.get(Application, "a1")
    managed_database_environment(db, app, cipher)
    clear_managed_environment(app)
    assert app.managed_env_ciphertext is None
    assert app.managed_env_nonce is None
    assert app.managed_env_key_version is None
