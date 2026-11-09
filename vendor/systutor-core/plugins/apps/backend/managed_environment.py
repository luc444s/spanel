"""Stable, engine-neutral application credentials encrypted at rest."""

from __future__ import annotations

import hashlib
import json
import secrets

from sqlalchemy.orm import Session

from systutor.core.errors import AppError

from .crypto import EncryptedValue, TokenCipher
from .models import Application

MANAGED_DATABASE_NAMES = (
    "APP_DB_NAME",
    "APP_DB_USER",
    "APP_DB_PASSWORD",
)
_PURPOSE = "application-managed-environment"


def _database_identity(application: Application) -> str:
    digest = hashlib.sha256(
        f"{application.tenant_id}\0{application.id}".encode()
    ).hexdigest()[:16]
    return f"app_{digest}"


def _decrypt(application: Application, cipher: TokenCipher) -> dict[str, str] | None:
    encrypted = (
        application.managed_env_ciphertext,
        application.managed_env_nonce,
        application.managed_env_key_version,
    )
    if all(value is None for value in encrypted):
        return None
    if any(value is None for value in encrypted):
        raise _invalid("Application credentials are incomplete")
    try:
        plaintext = cipher.decrypt_scoped(
            _PURPOSE,
            application.tenant_id,
            application.id,
            EncryptedValue(encrypted[0], encrypted[1], encrypted[2]),
        )
        values = json.loads(plaintext)
    except Exception as exc:
        raise _invalid("Application credentials cannot be decrypted") from exc
    if (
        not isinstance(values, dict)
        or set(values) != set(MANAGED_DATABASE_NAMES)
        or not all(isinstance(value, str) and value for value in values.values())
    ):
        raise _invalid("Application credentials are invalid")
    return values


def _invalid(message: str) -> AppError:
    return AppError(
        message,
        status_code=500,
        code="apps.managed_environment_invalid",
    )


def managed_database_environment(
    db: Session,
    application: Application,
    cipher: TokenCipher,
) -> dict[str, str]:
    """Return stable credentials, generating them once under a row lock."""

    db.refresh(application, with_for_update=True)
    existing = _decrypt(application, cipher)
    if existing is not None:
        return existing
    identity = _database_identity(application)
    values = {
        "APP_DB_NAME": identity,
        "APP_DB_USER": identity,
        "APP_DB_PASSWORD": secrets.token_hex(32),
    }
    encrypted = cipher.encrypt_scoped(
        _PURPOSE,
        application.tenant_id,
        application.id,
        json.dumps(values, sort_keys=True, separators=(",", ":")),
    )
    application.managed_env_ciphertext = encrypted.ciphertext
    application.managed_env_nonce = encrypted.nonce
    application.managed_env_key_version = encrypted.key_version
    db.flush()
    return values


def merge_managed_environment(
    db: Session,
    application: Application,
    cipher: TokenCipher,
    supplied: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    reserved = {name for name, _value in supplied}.intersection(MANAGED_DATABASE_NAMES)
    if reserved:
        raise AppError(
            f"Apps manages these environment variables: {', '.join(sorted(reserved))}",
            status_code=422,
            code="apps.environment_managed",
        )
    managed = managed_database_environment(db, application, cipher)
    return [*managed.items(), *supplied]


def clear_managed_environment(application: Application) -> None:
    application.managed_env_ciphertext = None
    application.managed_env_nonce = None
    application.managed_env_key_version = None
