from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("spanel_apps", ROOT / "scripts/spanel_apps.py")
assert SPEC and SPEC.loader
apps = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = apps
SPEC.loader.exec_module(apps)


class FakeTransport:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def __call__(self, method, url, *, token, payload=None, idempotency_key=None, timeout=30):
        self.calls.append({
            "method": method, "url": url, "token": token,
            "payload": payload, "key": idempotency_key,
        })
        if not self.responses:
            return {}
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def client(transport: FakeTransport) -> apps.Client:
    return apps.Client(
        "https://panel.example", "token-canary", transport=transport, poll_interval=0
    )


def test_add_uses_internal_ids_and_idempotency() -> None:
    transport = FakeTransport([{"id": "a1"}])
    result = apps.run(
        apps.build_parser().parse_args([
            "add", "--installation", "i1", "--repository", "r1",
            "--name", "Demo", "--branch", "dev",
        ]),
        client(transport),
    )
    assert result == {"id": "a1"}
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/plugins/apps/applications")
    assert call["payload"] == {
        "name": "Demo", "installation_id": "i1", "repository_id": "r1",
        "branch": "dev",
    }
    assert call["key"]


def test_repositories_are_installation_scoped() -> None:
    transport = FakeTransport([[]])
    apps.run(
        apps.build_parser().parse_args(["repositories", "--installation", "i1"]),
        client(transport),
    )
    assert transport.calls[0]["url"].endswith(
        "/api/v1/plugins/apps/github/installations/i1/repositories"
    )


def test_inspect_wait_polls_with_bounded_client() -> None:
    transport = FakeTransport([
        {"operation_id": "op1"},
        {"operation_id": "op1", "status": "running"},
        {"operation_id": "op1", "status": "succeeded"},
    ])
    operation = apps.run(
        apps.build_parser().parse_args(["inspect", "a1", "--wait"]),
        client(transport),
    )
    assert operation["status"] == "succeeded"
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["payload"] == {}
    assert transport.calls[1]["method"] == "GET"


def test_status_timeout_is_bounded() -> None:
    transport = FakeTransport([{"operation_id": "op1", "status": "running"}])
    with pytest.raises(apps.CliError, match="timeout"):
        client(transport).poll("op1", timeout=0)


def test_deploy_env_file_must_be_private_and_values_never_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / "app.env"
    env.write_text("CANARY=top-secret\n", encoding="utf-8")
    env.chmod(0o644)
    transport = FakeTransport()
    with pytest.raises(apps.CliError, match="0600"):
        apps.run(
            apps.build_parser().parse_args(["deploy", "a1", "--env-file", str(env)]),
            client(transport),
        )
    assert transport.calls == []

    env.chmod(0o600)
    transport = FakeTransport([{"operation_id": "op1"}])
    apps.run(
        apps.build_parser().parse_args(["deploy", "a1", "--env-file", str(env)]),
        client(transport),
    )
    assert transport.calls[0]["payload"] == {"environment": [["CANARY", "top-secret"]]}
    assert transport.calls[0]["url"].endswith(
        "/api/v1/plugins/apps/applications/a1/deployments"
    )
    assert "top-secret" not in capsys.readouterr().out


def test_deploy_accepts_one_plain_multiline_env_block() -> None:
    transport = FakeTransport([{"operation_id": "op-inline"}])
    args = apps.build_parser().parse_args([
        "deploy", "a1", "--env", "SMTP_HOST=mail\nAPI_URL=https://api.example?a=b",
    ])
    apps.run(args, client(transport))
    assert transport.calls[0]["payload"] == {
        "environment": [
            ["SMTP_HOST", "mail"],
            ["API_URL", "https://api.example?a=b"],
        ]
    }


def test_deploy_rejects_duplicate_inline_and_file_values(tmp_path: Path) -> None:
    env = tmp_path / "app.env"
    env.write_text("API_KEY=file\n", encoding="utf-8")
    env.chmod(0o600)
    args = apps.build_parser().parse_args([
        "deploy", "a1", "--env-file", str(env), "--env", "API_KEY=inline",
    ])
    with pytest.raises(apps.CliError, match="duplicate"):
        apps.run(args, client(FakeTransport()))


def test_delete_confirms_the_server_side_slug() -> None:
    transport = FakeTransport([{"slug": "demo"}, {"operation_id": "op1"}])
    apps.run(
        apps.build_parser().parse_args(["delete", "a1", "--purge-data"]),
        client(transport),
    )
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[1]["method"] == "DELETE"
    assert transport.calls[1]["payload"] == {"confirm": "demo", "purge_data": True}


def test_cli_has_no_runtime_database_docker_or_ssh_imports() -> None:
    tree = ast.parse((ROOT / "scripts/spanel_apps.py").read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    forbidden = {"app", "systutor", "plugins", "psycopg", "sqlalchemy", "redis", "dramatiq"}
    assert not (roots & forbidden)
    # Docker/SSH are only mentioned in documentation, never imported.
    assert "docker" not in roots and "paramiko" not in roots


def test_access_token_is_read_from_environment_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPANEL_APPS_TOKEN", "env-token")
    assert apps.read_token() == "env-token"
    with pytest.raises(SystemExit):
        apps.build_parser().parse_args(["providers", "--token", "argv-token"])
    assert not any("--token" in argument for argument in sys.argv)


def test_json_output_shape() -> None:
    transport = FakeTransport([[{"id": "p1", "app_slug": "one"}]])
    value = apps.run(
        apps.build_parser().parse_args(["providers"]), client(transport)
    )
    assert json.loads(json.dumps(value))[0]["app_slug"] == "one"


def _detail(service="backend", ports=None):
    return {
        "id": "a1",
        "services": [service],
        "active_deployment": {
            "service_details": [
                {
                    "name": service,
                    "status": "running",
                    "health": "healthy",
                    "ports": [8000] if ports is None else ports,
                }
            ]
        },
    }


def test_services_prints_dynamic_name_status_health_ports(capsys) -> None:
    transport = FakeTransport([_detail("api", [8000])])
    apps.main(["services", "a1"], client(transport))
    assert capsys.readouterr().out == "name=api status=running health=healthy ports=8000\n"


def test_lifecycle_service_selector_is_sent_in_body() -> None:
    transport = FakeTransport([{"operation_id": "op1"}])
    apps.run(
        apps.build_parser().parse_args(["restart", "a1", "--service", "backend"]),
        client(transport),
    )
    assert transport.calls[0]["payload"] == {"service": "backend"}
    assert transport.calls[0]["url"].endswith(
        "/api/v1/plugins/apps/applications/a1/actions/restart"
    )


def test_down_does_not_accept_service_selector() -> None:
    with pytest.raises(SystemExit):
        apps.build_parser().parse_args(["down", "a1", "--service", "backend"])


def test_add_does_not_offer_ingress_flags() -> None:
    with pytest.raises(SystemExit):
        apps.build_parser().parse_args([
            "add", "--installation", "i1", "--repository", "r1", "--name", "Demo",
            "--domain", "api.example.com",
        ])


def test_domain_list_distinguishes_available_and_linked_routes() -> None:
    transport = FakeTransport([
        _detail("backend"),
        {"items": [{"domain": "api.example.com", "published": True}]},
        {"items": [{"id": "r1", "domain": "api.example.com", "site_id": "a1:backend"}]},
    ])
    value = apps.run(
        apps.build_parser().parse_args(["domain-list", "a1", "backend"]),
        client(transport),
    )
    assert value["available"][0]["domain"] == "api.example.com"
    assert value["routes"][0]["id"] == "r1"
    assert transport.calls[1]["url"].endswith("/api/v1/plugins/domains/available")
    assert transport.calls[2]["url"].endswith(
        "/api/v1/plugins/domains/routes?site_id=a1%3Abackend"
    )


def test_domain_assign_uses_registered_domain_and_detected_port() -> None:
    transport = FakeTransport([
        _detail("backend", [8000]),
        {
            "items": [{"domain": "api.example.com"}],
            "entrypoints": ["web", "websecure"],
            "middlewares": ["rate-limit@file"],
        },
        {"id": "r1", "domain": "api.example.com", "path": "/api"},
    ])
    apps.run(
        apps.build_parser().parse_args([
            "domain-assign", "a1", "backend",
            "--domain", "API.example.com",
            "--path", "/api",
            "--entrypoint", "web", "--entrypoint", "websecure",
            "--middleware", "rate-limit@file",
        ]),
        client(transport),
    )
    assert transport.calls[2]["payload"] == {
        "domain": "api.example.com",
        "path": "/api",
        "internal_path": "/",
        "strip_path": False,
        "port": 8000,
        "entrypoints": ["web", "websecure"],
        "https": True,
        "middlewares": ["rate-limit@file"],
        "site_id": "a1:backend",
    }
    assert "container" not in json.dumps(transport.calls[2]["payload"])


def test_domain_assign_rejects_unregistered_domain_without_post() -> None:
    transport = FakeTransport([
        _detail("backend"),
        {"items": [{"domain": "other.example.com"}]},
    ])
    with pytest.raises(apps.CliError, match="not registered"):
        apps.run(
            apps.build_parser().parse_args([
                "domain-assign", "a1", "backend", "--domain", "api.example.com",
            ]),
            client(transport),
        )
    assert [call["method"] for call in transport.calls] == ["GET", "GET"]


def test_domain_assign_requires_port_when_ambiguous() -> None:
    transport = FakeTransport([
        _detail("backend", [8000, 8443]),
        {"items": [{"domain": "api.example.com"}]},
    ])
    with pytest.raises(apps.CliError, match="--port"):
        apps.run(
            apps.build_parser().parse_args([
                "domain-assign", "a1", "backend", "--domain", "api.example.com",
            ]),
            client(transport),
        )


def test_domain_remove_and_probe_validate_route_ownership() -> None:
    transport = FakeTransport([
        _detail("backend"),
        {"id": "r1", "site_id": "a1:backend", "domain": "api.example.com"},
        None,
    ])
    apps.run(
        apps.build_parser().parse_args(["domain-remove", "a1", "backend", "r1"]),
        client(transport),
    )
    assert [call["method"] for call in transport.calls] == ["GET", "GET", "DELETE"]

    probe_transport = FakeTransport([
        _detail("backend"),
        {"id": "r1", "site_id": "a1:backend", "domain": "api.example.com"},
        {"route_id": "r1", "domain": "api.example.com", "http_status": 200},
    ])
    value = apps.run(
        apps.build_parser().parse_args(["domain-probe", "a1", "backend", "r1"]),
        client(probe_transport),
    )
    assert value["http_status"] == 200
    assert probe_transport.calls[2]["url"].endswith(
        "/api/v1/plugins/domains/routes/r1/probe"
    )


def test_domain_remove_rejects_route_of_another_service() -> None:
    transport = FakeTransport([
        _detail("backend"),
        {"id": "r1", "site_id": "a1:worker", "domain": "api.example.com"},
    ])
    with pytest.raises(apps.CliError, match="does not belong"):
        apps.run(
            apps.build_parser().parse_args(["domain-remove", "a1", "backend", "r1"]),
            client(transport),
        )
    assert len(transport.calls) == 2


def test_domain_assign_rejects_port_not_exposed_by_service() -> None:
    transport = FakeTransport([
        _detail("backend", [8000]),
        {"items": [{"domain": "api.example.com"}]},
    ])
    with pytest.raises(apps.CliError, match="not exposed"):
        apps.run(
            apps.build_parser().parse_args([
                "domain-assign", "a1", "backend", "--domain", "api.example.com",
                "--port", "9000",
            ]),
            client(transport),
        )


def test_domain_assign_rejects_options_outside_domains_allowlist() -> None:
    transport = FakeTransport([
        _detail("backend"),
        {
            "items": [{"domain": "api.example.com"}],
            "entrypoints": ["web", "websecure"],
            "middlewares": [],
        },
    ])
    with pytest.raises(apps.CliError, match="Middleware is not allowed"):
        apps.run(
            apps.build_parser().parse_args([
                "domain-assign", "a1", "backend", "--domain", "api.example.com",
                "--middleware", "untrusted@file",
            ]),
            client(transport),
        )
    assert [call["method"] for call in transport.calls] == ["GET", "GET"]

    entrypoint_transport = FakeTransport([
        _detail("backend"),
        {
            "items": [{"domain": "api.example.com"}],
            "entrypoints": ["web", "websecure"],
            "middlewares": [],
        },
    ])
    with pytest.raises(apps.CliError, match="Entrypoint is not allowed"):
        apps.run(
            apps.build_parser().parse_args([
                "domain-assign", "a1", "backend", "--domain", "api.example.com",
                "--entrypoint", "admin",
            ]),
            client(entrypoint_transport),
        )
