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
        "spanel-api-example-com-route-route-1-rewrite-strip",
        "spanel-api-example-com-route-route-1-rewrite-add",
        "rate-limit@file",
    ]
    middlewares = doc["http"]["middlewares"]
    assert middlewares[
        "spanel-api-example-com-route-route-1-rewrite-strip"
    ]["stripPrefix"]["prefixes"] == ["/api"]
    assert middlewares[
        "spanel-api-example-com-route-route-1-rewrite-add"
    ]["addPrefix"]["prefix"] == "/v1"
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


def test_root_path_applies_internal_prefix_and_never_strips_root() -> None:
    for strip in (False, True):
        doc = rendered(
            PathRoute(
                "/", "app-container", 80, internal_path="/v1", strip_path=strip,
                route_id="r1",
            )
        )
        router = doc["http"]["routers"]["spanel-api-example-com-route-r1"]
        name = "spanel-api-example-com-route-r1-rewrite"
        assert router["middlewares"] == [name]
        rewrite = doc["http"]["middlewares"][name]["replacePathRegex"]
        assert rewrite == {"regex": "^/(.*)", "replacement": "/v1/$1"}
        assert "stripPrefix" not in str(doc["http"]["middlewares"])


def test_root_with_root_internal_path_has_no_rewrite() -> None:
    for strip in (False, True):
        doc = rendered(
            PathRoute(
                "/", "app-container", 80, internal_path="/", strip_path=strip,
                route_id="r1",
            )
        )
        router = doc["http"]["routers"]["spanel-api-example-com-route-r1"]
        assert "middlewares" not in router
        assert not any(
            "rewrite" in name for name in doc["http"]["middlewares"]
        )
        assert "stripPrefix" not in str(doc["http"]["middlewares"])


def test_internal_path_without_strip_accepts_exact_prefix() -> None:
    doc = rendered(
        PathRoute(
            "/api", "app-container", 80, internal_path="/v1", strip_path=False,
            route_id="r1",
        )
    )
    name = "spanel-api-example-com-route-r1-rewrite"
    assert doc["http"]["routers"]["spanel-api-example-com-route-r1"]["middlewares"] == [name]
    rewrite = doc["http"]["middlewares"][name]["replacePathRegex"]
    assert rewrite["regex"] == "^/api(.*)"
    assert rewrite["replacement"] == "/v1$1"


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
