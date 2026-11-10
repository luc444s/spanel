"""Renderer mínimo de rutas: reutiliza YAML gestionado y no duplica ingress."""

from __future__ import annotations

import yaml

from plugins.domains.backend.proxy import (
    PathRoute,
    document_path_routes,
    parse_site_document,
    render_site_yaml,
)


def rendered(*routes: PathRoute, https: bool = True) -> dict:
    text = render_site_yaml(
        "api.example.com",
        ["api.example.com"],
        "letsencrypt",
        path_routes=list(routes),
        include_base=False,
    )
    return parse_site_document(text)


def test_route_only_document_has_no_base_router_and_keeps_tls() -> None:
    doc = rendered(
        PathRoute(
            "/api",
            "app-container",
            8000,
            internal_path="/v1",
            strip_path=True,
            entrypoints=("web", "websecure"),
            middlewares=("rate-limit@file",),
            route_id="route-1",
        )
    )
    routers = doc["http"]["routers"]
    assert set(routers) == {
        "spanel-api-example-com-route-route-1",
        "spanel-api-example-com-route-route-1-http",
    }
    secure = routers["spanel-api-example-com-route-route-1"]
    assert secure["tls"] == {"certResolver": "letsencrypt"}
    assert secure["middlewares"] == [
        "spanel-api-example-com-route-route-1-rewrite",
        "rate-limit@file",
    ]
    middleware = doc["http"]["middlewares"][
        "spanel-api-example-com-route-route-1-rewrite"
    ]
    assert middleware["replacePathRegex"]["regex"] == "^/api(.*)"
    assert middleware["replacePathRegex"]["replacement"] == "/v1$1"
    redirect = routers["spanel-api-example-com-route-route-1-http"]
    assert redirect["entryPoints"] == ["web"]


def test_route_without_https_uses_http_entrypoint_only() -> None:
    doc = rendered(PathRoute("/", "app-container", 80, https=False, route_id="r1"))
    router = doc["http"]["routers"]["spanel-api-example-com-route-r1"]
    assert "tls" not in router
    assert router["entryPoints"] == ["web"]


def test_strip_path_without_internal_rewrite_preserves_prefix() -> None:
    doc = rendered(
        PathRoute("/api", "app-container", 80, strip_path=False, route_id="r1")
    )
    router = doc["http"]["routers"]["spanel-api-example-com-route-r1"]
    assert "middlewares" not in router


def test_parser_recognizes_route_router_names() -> None:
    text = render_site_yaml(
        "api.example.com",
        ["api.example.com"],
        "letsencrypt",
        path_routes=[
            PathRoute("/api", "app-container", 8000, route_id="abc"),
            PathRoute("/v1", "other-container", 9000, route_id="def"),
        ],
        include_base=False,
    )
    routes = document_path_routes(yaml.safe_load(text))
    assert [(route.prefix, route.container, route.port) for route in routes] == [
        ("/api", "app-container", 8000),
        ("/v1", "other-container", 9000),
    ]
