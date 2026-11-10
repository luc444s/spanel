from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("e2e_apps_api", ROOT / "scripts/e2e_apps_api.py")
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e2e
SPEC.loader.exec_module(e2e)


def service(name: str, ports: list[int], health: str = "healthy") -> dict:
    return {"name": name, "ports": ports, "health": health, "created_at": "2026-11-09T00:00:00Z"}


def detail(*services: dict) -> dict:
    return {"active_deployment": {"service_details": list(services)}}


def test_select_http_service_uses_detected_ports_and_health() -> None:
    picked = e2e.select_http_service(detail(
        service("data", [5432]),
        service("worker", [8080]),
        service("edge", [80], "starting"),
    ))
    assert picked["name"] == "worker"

    picked = e2e.select_http_service(detail(
        service("data", [5432]),
        service("worker", [8080], "unhealthy"),
        service("edge", [80]),
    ))
    assert picked["name"] == "edge"


def test_select_http_service_requires_http_port() -> None:
    with pytest.raises(e2e.E2EError, match="HTTP port"):
        e2e.select_http_service(detail(service("data", [5432])))


def test_pick_lab_domain_uses_only_registered_domains() -> None:
    items = [
        {"domain": "mail.example.com", "owner": "hosting"},
        {"domain": "lab.example.com", "owner": "domains"},
        {"domain": "prod.example.com", "owner": "domains"},
    ]
    assert e2e.pick_lab_domain(items, None) == "lab.example.com"
    assert e2e.pick_lab_domain(items, "prod.example.com") == "prod.example.com"
    with pytest.raises(e2e.E2EError, match="not registered"):
        e2e.pick_lab_domain(items, "invented.example.com")
    with pytest.raises(e2e.E2EError, match="--domain"):
        e2e.pick_lab_domain([
            {"domain": "prod.example.com", "owner": "domains"},
            {"domain": "alt.example.com", "owner": "domains"},
        ], None)


def test_route_payload_without_free_host_or_options() -> None:
    available = {"entrypoints": ["web", "websecure"], "middlewares": ["rate-limit@file"]}
    payload = e2e.build_route_payload(
        "lab.example.com", service("api", [8000]), "abc123", available
    )
    assert payload["domain"] == "lab.example.com"
    assert payload["path"] == "/e2e-abc123"
    assert payload["internal_path"] == "/health"
    assert payload["strip_path"] is True
    assert payload["port"] == 8000
    assert payload["https"] is True
    assert payload["entrypoints"] == ["websecure"]
    assert payload["middlewares"] == []
    assert "host" not in payload
    assert set(payload["entrypoints"]) <= set(available["entrypoints"])
    assert e2e.service_subject("a1", "api") == "a1:api"


def test_route_matches_exact_payload_and_signature() -> None:
    available = {"entrypoints": ["websecure"]}
    payload = e2e.build_route_payload(
        "lab.example.com", service("api", [8000]), "abc123", available
    )
    route = {**payload, "id": "r1", "site_id": "a1:api", "available": False}
    assert e2e.route_matches(route, payload)
    assert e2e.route_signature(route)["id"] == "r1"
    assert e2e.route_signature(route) == e2e.route_signature({**route, "available": True})
    assert not e2e.route_matches({**route, "path": "/other"}, payload)


def test_https_route_url_never_bypasses_certificate() -> None:
    assert e2e.https_route_url({"domain": "lab.example.com", "path": "/e2e-a"}) == (
        "https://lab.example.com/e2e-a"
    )
    assert e2e.https_route_url({"domain": "lab.example.com", "path": "/"}) == (
        "https://lab.example.com"
    )


def test_sensitive_values_skip_non_secret_flags() -> None:
    values = e2e.sensitive_values([
        ["VITE_CHAT_ENABLED", "false"],
        ["RUN_DB_MIGRATIONS", "true"],
        ["JWT_SECRET_KEY", "jwt-canary"],
        ["E2E_LOG_CANARY", "canary-value"],
    ])
    assert values == ["jwt-canary", "canary-value"]


def test_redaction_removes_canaries_and_fails_on_leak() -> None:
    canary = "canary-value-123"
    rendered = e2e.redact_text(f"line {canary} line", [canary])
    assert canary not in rendered
    assert "[REDACTED]" in rendered
    e2e.assert_no_secrets(rendered, [canary], "logs")
    with pytest.raises(e2e.E2EError, match="leaked"):
        e2e.assert_no_secrets(f"line {canary} line", [canary], "logs")
    error = None
    try:
        e2e.assert_no_secrets(canary, [canary], "logs")
    except e2e.E2EError as exc:
        error = str(exc)
    assert error is not None and canary not in error


def test_service_snapshot_detects_only_unrelated_restarts() -> None:
    before = {
        "api": {"created_at": "2026-11-09T00:00:00Z", "restart_count": 0},
        "db": {"created_at": "2026-11-09T00:00:00Z", "restart_count": 0},
    }
    assert e2e.changed_services(before, before, "api") == []
    after = {**before, "db": {**before["db"], "restart_count": 1}}
    assert e2e.changed_services(before, after, "api") == ["db"]
    assert e2e.changed_services(before, after, "db") == []


def test_managed_secret_requires_managed_revealable_source() -> None:
    value = detail(
        {
            "name": "api",
            "environment": [
                {"name": "JWT_SECRET_KEY", "source": "tenant", "secret": True, "revealable": False},
                {"name": "APP_DB_PASSWORD", "source": "managed", "secret": True, "revealable": True},
            ],
        }
    )
    assert e2e.managed_secret_name(value, "api") == "APP_DB_PASSWORD"
    hidden = detail(
        {
            "name": "api",
            "environment": [
                {"name": "APP_DB_PASSWORD", "source": "managed", "secret": True, "revealable": False},
            ],
        }
    )
    with pytest.raises(e2e.E2EError, match="revealable managed secret"):
        e2e.managed_secret_name(hidden, "api")


def test_base_url_requires_tls_except_loopback() -> None:
    with pytest.raises(e2e.E2EError, match="HTTPS"):
        e2e.validate_base_url("http://panel.example.com")
    assert e2e.validate_base_url("https://panel.example.com") == "https://panel.example.com"
    assert e2e.validate_base_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_parser_accepts_registered_lab_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPANEL_E2E_DOMAIN", "lab.example.com")
    args = e2e.build_parser().parse_args([])
    assert args.domain == "lab.example.com"
    assert e2e.build_parser().parse_args(["--domain", "other.example.com"]).domain == (
        "other.example.com"
    )


class MissingClient:
    def request(self, method: str, path: str):
        raise e2e.E2EError(f"{method} {path} returned HTTP 404: not found")


class PresentClient:
    def request(self, method: str, path: str):
        return {"id": path}


def test_expect_missing_accepts_only_404() -> None:
    e2e.expect_missing(MissingClient(), "/x", "route")  # type: ignore[arg-type]
    with pytest.raises(e2e.E2EError, match="still exists"):
        e2e.expect_missing(PresentClient(), "/x", "route")  # type: ignore[arg-type]
