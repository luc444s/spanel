from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

KEY_PREFIX = "systutor:cache:"


def _prefixed(key: str) -> str:
    return f"{KEY_PREFIX}{key}"


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class MemoryCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        entry = self._store.get(_prefixed(key))
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[_prefixed(key)]
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._store[_prefixed(key)] = (time.monotonic() + ttl, value)

    def delete(self, key: str) -> None:
        self._store.pop(_prefixed(key), None)

    def clear(self) -> None:
        self._store.clear()

    def close(self) -> None:
        self._store.clear()


class RedisCacheBackend(CacheBackend):
    def __init__(self, redis_url: str) -> None:
        import redis as _redis

        self._client: Any = _redis.from_url(redis_url, decode_responses=True)
        self._client.ping()

    def get(self, key: str) -> str | None:
        val = self._client.get(_prefixed(key))
        return val if val is not None else None

    def set(self, key: str, value: str, ttl: int) -> None:
        self._client.setex(_prefixed(key), ttl, value)

    def delete(self, key: str) -> None:
        self._client.delete(_prefixed(key))

    def clear(self) -> None:
        cursor = 0
        while True:
            cursor, keys = self._client.scan(cursor, match=f"{KEY_PREFIX}*", count=500)
            if keys:
                self._client.delete(*keys)
            if cursor == 0:
                break

    def close(self) -> None:
        self._client.close()


_cache: CacheBackend | None = None


def init_cache(redis_url: str | None = None) -> CacheBackend:
    global _cache
    if _cache is not None:
        return _cache
    if redis_url:
        try:
            _cache = RedisCacheBackend(redis_url)
            logger.info("cache: usando RedisCacheBackend")
            return _cache
        except Exception as exc:
            logger.warning("cache: Redis no disponible (%s), usando MemoryCacheBackend", exc)
    _cache = MemoryCacheBackend()
    logger.info("cache: usando MemoryCacheBackend")
    return _cache


def close_cache() -> None:
    global _cache
    if _cache is not None:
        _cache.close()
        _cache = None


def cache() -> CacheBackend:
    backend = _cache
    if backend is None:
        backend = init_cache()
    return backend
