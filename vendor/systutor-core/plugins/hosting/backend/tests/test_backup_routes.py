from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from plugins.hosting.backend import backup_tickets
from plugins.hosting.backend import router as router_module
from systutor.core.cache import KEY_PREFIX, MemoryCacheBackend, RedisCacheBackend
from systutor.core.errors import AppError


def _flat_routes(routes: list) -> list[APIRoute]:
    flattened: list[APIRoute] = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            flattened.extend(_flat_routes(route.original_router.routes))
        elif isinstance(route, APIRoute):
            flattened.append(route)
    return flattened


def _permissions(route: APIRoute) -> set[str]:
    found: set[str] = set()
    for dependency in route.dependant.dependencies:
        for cell in getattr(dependency.call, "__closure__", None) or []:
            value = cell.cell_contents
            if isinstance(value, str) and value.startswith("hosting."):
                found.add(value)
    return found


def test_backup_routes_are_narrow_and_safety_has_no_http_surface() -> None:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/plugins/hosting")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/plugins/hosting/sites/{site_id}/backup/export" in paths
    assert "/api/v1/plugins/hosting/sites/{site_id}/backup/import" in paths
    assert "/api/v1/plugins/hosting/sites/{site_id}/backup/download/{ticket}" in paths
    assert not any("safety" in path or "backups" in path for path in paths)

    routes = _flat_routes(router_module.router.routes)
    export = next(route for route in routes if route.path.endswith("/backup/export"))
    restore = next(route for route in routes if route.path.endswith("/backup/import"))
    download = next(route for route in routes if route.path.endswith("/backup/download/{ticket}"))
    assert _permissions(export) == {"hosting.sites.manage"}
    assert _permissions(restore) == {"hosting.sites.manage"}
    assert _permissions(download) == set()
    assert {field.name for field in restore.dependant.body_params} == {"file", "confirmation"}


def test_memory_cache_cannot_issue_backup_links(monkeypatch) -> None:
    monkeypatch.setattr(backup_tickets, "cache", lambda: MemoryCacheBackend())
    with pytest.raises(AppError) as error:
        backup_tickets.issue({"site_id": "site"}, 300)
    assert error.value.status_code == 503
    assert error.value.code == "hosting_backup_ticket_store_unavailable"


class _AtomicRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lock = threading.Lock()

    def setex(self, key: str, _ttl: int, value: str) -> None:
        with self.lock:
            self.values[key] = value

    def getdel(self, key: str) -> str | None:
        with self.lock:
            return self.values.pop(key, None)


def test_ticket_getdel_allows_exactly_one_consumer(monkeypatch) -> None:
    backend = object.__new__(RedisCacheBackend)
    backend._client = _AtomicRedis()
    monkeypatch.setattr(backup_tickets, "cache", lambda: backend)
    payload = {
        "user_id": "user",
        "tenant_id": "tenant",
        "site_id": "site",
        "backup_id": "a" * 32,
        "filename": "backup.zip",
    }
    ticket = backup_tickets.issue(payload, 300)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _index: backup_tickets.consume(ticket), range(24)))

    assert results.count(payload) == 1
    assert results.count(None) == 23
    assert backend._client.values == {}
    assert not any(ticket in value for value in payload.values())
    assert KEY_PREFIX.startswith("systutor:")
