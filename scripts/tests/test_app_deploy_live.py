"""Destructive opt-in Apps API E2E. Never prints credentials or environment values."""
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

from apps.backend.runtime import Runner  # noqa: E402

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="live Apps E2E requires SPANEL_LIVE_TESTS=1 and explicit lab configuration",
)

REPOSITORY = "luc444s/software-de-gestion-para-editores"
BRANCH = "feature/workflow-diseno"
TERMINAL = {"succeeded", "failed", "interrupted"}


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


def containers(runner: Runner) -> dict[str, dict]:
    completed = runner.run(["docker", "ps", "--format", "{{json .}}"])
    rows = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return {row["ID"]: row for row in rows}


def target_container_ids(runner: Runner, project: str) -> set[str]:
    output = runner.run([
        "docker", "ps", "--filter", f"label=io.spanel.app.owner={project}", "--format", "{{.ID}}",
    ]).stdout
    return {line.strip() for line in output.splitlines() if line.strip()}


def queue(api: Api, application_id: str, suffix: str, body: object | None = None) -> dict:
    status, operation = api.request(
        "POST", f"/api/v1/plugins/apps/applications/{application_id}/{suffix}",
        body, key=str(uuid4()),
    )
    assert status == 202
    return operation


def test_real_github_worker_ssh_proxy_flow_leaves_application_running() -> None:
    api = Api()
    runner = Runner("ssh")
    project = os.environ.get("SPANEL_APP_LIVE_RUNTIME_PROJECT", "")
    assert project, "SPANEL_APP_LIVE_RUNTIME_PROJECT is required for non-root proof"
    before = containers(runner)
    foreign_before = set(before) - target_container_ids(runner, project)

    status, connection = api.request("GET", "/api/v1/plugins/apps/github/connection")
    assert status == 200 and connection["connected"] is True
    # Metadata only: OAuth tokens/codes/state/verifiers must never reach this client.
    assert not ({"token", "access_token", "refresh_token", "code", "state", "verifier"} & connection.keys())

    _, applications = api.request("GET", "/api/v1/plugins/apps/applications")
    name = os.environ.get("SPANEL_APP_LIVE_NAME", "Editors live E2E")
    application = next((item for item in applications if item["name"] == name), None)
    if application is None:
        domain = os.environ.get("SPANEL_APP_LIVE_DOMAIN", "")
        service = os.environ.get("SPANEL_APP_LIVE_INGRESS_SERVICE", "frontend")
        port = int(os.environ.get("SPANEL_APP_LIVE_INGRESS_PORT", "80"))
        assert domain, "SPANEL_APP_LIVE_DOMAIN is required"
        status, application = api.request("POST", "/api/v1/plugins/apps/applications", {
            "name": name, "repository": REPOSITORY, "branch": BRANCH,
            "compose_file": os.environ.get("SPANEL_APP_LIVE_COMPOSE_FILE") or None,
            "domain": domain, "ingress_service": service, "ingress_port": port,
        })
        assert status == 201
    assert application["repository"] == REPOSITORY and application["branch"] == BRANCH

    inspect = queue(api, application["id"], "inspect")
    inspected, inspect_phases = poll(api, inspect["operation_id"])
    assert inspected["status"] == "succeeded"
    assert "queued" in inspect_phases or "cloning" in inspect_phases or "auditing" in inspect_phases
    report = inspected["result"]
    assert report["blocking"] is False
    assert not [item for item in report["findings"] if item["severity"] == "error"]

    deploy = queue(api, application["id"], "deployments", {"environment": environment_from_file()})
    deployed, _ = poll(api, deploy["operation_id"])
    assert deployed["status"] == "succeeded"

    after = containers(runner)
    # Apps deployment must not remove resources that predated it (including Hosting).
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

    _, final_application = api.request("GET", f"/api/v1/plugins/apps/applications/{application['id']}")
    assert final_application["status"] == "running"
