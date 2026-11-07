"""Redis-only, one-time tickets for private hosting backup exports."""

from __future__ import annotations

import json
import secrets

from systutor.core.cache import KEY_PREFIX, RedisCacheBackend, cache
from systutor.core.errors import AppError

_PREFIX = "hosting:backup:download:"


def _redis() -> RedisCacheBackend:
    backend = cache()
    if not isinstance(backend, RedisCacheBackend):
        raise AppError(
            "El almacén de enlaces temporales no está disponible",
            status_code=503,
            code="hosting_backup_ticket_store_unavailable",
        )
    return backend


def require_store() -> None:
    """Fail before creating an export when atomic GETDEL is unavailable."""
    _redis()


def issue(payload: dict[str, str], ttl: int) -> str:
    backend = _redis()
    ticket = secrets.token_urlsafe(32)
    try:
        backend.set(_PREFIX + ticket, json.dumps(payload, separators=(",", ":")), ttl)
    except Exception as exc:
        raise AppError(
            "El almacén de enlaces temporales no está disponible",
            status_code=503,
            code="hosting_backup_ticket_store_unavailable",
        ) from exc
    return ticket


def consume(ticket: str) -> dict[str, str] | None:
    """Atomically consume a ticket across every web worker."""
    try:
        backend = _redis()
        raw = backend._client.getdel(KEY_PREFIX + _PREFIX + ticket)
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        return None
    return payload
