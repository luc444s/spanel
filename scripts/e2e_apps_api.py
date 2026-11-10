#!/usr/bin/env python3
"""Black-box Apps E2E using real login, HTTP APIs, worker, and Docker."""

from __future__ import annotations

import argparse
import ipaddress
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
DOMAINS_ROOT = "/api/v1/plugins/domains"
HTTP_PORTS = frozenset({80, 443, 3000, 3001, 5000, 8000, 8080, 8081, 8088, 8443})


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
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise E2EError("base URL must be an absolute HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise E2EError("base URL must not contain credentials, query, or fragment")
    loopback = parsed.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not loopback:
        raise E2EError("base URL must use HTTPS unless it is loopback")
    return value.strip().rstrip("/")


def service_subject(application_id: str, service: str) -> str:
    """Domains route site id for one dynamic Compose service."""
    return f"{application_id}:{service}"


def select_http_service(detail: dict[str, Any]) -> dict[str, Any]:
    """Pick an HTTP service from detected ports, never from a fixed name."""
    active = detail.get("active_deployment")
    candidates = active.get("service_details") if isinstance(active, dict) else None
    if not isinstance(candidates, list):
        raise E2EError("deployment did not expose service_details")
    matches: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        ports = item.get("ports")
        if not isinstance(name, str) or not name or not isinstance(ports, list):
            continue
        if any(isinstance(port, int) and port in HTTP_PORTS for port in ports):
            matches.append(item)
    if not matches:
        raise E2EError("deployment did not expose a service with an HTTP port")
    matches.sort(key=lambda item: (
        str(item.get("health") or "").lower() != "healthy", str(item["name"])
    ))
    return matches[0]


def registered_domains(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    names = {
        str(item["domain"])
        for item in items
        if isinstance(item, dict)
        and item.get("owner") == "domains"
        and isinstance(item.get("domain"), str)
        and item["domain"].strip()
    }
    return sorted(names)


def pick_lab_domain(items: object, requested: str | None) -> str:
    """Use a Domains-registered lab host; never invent a host."""
    names = registered_domains(items)
    if not names:
        raise E2EError("Domains has no registered domains for this tenant")
    if requested:
        needle = requested.strip().lower().removesuffix(".")
        for name in names:
            if name.lower().removesuffix(".") == needle:
                return name
        raise E2EError(f"domain {requested} is not registered in Domains")
    lab = [name for name in names if "lab" in name.lower()]
    if len(lab) == 1:
        return lab[0]
    if len(names) == 1:
        return names[0]
    raise E2EError("set --domain to one of the registered lab domains")


def https_entrypoints(available: dict[str, Any]) -> list[str]:
    values = [
        str(value) for value in available.get("entrypoints") or []
        if isinstance(value, str) and value.strip()
    ]
    secure = [
        value for value in values
        if "secure" in value.lower() or "https" in value.lower()
    ]
    return secure or values


def route_port(service: dict[str, Any]) -> int:
    ports = service.get("ports")
    if not isinstance(ports, list):
        raise E2EError("service does not expose detected ports")
    for port in ports:
        if isinstance(port, int) and port in HTTP_PORTS:
            return port
    raise E2EError("service does not expose an HTTP port")


def build_route_payload(
    domain: str, service: dict[str, Any], suffix: str, available: dict[str, Any]
) -> dict[str, Any]:
    """Exact Domains payload with allowlisted options only."""
    return {
        "domain": domain,
        "path": f"/e2e-{suffix}",
        "internal_path": "/health",
        "strip_path": True,
        "port": route_port(service),
        "entrypoints": https_entrypoints(available),
        "https": True,
        "middlewares": [],
    }


def route_matches(route: dict[str, Any], payload: dict[str, Any]) -> bool:
    keys = ("domain", "path", "internal_path", "strip_path", "port", "https")
    if any(route.get(key) != payload.get(key) for key in keys):
        return False
    return route.get("entrypoints") == payload.get("entrypoints")


def route_signature(route: dict[str, Any]) -> dict[str, Any]:
    return {
        key: route.get(key)
        for key in (
            "id", "domain", "path", "internal_path", "strip_path", "port",
            "entrypoints", "https", "middlewares", "site_id",
        )
    }


def https_route_url(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/")
    suffix = "" if path == "/" else path.rstrip("/")
    return f"https://{route['domain']}{suffix}"


def request_https(url: str, timeout: int = 60) -> tuple[int, bytes]:
    """Real HTTPS request with certificate verification (never unverified)."""
    request = urllib.request.Request(
        url, headers={"Accept": "*/*", "User-Agent": "spanel-e2e"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(512)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(512)
    except urllib.error.URLError as exc:
        raise E2EError(f"HTTPS route could not reach {url}: {exc.reason}") from None


def redact_text(text: str, values: Sequence[str]) -> str:
    for value in sorted((item for item in values if len(item) >= 3), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def assert_no_secrets(text: str, values: Sequence[str], label: str) -> None:
    for value in values:
        if len(value) >= 3 and value in text:
            raise E2EError(f"{label} leaked a secret or canary")


def service_snapshot(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active = detail.get("active_deployment")
    details = active.get("service_details") if isinstance(active, dict) else None
    if not isinstance(details, list):
        return {}
    return {
        str(item["name"]): {
            "created_at": item.get("created_at"),
            "restart_count": item.get("restart_count"),
        }
        for item in details
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def changed_services(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    selected: str,
) -> list[str]:
    """Services other than `selected` whose container identity or restarts moved."""
    return sorted(
        name for name, state in before.items()
        if name != selected and after.get(name) != state
    )


def managed_secret_name(detail: dict[str, Any], service_name: str) -> str:
    """Prefer the selected service, then any managed service in the deployment."""
    active = detail.get("active_deployment")
    details = active.get("service_details") if isinstance(active, dict) else None
    if not isinstance(details, list):
        raise E2EError("deployment did not expose service_details")
    preferred = [
        item for item in details
        if isinstance(item, dict) and item.get("name") == service_name
    ]
    others = [
        item for item in details
        if isinstance(item, dict) and item.get("name") != service_name
    ]
    for item in [*preferred, *others]:
        environment = item.get("environment")
        if not isinstance(environment, list):
            continue
        for variable in environment:
            if (
                isinstance(variable, dict)
                and variable.get("source") == "managed"
                and variable.get("revealable") is True
                and isinstance(variable.get("name"), str)
            ):
                return variable["name"]
    raise E2EError("deployment did not expose a revealable managed secret")


def wait_service_health(
    client: ApiClient, root: str, service_name: str, timeout: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.request("GET", root)
        active = detail.get("active_deployment")
        details = active.get("service_details") if isinstance(active, dict) else None
        for item in (details if isinstance(details, list) else []):
            if isinstance(item, dict) and item.get("name") == service_name:
                health = str(item.get("health")).lower()
                running = str(item.get("status")).lower() == "running"
                # Compose sin healthcheck reporta "unknown"/"none"; el
                # servicio está sano si Docker lo mantiene running.
                if health == "healthy" or (running and health in {"unknown", "none", ""}):
                    return detail
                break
        time.sleep(2)
    raise E2EError(f"{service_name} did not become healthy within {timeout}s")


def expect_missing(client: ApiClient, path: str, label: str) -> None:
    try:
        client.request("GET", path)
    except E2EError as exc:
        if "HTTP 404" in str(exc):
            return
        raise
    raise E2EError(f"{label} still exists after cleanup")


def wait_route_probe(
    client: ApiClient, route_id: str, timeout: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: int | None = None
    while time.monotonic() < deadline:
        probe = client.request("POST", f"{DOMAINS_ROOT}/routes/{route_id}/probe")
        if isinstance(probe, dict):
            last_status = probe.get("http_status") if isinstance(probe.get("http_status"), int) else None
            certificate = probe.get("certificate")
            verified = isinstance(certificate, dict) and certificate.get("verified") is True
            if last_status == 200 and verified:
                return probe
        time.sleep(2)
    raise E2EError(
        f"route {route_id} did not answer 200 with a verified certificate "
        f"(last status {last_status})"
    )




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


def sensitive_values(pairs: Sequence[Sequence[str]]) -> list[str]:
    """Only values whose names make them secrets or canaries."""
    markers = ("SECRET", "PASSWORD", "TOKEN", "KEY", "CANARY")
    values: list[str] = []
    for name, value in pairs:
        upper = name.upper()
        if (
            (name == "DATABASE_URL" or any(marker in upper for marker in markers))
            and isinstance(value, str)
            and len(value) >= 8
        ):
            values.append(value)
    return values


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
    application_id: str | None = None
    route_id: str | None = None
    revealed_value = ""
    sensitive: list[str] = []
    primary_error: Exception | None = None
    cleanup_error: Exception | None = None
    started = time.monotonic()
    print(f"Black-box target: {base_url}")
    try:
        print("[1/17] Real login and permission validation")
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
            "apps.applications.read",
            "apps.deployments.create",
            "apps.github.connect",
            "apps.secrets.read",
            "domains.domains.manage",
            "domains.domains.read",
            "core.audit.read",
            "core.platform.admin",
        }
        missing = required.difference(permissions)
        if missing:
            raise E2EError(f"login lacks permissions: {', '.join(sorted(missing))}")

        print("[2/17] Resolve real GitHub installation and repository")
        installation_id, repository_id = find_repository(client)
        suffix = secrets.token_hex(5)
        print("[3/17] Create temporary application through API")
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
        print("[4/17] Inspect through API and worker")
        queue(client, "POST", f"{root}/inspect", None, args.operation_timeout, "inspect")
        detail = client.request("GET", root)
        candidates = detail.get("compose_candidates", [])
        if COMPOSE_FILE not in candidates:
            raise E2EError("inspection did not persist the expected Compose candidate")

        print("[5/17] Deploy through API with server-managed DB credentials")
        deploy_environment = environment()
        deploy_environment.append(["E2E_LOG_CANARY", f"canary-{secrets.token_hex(8)}"])
        sensitive = sensitive_values(deploy_environment)
        queue(
            client,
            "POST",
            f"{root}/deployments",
            {"environment": deploy_environment},
            args.deploy_timeout,
            "deploy",
        )
        detail = client.request("GET", root)
        services = detail.get("services", [])
        if not isinstance(services, list) or not services:
            raise E2EError("deployment did not expose dynamic services")

        print("[6/17] Select an HTTP service from detected ports")
        service = select_http_service(detail)
        service_name = str(service["name"])
        if service_name not in services:
            raise E2EError("selected service does not belong to the active deployment")
        subject = service_subject(application_id, service_name)
        print(f"  selected service: {service_name} (port {route_port(service)})")

        print("[7/17] Assign a registered Domains route and validate HTTPS")
        available = client.request("GET", f"{DOMAINS_ROOT}/available")
        domain = pick_lab_domain(
            available.get("items") if isinstance(available, dict) else None,
            args.domain,
        )
        route_payload = build_route_payload(domain, service, suffix, available)
        route_payload["site_id"] = subject
        route = client.request("POST", f"{DOMAINS_ROOT}/routes", route_payload)
        if not isinstance(route, dict) or not route.get("id"):
            raise E2EError("Domains did not return the created route")
        route_id = str(route["id"])
        if not route_matches(route, route_payload) or route.get("site_id") != subject:
            raise E2EError("Domains persisted a route different from the requested payload")
        wait_route_probe(client, route_id, min(args.operation_timeout, 120))
        status, _ = request_https(https_route_url(route))
        if status != 200:
            raise E2EError(f"HTTPS route returned HTTP {status}")

        print("[8/17] Request scoped logs and verify redaction canaries")
        logs = queue(
            client, "POST", f"{root}/actions/logs", {"service": service_name},
            args.operation_timeout, "logs",
        )
        if not isinstance(logs.get("result"), dict):
            raise E2EError("logs operation did not return a result")
        output = str(logs["result"].get("output") or "")
        assert_no_secrets(output, sensitive, "logs")

        print("[9/17] Restart only the selected service")
        before = service_snapshot(detail)
        queue(
            client, "POST", f"{root}/actions/restart", {"service": service_name},
            args.operation_timeout, "restart",
        )
        queue(client, "POST", f"{root}/actions/refresh", None, args.operation_timeout, "post-restart refresh")
        detail = client.request("GET", root)
        changed = changed_services(before, service_snapshot(detail), service_name)
        if changed:
            raise E2EError(f"restart also changed other services: {', '.join(changed)}")

        print("[10/17] Stop and start only the selected service")
        queue(
            client, "POST", f"{root}/actions/stop", {"service": service_name},
            args.operation_timeout, "stop",
        )
        queue(
            client, "POST", f"{root}/actions/start", {"service": service_name},
            args.operation_timeout, "start",
        )
        queue(client, "POST", f"{root}/actions/refresh", None, args.operation_timeout, "post-start refresh")
        detail = wait_service_health(client, root, service_name, args.operation_timeout)

        print("[11/17] Reveal a managed secret with reason and audit without value")
        secret_name = managed_secret_name(detail, service_name)
        reason = f"E2E service console {suffix}"
        reveal = client.request(
            "POST",
            f"{root}/secrets/{secret_name}/reveal",
            {"reason": reason},
        )
        revealed_value = reveal.get("value") if isinstance(reveal, dict) else None
        if not isinstance(revealed_value, str) or not revealed_value:
            raise E2EError("managed secret reveal did not return a value")
        sensitive.append(revealed_value)
        audits = client.request("GET", "/api/v1/audit-logs")
        entries = [
            entry for entry in audits
            if isinstance(entry, dict)
            and entry.get("action") == "apps.secret.revealed"
            and entry.get("entity_id") == application_id
            and isinstance(entry.get("details"), dict)
            and entry["details"].get("name") == secret_name
            and entry["details"].get("reason") == reason
        ] if isinstance(audits, list) else []
        if not entries:
            raise E2EError("secret reveal was not audited with its reason")
        assert_no_secrets(json.dumps(entries[0], default=str), sensitive, "audit")
        revealed_value = ""

        print("[12/17] Redeploy and confirm the same route survives container replacement")
        route_before = client.request("GET", f"{DOMAINS_ROOT}/routes/{route_id}")
        deployment_before = (detail.get("active_deployment") or {}).get("id")
        queue(
            client,
            "POST",
            f"{root}/deployments",
            {"environment": deploy_environment},
            args.deploy_timeout,
            "redeploy",
        )
        detail = client.request("GET", root)
        deployment_after = (detail.get("active_deployment") or {}).get("id")
        if not deployment_after or deployment_after == deployment_before:
            raise E2EError("redeploy did not activate a new deployment")
        route_after = client.request("GET", f"{DOMAINS_ROOT}/routes/{route_id}")
        if route_signature(route_before) != route_signature(route_after):
            raise E2EError("route identity changed after redeploy")
        wait_route_probe(client, route_id, min(args.operation_timeout, 120))
        status, _ = request_https(https_route_url(route_after))
        if status != 200:
            raise E2EError(f"HTTPS route returned HTTP {status} after redeploy")

        print("[13/17] Delete the route through Domains")
        client.request("DELETE", f"{DOMAINS_ROOT}/routes/{route_id}")
        expect_missing(client, f"{DOMAINS_ROOT}/routes/{route_id}", "route")
        route_id = None
    except Exception as exc:  # noqa: BLE001 -- API cleanup must always run
        primary_error = exc
    finally:
        password = ""
        revealed_value = ""
        if application is not None and application_id is not None and client.token:
            if route_id:
                print("[14/17] Cleanup: delete any remaining route through Domains")
                try:
                    client.request("DELETE", f"{DOMAINS_ROOT}/routes/{route_id}")
                except Exception as exc:  # noqa: BLE001 -- app purge still owns cleanup
                    if "HTTP 404" not in str(exc):
                        cleanup_error = exc
            print("[15/17] Delete temporary application and purge E2E data through API")
            try:
                queue(
                    client,
                    "DELETE",
                    f"/api/v1/plugins/apps/applications/{application_id}",
                    {"confirm": application["slug"], "purge_data": True},
                    args.operation_timeout,
                    "delete",
                )
            except Exception as exc:  # noqa: BLE001 -- report cleanup separately
                cleanup_error = cleanup_error or exc
            print("[16/17] Verify API cleanup left no application or route")
            try:
                expect_missing(client, root, "application")
                applications = client.request("GET", "/api/v1/plugins/apps/applications")
                if isinstance(applications, list) and any(
                    isinstance(item, dict) and item.get("id") == application_id
                    for item in applications
                ):
                    raise E2EError("deleted application is still listed")
                if route_id:
                    expect_missing(client, f"{DOMAINS_ROOT}/routes/{route_id}", "route")
            except Exception as exc:  # noqa: BLE001 -- report cleanup separately
                cleanup_error = cleanup_error or exc
        print("[17/17] Cleanup checks finished")
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
        default=os.environ.get("SPANEL_E2E_BASE_URL", "https://spanel.systutor.com"),
    )
    parser.add_argument("--operation-timeout", type=int, default=300)
    parser.add_argument("--deploy-timeout", type=int, default=2400)
    parser.add_argument(
        "--domain",
        default=os.environ.get("SPANEL_E2E_DOMAIN", "").strip() or None,
        help="registered lab domain from Domains; required when the list is ambiguous",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (E2EError, OSError) as exc:
        print(f"E2E FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
