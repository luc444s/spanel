#!/usr/bin/env python3
"""Black-box Apps E2E using real login, HTTP APIs, worker, and Docker."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

REPOSITORY = "luc444s/software-de-gestion-para-editores"
BRANCH = "feature/workflow-diseno"
COMPOSE_FILE = "docker-compose.dokploy.yml"
TERMINAL = {"succeeded", "failed", "interrupted"}


class E2EError(RuntimeError):
    """Safe black-box E2E failure."""


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        idempotent: bool = False,
        timeout: int = 60,
    ) -> Any:
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        request = urllib.request.Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=payload,
            headers=headers,
            method=method,
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            content = exc.read()
            message = _api_error(content) or exc.reason
            raise E2EError(f"{method} {path} returned HTTP {exc.code}: {message}") from None
        except urllib.error.URLError as exc:
            raise E2EError(f"{method} {path} could not reach the API: {exc.reason}") from None
        except TimeoutError:
            raise E2EError(f"{method} {path} timed out") from None
        elapsed = time.monotonic() - started
        print(f"  {method} {path} -> {status} ({elapsed:.2f}s)")
        if not content or status == 204:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise E2EError(f"{method} {path} returned invalid JSON") from exc


def _api_error(content: bytes) -> str | None:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return None


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise E2EError("base URL must be an absolute HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise E2EError("base URL must not contain credentials, query, or fragment")
    return value.strip().rstrip("/")


def read_password(path: Path) -> str:
    absolute = path.expanduser().resolve(strict=True)
    info = absolute.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise E2EError("password file must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise E2EError("password file must not be readable by group or others")
    value = absolute.read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        raise E2EError("password file is empty")
    return value


def environment() -> list[list[str]]:
    return [
        [
            "DATABASE_URL",
            (
                "postgresql://${APP_DB_USER}:${APP_DB_PASSWORD}"
                "@database:5432/${APP_DB_NAME}"
            ),
        ],
        ["API_KEY", secrets.token_hex(32)],
        ["JWT_SECRET_KEY", secrets.token_hex(32)],
        ["SECRET_KEY", secrets.token_hex(32)],
        ["CORS_ORIGINS", "http://localhost:8080"],
        ["VITE_API_BASE_URL", "/api"],
        ["VITE_CHAT_ENABLED", "false"],
        ["PAYPAL_ENABLED", "false"],
        ["CLOUDFLARE_R2_ENABLED", "false"],
        ["CLOUDFLARE_R2_ACCESS_KEY_ID", "e2e-disabled"],
        ["CLOUDFLARE_R2_SECRET_ACCESS_KEY", "e2e-disabled"],
        ["CLOUDFLARE_R2_ENDPOINT_URL", "https://r2.invalid"],
        ["CLOUDFLARE_R2_BUCKET", "e2e-disabled"],
        ["WORKER_BASE_URL", "https://worker.invalid"],
        ["CLOUDFLARE_WORKER_SHARED_SECRET", secrets.token_hex(32)],
        ["RUN_DB_MIGRATIONS", "true"],
        ["WAIT_FOR_DB", "true"],
    ]


def poll_operation(
    client: ApiClient, operation_id: str, timeout: int, label: str
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    previous = ""
    while time.monotonic() < deadline:
        operation = client.request(
            "GET", f"/api/v1/plugins/apps/operations/{operation_id}"
        )
        state = f"{operation['status']}/{operation['phase']}"
        if state != previous:
            print(f"  {label}: {state}")
            previous = state
        if operation["status"] in TERMINAL:
            if operation["status"] != "succeeded":
                message = operation.get("public_error") or operation.get("error_code")
                raise E2EError(f"{label} failed: {message or 'unknown operation error'}")
            return operation
        time.sleep(2)
    raise E2EError(f"{label} did not finish within {timeout}s")


def queue(
    client: ApiClient,
    method: str,
    path: str,
    body: object | None,
    timeout: int,
    label: str,
) -> dict[str, Any]:
    accepted = client.request(method, path, body, idempotent=True)
    operation_id = accepted.get("operation_id") if isinstance(accepted, dict) else None
    if not isinstance(operation_id, str):
        raise E2EError(f"{label} did not return an operation ID")
    return poll_operation(client, operation_id, timeout, label)


def find_repository(client: ApiClient) -> tuple[str, str]:
    installations = client.request(
        "GET", "/api/v1/plugins/apps/github/installations"
    )
    for installation in installations:
        installation_id = installation.get("id")
        if not isinstance(installation_id, str):
            continue
        repositories = client.request(
            "GET",
            f"/api/v1/plugins/apps/github/installations/{installation_id}/repositories",
        )
        for repository in repositories:
            if repository.get("full_name") == REPOSITORY:
                repository_id = repository.get("id")
                if isinstance(repository_id, str):
                    return installation_id, repository_id
    raise E2EError(f"GitHub repository {REPOSITORY} is not available to this tenant")


def run(args: argparse.Namespace) -> int:
    base_url = validate_base_url(args.base_url)
    email = os.environ.get("SPANEL_E2E_EMAIL", "").strip()
    password_path = os.environ.get("SPANEL_E2E_PASSWORD_FILE", "").strip()
    if not email or not password_path:
        raise E2EError(
            "SPANEL_E2E_EMAIL and SPANEL_E2E_PASSWORD_FILE are required"
        )
    password = read_password(Path(password_path))
    client = ApiClient(base_url)
    application: dict[str, Any] | None = None
    primary_error: Exception | None = None
    cleanup_error: Exception | None = None
    started = time.monotonic()
    print(f"Black-box target: {base_url}")
    try:
        print("[1/9] Real login")
        login = client.request(
            "POST", "/api/v1/auth/login", {"email": email, "password": password}
        )
        password = ""
        token = login.get("access_token") if isinstance(login, dict) else None
        if not isinstance(token, str) or not token:
            raise E2EError("login did not return an access token")
        client.token = token
        profile = client.request("GET", "/api/v1/auth/me")
        permissions = set(profile.get("permissions", []))
        required = {
            "apps.applications.create",
            "apps.applications.delete",
            "apps.applications.manage",
            "apps.deployments.create",
            "apps.github.connect",
        }
        missing = required.difference(permissions)
        if missing:
            raise E2EError(f"login lacks permissions: {', '.join(sorted(missing))}")

        print("[2/9] Resolve real GitHub installation and repository")
        installation_id, repository_id = find_repository(client)
        suffix = secrets.token_hex(5)
        print("[3/9] Create temporary application through API")
        application = client.request(
            "POST",
            "/api/v1/plugins/apps/applications",
            {
                "name": f"E2E Software Editores {suffix}",
                "installation_id": installation_id,
                "repository_id": repository_id,
                "branch": BRANCH,
                "compose_file": COMPOSE_FILE,
            },
        )
        if not isinstance(application, dict):
            raise E2EError("create application returned an invalid response")
        application_id = application["id"]
        root = f"/api/v1/plugins/apps/applications/{application_id}"

        print("[4/9] Inspect through API and worker")
        queue(client, "POST", f"{root}/inspect", None, args.operation_timeout, "inspect")
        detail = client.request("GET", root)
        candidates = detail.get("compose_candidates", [])
        if COMPOSE_FILE not in candidates:
            raise E2EError("inspection did not persist the expected Compose candidate")

        print("[5/9] Deploy through API with server-managed DB credentials")
        queue(
            client,
            "POST",
            f"{root}/deployments",
            {"environment": environment()},
            args.deploy_timeout,
            "deploy",
        )
        detail = client.request("GET", root)
        services = set(detail.get("services", []))
        if not {"backend", "frontend", "database"}.issubset(services):
            raise E2EError("deployment did not expose the three expected services")

        print("[6/9] Refresh and logs through API")
        queue(client, "POST", f"{root}/actions/refresh", None, args.operation_timeout, "refresh")
        logs = queue(client, "POST", f"{root}/actions/logs", None, args.operation_timeout, "logs")
        if not isinstance(logs.get("result"), dict):
            raise E2EError("logs operation did not return a result")

        print("[7/9] Restart and verify through API")
        queue(client, "POST", f"{root}/actions/restart", None, args.operation_timeout, "restart")
        queue(client, "POST", f"{root}/actions/refresh", None, args.operation_timeout, "post-restart refresh")

        print("[8/9] Stop and start through API")
        queue(client, "POST", f"{root}/actions/stop", None, args.operation_timeout, "stop")
        queue(client, "POST", f"{root}/actions/start", None, args.operation_timeout, "start")
        queue(client, "POST", f"{root}/actions/refresh", None, args.operation_timeout, "post-start refresh")
    except Exception as exc:  # noqa: BLE001 -- API cleanup must always run
        primary_error = exc
    finally:
        password = ""
        if application is not None and client.token:
            print("[9/9] Delete temporary application and purge E2E data through API")
            try:
                queue(
                    client,
                    "DELETE",
                    f"/api/v1/plugins/apps/applications/{application['id']}",
                    {"confirm": application["slug"], "purge_data": True},
                    args.operation_timeout,
                    "delete",
                )
            except Exception as exc:  # noqa: BLE001 -- report cleanup separately
                cleanup_error = exc
        client.token = None

    if primary_error:
        print(f"E2E FAILED: {primary_error}", file=sys.stderr)
    if cleanup_error:
        print(f"E2E CLEANUP FAILED: {cleanup_error}", file=sys.stderr)
    if primary_error or cleanup_error:
        return 1
    print(f"E2E PASSED in {time.monotonic() - started:.2f}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SPANEL_E2E_BASE_URL", "https://core.systutor.com"),
    )
    parser.add_argument("--operation-timeout", type=int, default=300)
    parser.add_argument("--deploy-timeout", type=int, default=2400)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (E2EError, OSError) as exc:
        print(f"E2E FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
