"""Opt-in destructive Apps E2E against a real GitHub App installation.

Never prints credentials, tokens, PEMs or environment values. With
``SPANEL_LIVE_TESTS=0`` the module reports an explicit skip.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = ROOT / "vendor" / "systutor-core" / "plugins"
sys.path.insert(0, str(PLUGIN_ROOT))

from apps.backend.runtime import Runner

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="live Apps E2E requires SPANEL_LIVE_TESTS=1 and explicit lab configuration",
)

TERMINAL = {"succeeded", "failed", "interrupted"}
SECRET_KEYS = {
    "token", "access_token", "refresh_token", "private_key", "pem",
    "ciphertext", "nonce", "client_secret", "webhook_secret", "code", "state",
    "verifier", "jwt", "installation_token",
}


class Api:
    def __init__(self) -> None:
        self.base = os.environ.get("SPANEL_LIVE_API_URL", "").rstrip("/")
        self.token = os.environ.get("SPANEL_LIVE_ACCESS_TOKEN", "")
        if not self.base or not self.token:
            pytest.fail("SPANEL_LIVE_API_URL and SPANEL_LIVE_ACCESS_TOKEN are required")
        self.context = ssl.create_default_context()

    def request(self, method: str, path: str, body: object | None = None, *, key: str | None = None):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            f"{self.base}{path}", method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Do not include the response body: a lab error must not leak secrets.
            raise AssertionError(f"Apps API returned HTTP {exc.code} for {method} {path}") from None


def poll(api: Api, operation_id: str, timeout: int = 1800) -> tuple[dict, set[str]]:
    deadline = time.monotonic() + timeout
    phases: set[str] = set()
    while time.monotonic() < deadline:
        status, operation = api.request("GET", f"/api/v1/plugins/apps/operations/{operation_id}")
        assert status == 200
        phases.add(operation["phase"])
        if operation["status"] in TERMINAL:
            return operation, phases
        time.sleep(2)
    pytest.fail("Apps operation did not reach a terminal status")


def environment_from_file() -> list[list[str]]:
    path_text = os.environ.get("SPANEL_APP_LIVE_ENV_FILE", "")
    if not path_text:
        pytest.fail("SPANEL_APP_LIVE_ENV_FILE is required")
    entries: list[list[str]] = []
    for line in Path(path_text).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            entries.append([name.strip(), value])
    return entries


def assert_no_secret_keys(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert key.lower() not in SECRET_KEYS, f"secret field at {path}.{key}"
            assert_no_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_secret_keys(item, f"{path}[{index}]")


def queue(api: Api, application_id: str, suffix: str, body: object | None = None) -> dict:
    status, operation = api.request(
        "POST", f"/api/v1/plugins/apps/applications/{application_id}/{suffix}",
        body, key=str(uuid4()),
    )
    assert status == 202
    return operation


def evidence(path_env: str, payload: dict) -> None:
    target = os.environ.get(path_env, "")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    path.chmod(0o600)


def target_container_ids(runner: Runner, project: str) -> set[str]:
    output = runner.run([
        "docker", "ps", "--filter", f"label=io.spanel.app.owner={project}", "--format", "{{.ID}}",
    ]).stdout
    return {line.strip() for line in output.splitlines() if line.strip()}


def select_source(api: Api) -> tuple[str, str, str]:
    _, providers = api.request("GET", "/api/v1/plugins/apps/github/providers")
    _, installations = api.request("GET", "/api/v1/plugins/apps/github/installations")
    assert_no_secret_keys(providers)
    assert_no_secret_keys(installations)
    active = [row for row in installations if row["status"] == "active"]
    assert active, "no active GitHub installation"
    installation_id = os.environ.get("SPANEL_APP_LIVE_INSTALLATION_ID") or active[0]["id"]
    _, repositories = api.request(
        "GET", f"/api/v1/plugins/apps/github/installations/{installation_id}/repositories"
    )
    assert_no_secret_keys(repositories)
    assert repositories, "installation has no authorized repositories"
    repository_id = os.environ.get("SPANEL_APP_LIVE_REPOSITORY_ID") or repositories[0]["id"]
    selected = next(row for row in repositories if row["id"] == repository_id)
    branch = os.environ.get("SPANEL_APP_LIVE_BRANCH") or selected["default_branch"] or "main"
    return installation_id, repository_id, branch


def test_real_github_worker_ssh_proxy_flow_leaves_application_running() -> None:
    api = Api()
    runner = Runner("ssh")
    project = os.environ.get("SPANEL_APP_LIVE_RUNTIME_PROJECT", "")
    assert project, "SPANEL_APP_LIVE_RUNTIME_PROJECT is required for non-root proof"
    before = {
        row["ID"]: row for row in (
            json.loads(line) for line in runner.run(
                ["docker", "ps", "--format", "{{json .}}"]
            ).stdout.splitlines() if line.strip()
        )
    }
    foreign_before = set(before) - target_container_ids(runner, project)

    status, readiness = api.request("GET", "/api/v1/plugins/apps/github/status")
    assert status == 200
    assert readiness["state"] in {"connected", "syncing"}
    assert_no_secret_keys(readiness)
    installation_id, repository_id, branch = select_source(api)

    _, applications = api.request("GET", "/api/v1/plugins/apps/applications")
    name = os.environ.get("SPANEL_APP_LIVE_NAME", "Editors live E2E")
    application = next((item for item in applications if item["name"] == name), None)
    if application is None:
        domain = os.environ.get("SPANEL_APP_LIVE_DOMAIN", "")
        service = os.environ.get("SPANEL_APP_LIVE_INGRESS_SERVICE", "frontend")
        port = int(os.environ.get("SPANEL_APP_LIVE_INGRESS_PORT", "80"))
        assert domain, "SPANEL_APP_LIVE_DOMAIN is required"
        status, application = api.request("POST", "/api/v1/plugins/apps/applications", {
            "name": name, "installation_id": installation_id,
            "repository_id": repository_id, "branch": branch,
            "compose_file": os.environ.get("SPANEL_APP_LIVE_COMPOSE_FILE") or None,
            "domain": domain, "ingress_service": service, "ingress_port": port,
        })
        assert status == 201
    assert application["source_installation_id"] == installation_id
    assert application["source_repository_id"] == repository_id

    inspect = queue(api, application["id"], "inspect")
    inspected, inspect_phases = poll(api, inspect["operation_id"])
    assert inspected["status"] == "succeeded"
    assert "queued" in inspect_phases or "cloning" in inspect_phases or "auditing" in inspect_phases
    report = inspected["result"]
    assert report["blocking"] is False
    assert not [item for item in report["findings"] if item["severity"] == "error"]

    first = queue(api, application["id"], "deployments", {"environment": environment_from_file()})
    deployed, _ = poll(api, first["operation_id"])
    assert deployed["status"] == "succeeded"
    first_deployment = deployed["result"]["deployment_id"]

    after = {
        row["ID"]: row for row in (
            json.loads(line) for line in runner.run(
                ["docker", "ps", "--format", "{{json .}}"]
            ).stdout.splitlines() if line.strip()
        )
    }
    assert foreign_before.issubset(after)
    target_rows = [after[identifier] for identifier in target_container_ids(runner, project)]
    assert len(target_rows) == 3
    assert all("healthy" in row.get("Status", "").lower() for row in target_rows)
    for row in target_rows:
        identifier = row["ID"]
        user = runner.run(["docker", "inspect", "--format", "{{.Config.User}}", identifier]).stdout.strip()
        assert user not in {"", "0", "root", "0:0", "root:root"}
        top = runner.run(["docker", "top", identifier, "-eo", "user="]).stdout.splitlines()
        assert top and all(value.strip() not in {"", "root", "0"} for value in top)

    domain = application["domain"]
    with urllib.request.urlopen(f"https://{domain}", timeout=30, context=ssl.create_default_context()) as response:
        assert 200 <= response.status < 400

    for action in ("refresh", "logs", "stop", "start", "restart"):
        accepted = queue(api, application["id"], f"actions/{action}")
        completed, _ = poll(api, accepted["operation_id"])
        assert completed["status"] == "succeeded"

    # A second deployment is immutable: new deployment id, previous history kept.
    second = queue(api, application["id"], "deployments", {"environment": environment_from_file()})
    deployed_second, _ = poll(api, second["operation_id"])
    assert deployed_second["status"] == "succeeded"
    second_deployment = deployed_second["result"]["deployment_id"]
    assert second_deployment != first_deployment
    _, detail = api.request("GET", f"/api/v1/plugins/apps/applications/{application['id']}")
    deployment_ids = {row["id"] for row in detail["deployments"]}
    assert {first_deployment, second_deployment} <= deployment_ids
    assert detail["active_deployment"]["id"] == second_deployment
    _, operations = api.request(
        "GET", f"/api/v1/plugins/apps/applications/{application['id']}/operations"
    )
    assert {first["operation_id"], second["operation_id"]} <= {row["id"] for row in operations}

    _, final_application = api.request("GET", f"/api/v1/plugins/apps/applications/{application['id']}")
    assert final_application["status"] == "running"
    evidence("SPANEL_APP_LIVE_EVIDENCE", {
        "application_id": application["id"],
        "installation_id": installation_id,
        "repository_id": repository_id,
        "deployments": [first_deployment, second_deployment],
        "operations": [first["operation_id"], second["operation_id"]],
        "domain": domain,
        "status": final_application["status"],
        "services": detail["services"],
    })


@pytest.mark.skipif(
    not os.environ.get("SPANEL_APP_LIVE_BAD_BRANCH"),
    reason="unhealthy-release rollback needs SPANEL_APP_LIVE_BAD_BRANCH",
)
def test_unhealthy_release_restores_previous_deployment() -> None:
    api = Api()
    _, applications = api.request("GET", "/api/v1/plugins/apps/applications")
    name = os.environ.get("SPANEL_APP_LIVE_NAME", "Editors live E2E")
    application = next((item for item in applications if item["name"] == name), None)
    assert application, "run the healthy flow first"
    _, before = api.request("GET", f"/api/v1/plugins/apps/applications/{application['id']}")
    active_before = before["active_deployment"]["id"]
    good_branch = application["branch"]
    installation_id, repository_id, _ = select_source(api)
    status, _ = api.request(
        "PATCH", f"/api/v1/plugins/apps/applications/{application['id']}/source", {
            "installation_id": installation_id, "repository_id": repository_id,
            "branch": os.environ["SPANEL_APP_LIVE_BAD_BRANCH"],
        },
    )
    assert status == 200
    try:
        deploy = queue(api, application["id"], "deployments", {"environment": environment_from_file()})
        failed, _ = poll(api, deploy["operation_id"])
        assert failed["status"] == "failed"
        _, after = api.request("GET", f"/api/v1/plugins/apps/applications/{application['id']}")
        assert after["active_deployment"]["id"] == active_before
    finally:
        api.request(
            "PATCH", f"/api/v1/plugins/apps/applications/{application['id']}/source", {
                "installation_id": installation_id, "repository_id": repository_id,
                "branch": good_branch,
            },
        )
