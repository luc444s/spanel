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
