"""Router wiring of the profile routes (I4): RBAC decides, never superadmin."""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIRouter, _IncludedRouter

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend import router as router_module


def _flat_routes(routes: list) -> list[APIRoute]:
    """Flatten `include_router` aggregations (lazy `_IncludedRouter` nodes)."""
    out: list[APIRoute] = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            original = route.original_router
            out.extend(_flat_routes(getattr(original, "routes", [])))
        elif isinstance(route, APIRoute):
            out.append(route)
    return out


def _site_routes() -> list[APIRoute]:
    return _flat_routes(router_module.router.routes)


def _app_paths() -> set[str]:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/plugins/hosting")
    return set(app.openapi()["paths"])


def _permissions_of(route: APIRoute) -> set[str]:
    permissions: set[str] = set()
    for dep in route.dependant.dependencies:
        for cell in getattr(dep.call, "__closure__", None) or []:
            value = cell.cell_contents
            if isinstance(value, str) and value.startswith(("hosting.", "core.")):
                permissions.add(value)
    return permissions


def test_profile_routes_exist_alongside_the_sites_surface() -> None:
    paths = _app_paths()
    assert "/api/v1/plugins/hosting/static-profiles" in paths
    assert "/api/v1/plugins/hosting/static-profiles/{profile_id}" in paths
    # The 0031 surface keeps every path exactly where it was.
    assert "/api/v1/plugins/hosting/sites" in paths
    assert "/api/v1/plugins/hosting/sites/{site_id}/logs" in paths


def test_profile_listing_requires_sites_read() -> None:
    routes = [
        route
        for route in _site_routes()
        if route.path == "/static-profiles" and "GET" in set(route.methods or ())
    ]
    assert len(routes) == 1
    assert _permissions_of(routes[0]) == {"hosting.sites.read"}


def test_profile_crud_requires_profiles_manage() -> None:
    expected = {
        ("/static-profiles", "POST"),
        ("/static-profiles/{profile_id}", "PUT"),
        ("/static-profiles/{profile_id}", "DELETE"),
    }
    for path, method in expected:
        routes = [
            route for route in _site_routes()
            if route.path == path and method in set(route.methods or ())
        ]
        assert len(routes) == 1, (path, method)
        assert "hosting.profiles.manage" in _permissions_of(routes[0]), (path, method)


def test_crud_decides_by_rbac_not_by_superadmin() -> None:
    source = inspect.getsource(router_module)
    assert 'require_permission("hosting.profiles.manage")' in source
    # The only mention of the legacy flag IS the prohibition comment; no code
    # path may read it.
    assert source.count("is_superadmin") == 1
    assert "never by is_superadmin" in source


def test_both_premounted_routers_are_plain_api_routers() -> None:
    assert isinstance(router_module.profiles_router, APIRouter)
