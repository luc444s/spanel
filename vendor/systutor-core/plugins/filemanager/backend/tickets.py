"""One-time browser-native download tickets (never Docker paths or credentials)."""

from __future__ import annotations

import json
import secrets
import threading

from systutor.core.cache import KEY_PREFIX, RedisCacheBackend, cache

_lock = threading.Lock()


def issue(payload: dict[str, str]) -> str:
    ticket = secrets.token_urlsafe(32)
    cache().set("filemanager:download:" + ticket, json.dumps(payload), 30)
    return ticket


def consume(ticket: str) -> dict[str, str] | None:
    key = "filemanager:download:" + ticket
    backend = cache()
    if isinstance(backend, RedisCacheBackend):
        value = backend._client.getdel(KEY_PREFIX + key)
    else:
        with _lock:
            value = backend.get(key)
            backend.delete(key)
    if not value:
        return None
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else None
    except (ValueError, TypeError):
        return None
