"""Encrypted, reusable tenant environment for Apps deployments.

Values never leave the server after a deployment is accepted. They are stored
with the same application-scoped envelope as managed credentials so the next
deployment can reuse them without asking the operator to paste secrets again.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from systutor.core.errors import AppError

from .crypto import EncryptedValue, TokenCipher
from .managed_environment import MANAGED_DATABASE_NAMES
from .models import Application

_PURPOSE = "application-tenant-environment"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _invalid(message: str) -> AppError:
    return AppError(message, status_code=422, code="apps.environment_invalid")


def _validate(entries: list[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_name, raw_value in entries:
        name = str(raw_name)
        value = str(raw_value)
        if not _NAME.fullmatch(name):
            raise _invalid(f"Nombre de variable inválido: {name}")
        if name in MANAGED_DATABASE_NAMES:
            raise _invalid(f"Apps administra la variable {name}")
        if name in values:
            raise _invalid(f"Variable duplicada: {name}")
        if any(character in value for character in "\x00\r\n") or len(value) > 8192:
            raise _invalid(f"Valor inválido para {name}")
        values[name] = value
    return values


def saved_tenant_environment(
    application: Application, cipher: TokenCipher
) -> list[tuple[str, str]]:
    """Stored tenant variables in deterministic order, or an empty list."""
    encrypted = (
        application.tenant_env_ciphertext,
        application.tenant_env_nonce,
        application.tenant_env_key_version,
    )
    if all(value is None for value in encrypted):
        return []
    if any(value is None for value in encrypted):
        raise _invalid("Las variables guardadas están incompletas")
    try:
        plaintext = cipher.decrypt_scoped(
            _PURPOSE,
            application.tenant_id,
            application.id,
            EncryptedValue(encrypted[0], encrypted[1], encrypted[2]),
        )
        values = json.loads(plaintext)
    except Exception as exc:
        raise _invalid("Las variables guardadas no se pueden descifrar") from exc
    if not isinstance(values, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in values.items()
    ):
        raise _invalid("Las variables guardadas son inválidas")
    return list(values.items())


def save_tenant_environment(
    db: Session,
    application: Application,
    cipher: TokenCipher,
    entries: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Validate and persist the tenant environment for later deployments."""
    values = _validate(entries)
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    encrypted = cipher.encrypt_scoped(
        _PURPOSE,
        application.tenant_id,
        application.id,
        payload,
    )
    application.tenant_env_ciphertext = encrypted.ciphertext
    application.tenant_env_nonce = encrypted.nonce
    application.tenant_env_key_version = encrypted.key_version
    db.flush()
    return list(values.items())
