#!/usr/bin/env python3
"""API-first CLI for SPanel Apps.

This script never imports the plugin runtime and never touches PostgreSQL,
Redis, Docker or SSH. It only speaks HTTP to the authenticated Apps API. The
access token is read from the environment or stdin, never from argv.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

TERMINAL = {"succeeded", "failed", "interrupted"}
ROOT = "/api/v1/plugins/apps"
DOMAINS_ROOT = "/api/v1/plugins/domains"
MANAGED_ENVIRONMENT_NAMES = {"APP_DB_NAME", "APP_DB_USER", "APP_DB_PASSWORD"}


class CliError(RuntimeError):
    """A safe-to-print CLI failure."""


def transport_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: object | None = None,
    idempotency_key: str | None = None,
    timeout: int = 30,
) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        # Never echo response bodies: they may contain operator-provided values.
        raise CliError(f"Apps API returned HTTP {exc.code} for {method}") from None
    except urllib.error.URLError as exc:
        raise CliError(f"Apps API is unavailable: {exc.reason}") from None
    except (ValueError, OSError) as exc:
        raise CliError("Apps API returned an invalid response") from exc


class Client:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: Callable[..., Any] = transport_json,
        timeout: int = 30,
        poll_interval: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _request_at(
        self,
        root: str,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.transport(
            method, f"{self.base_url}{root}{path}", token=self.token,
            payload=payload, idempotency_key=idempotency_key, timeout=self.timeout,
        )

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self._request_at(ROOT, method, path, payload, idempotency_key=idempotency_key)

    def domains_request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self._request_at(
            DOMAINS_ROOT, method, path, payload, idempotency_key=idempotency_key
        )

    def poll(self, operation_id: str, timeout: int = 900) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            operation = self.request("GET", f"/operations/{operation_id}")
            if operation.get("status") in TERMINAL:
                return operation
            if time.monotonic() >= deadline:
                raise CliError(f"Operation {operation_id} did not finish before the timeout")
            time.sleep(self.poll_interval)


def read_token() -> str:
    token = os.environ.get("SPANEL_APPS_TOKEN") or os.environ.get("SPANEL_ACCESS_TOKEN")
    if token:
        return token.strip()
    if sys.stdin.isatty():
        value = getpass.getpass("SPANEL access token: ")
    else:
        value = sys.stdin.readline()
    if not value.strip():
        raise CliError("SPANEL access token is required via stdin or environment")
    return value.strip()


def parse_env_text(text: str) -> list[list[str]]:
    entries: list[list[str]] = []
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise CliError("environment contains an invalid line")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "A").isalnum() or name[0].isdigit():
            raise CliError("environment contains an invalid variable name")
        if name in names:
            raise CliError("environment contains a duplicate variable")
        if name in MANAGED_ENVIRONMENT_NAMES:
            raise CliError(f"Apps manages environment variable {name}")
        if len(value) > 8192 or any(character in value for character in "\x00\r\n"):
            raise CliError("environment contains an invalid value")
        names.add(name)
        entries.append([name, value])
    if len(entries) > 100:
        raise CliError("environment contains more than 100 variables")
    return entries


def read_env_file(path: Path, *, checkout: Path | None = None) -> list[list[str]]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CliError("environment file does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CliError("environment file must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise CliError("environment file must have mode 0600")
    if checkout is not None:
        try:
            path.resolve().relative_to(checkout.resolve())
        except ValueError:
            pass
        else:
            raise CliError("environment file must be outside the checkout")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CliError("environment file is unreadable") from exc
    return parse_env_text(text)


def deployment_environment(path: Path | None, inline: str | None) -> list[list[str]]:
    entries = read_env_file(path) if path is not None else []
    entries.extend(parse_env_text(inline or ""))
    names = [name for name, _value in entries]
    if len(names) != len(set(names)):
        raise CliError("environment contains a duplicate variable")
    if len(entries) > 100:
        raise CliError("environment contains more than 100 variables")
    return entries


def _idempotency_key() -> str:
    return str(uuid4())


def _print(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True, indent=2))
        return
    if isinstance(value, list):
        for item in value:
            print(_human(item))
        return
    if isinstance(value, dict) and {"available", "routes"} <= set(value):
        for item in value["available"]:
            domain = item.get("domain", "") if isinstance(item, dict) else item
            published = item.get("published") if isinstance(item, dict) else None
            print(f"available={domain} published={published}")
        for item in value["routes"]:
            print(_human(item))
        return
    print(_human(value))


def _human(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    if {"name", "status", "ports"} <= set(value):
        ports = value.get("ports") or []
        return (
            f"name={value['name']} status={value['status']} "
            f"health={value.get('health', 'unknown')} "
            f"ports={','.join(str(port) for port in ports)}"
        )
    if "route_id" in value:
        return (
            f"route_id={value['route_id']} domain={value.get('domain', '')} "
            f"path={value.get('path', '')} "
            f"http_status={value.get('http_status')}"
        )
    columns = []
    for key in (
        "id", "route_id", "operation_id", "kind", "status", "phase", "name", "slug",
        "repository", "branch", "account_login", "full_name", "app_slug",
        "domain", "path", "internal_path", "site_id", "https", "public_error",
    ):
        if key in value:
            columns.append(f"{key}={value[key]}")
    return " ".join(columns) or json.dumps(value, sort_keys=True)


def _application_detail(client: Client, application: str) -> dict:
    value = client.request("GET", f"/applications/{application}")
    if not isinstance(value, dict):
        raise CliError("Apps API returned an invalid application")
    return value


def _active_service(client: Client, application: str, service: str) -> dict:
    detail = _application_detail(client, application)
    active = detail.get("active_deployment")
    details = active.get("service_details") if isinstance(active, dict) else None
    if not isinstance(details, list):
        raise CliError("Application has no active deployment")
    match = next(
        (item for item in details if isinstance(item, dict) and item.get("name") == service),
        None,
    )
    if match is None:
        raise CliError("Service does not belong to the active deployment")
    return match


def _site_id(application: str, service: str) -> str:
    return f"{application}:{service}"


def _route_port(service_detail: dict, requested: int | None) -> int:
    ports = service_detail.get("ports") or []
    if requested is not None:
        if ports and requested not in ports:
            raise CliError("Port is not exposed by the selected service")
        return requested
    if len(ports) == 1 and isinstance(ports[0], int):
        return ports[0]
    raise CliError("--port is required unless the service exposes exactly one port")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--timeout", type=int, default=900, help="bounded wait seconds")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers")
    repositories = sub.add_parser("repositories")
    repositories.add_argument("--installation", required=True)

    add = sub.add_parser("add")
    add.add_argument("--installation", required=True)
    add.add_argument("--repository", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--branch")
    add.add_argument("--compose-file")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("application")
    inspect.add_argument("--wait", action="store_true")

    deploy = sub.add_parser("deploy")
    deploy.add_argument("application")
    deploy.add_argument("--env-file", type=Path)
    deploy.add_argument(
        "--env",
        help="bloque dotenv plano multilínea (visible en argv; preferí --env-file para secretos)",
    )
    deploy.add_argument("--wait", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("operation")

    sub.add_parser("list")

    services = sub.add_parser("services")
    services.add_argument("application")

    for name in ("refresh", "logs", "start", "stop", "restart"):
        action = sub.add_parser(name)
        action.add_argument("application")
        action.add_argument("--service")
        action.add_argument("--wait", action="store_true")

    down = sub.add_parser("down")
    down.add_argument("application")
    down.add_argument("--wait", action="store_true")

    domain_list = sub.add_parser("domain-list")
    domain_list.add_argument("application")
    domain_list.add_argument("service")

    domain_assign = sub.add_parser("domain-assign")
    domain_assign.add_argument("application")
    domain_assign.add_argument("service")
    domain_assign.add_argument("--domain", required=True)
    domain_assign.add_argument("--path", default="/")
    domain_assign.add_argument("--internal-path", default="/")
    domain_assign.add_argument("--strip-path", action="store_true")
    domain_assign.add_argument("--port", type=int)
    domain_assign.add_argument("--entrypoint", action="append", default=[])
    domain_assign.add_argument(
        "--https", action=argparse.BooleanOptionalAction, default=True
    )
    domain_assign.add_argument("--middleware", action="append", default=[])

    domain_remove = sub.add_parser("domain-remove")
    domain_remove.add_argument("application")
    domain_remove.add_argument("service")
    domain_remove.add_argument("route_id")

    domain_probe = sub.add_parser("domain-probe")
    domain_probe.add_argument("application")
    domain_probe.add_argument("service")
    domain_probe.add_argument("route_id")

    delete = sub.add_parser("delete")
    delete.add_argument("application")
    delete.add_argument("--purge-data", action="store_true")
    delete.add_argument("--wait", action="store_true")
    return parser


def run(args: argparse.Namespace, client: Client) -> Any:
    if args.command == "providers":
        return client.request("GET", "/github/providers")
    if args.command == "repositories":
        return client.request(
            "GET", f"/github/installations/{args.installation}/repositories"
        )
    if args.command == "add":
        body: dict[str, Any] = {
            "name": args.name,
            "installation_id": args.installation,
            "repository_id": args.repository,
        }
        for key in ("branch", "compose_file"):
            value = getattr(args, key)
            if value is not None:
                body[key] = value
        return client.request(
            "POST", "/applications", body, idempotency_key=_idempotency_key()
        )
    if args.command in {"inspect", "deploy"}:
        if args.command == "inspect":
            body = {}
        else:
            body = {"environment": deployment_environment(args.env_file, args.env)}
        operation = client.request(
            "POST", f"/applications/{args.application}/{args.command if args.command == 'inspect' else 'deployments'}",
            body, idempotency_key=_idempotency_key(),
        )
        if args.wait:
            return client.poll(operation["operation_id"], timeout=args.timeout)
        return operation
    if args.command == "status":
        return client.request("GET", f"/operations/{args.operation}")
    if args.command == "list":
        return client.request("GET", "/applications")
    if args.command == "services":
        detail = _application_detail(client, args.application)
        active = detail.get("active_deployment")
        details = active.get("service_details") if isinstance(active, dict) else None
        if not isinstance(details, list):
            raise CliError("Application has no active deployment")
        return details
    if args.command in {"refresh", "logs", "start", "stop", "restart", "down"}:
        body = {}
        service = getattr(args, "service", None)
        if service:
            body["service"] = service
        operation = client.request(
            "POST", f"/applications/{args.application}/actions/{args.command}",
            body, idempotency_key=_idempotency_key(),
        )
        if args.wait:
            return client.poll(operation["operation_id"], timeout=args.timeout)
        return operation
    if args.command == "domain-list":
        _active_service(client, args.application, args.service)
        site_id = _site_id(args.application, args.service)
        available = client.domains_request("GET", "/available")
        routes = client.domains_request(
            "GET", f"/routes?site_id={urllib.parse.quote(site_id, safe='')}"
        )
        return {
            "available": available.get("items", []) if isinstance(available, dict) else [],
            "routes": routes.get("items", []) if isinstance(routes, dict) else [],
        }
    if args.command == "domain-assign":
        service_detail = _active_service(client, args.application, args.service)
        available = client.domains_request("GET", "/available")
        items = available.get("items", []) if isinstance(available, dict) else []
        domain = args.domain.strip().lower()
        if domain not in {
            str(item.get("domain", "")).lower()
            for item in items
            if isinstance(item, dict)
        }:
            raise CliError("Domain is not registered for this tenant")
        payload = {
            "domain": domain,
            "path": args.path,
            "internal_path": args.internal_path,
            "strip_path": bool(args.strip_path),
            "port": _route_port(service_detail, args.port),
            "entrypoints": list(args.entrypoint),
            "https": bool(args.https),
            "middlewares": list(args.middleware),
            "site_id": _site_id(args.application, args.service),
        }
        return client.domains_request("POST", "/routes", payload)
    if args.command in {"domain-remove", "domain-probe"}:
        _active_service(client, args.application, args.service)
        route = client.domains_request("GET", f"/routes/{args.route_id}")
        if not isinstance(route, dict) or route.get("site_id") != _site_id(
            args.application, args.service
        ):
            raise CliError("Route does not belong to this application service")
        if args.command == "domain-remove":
            return client.domains_request("DELETE", f"/routes/{args.route_id}")
        return client.domains_request("POST", f"/routes/{args.route_id}/probe", {})
    if args.command == "delete":
        application = client.request("GET", f"/applications/{args.application}")
        body = {
            "confirm": application.get("slug", ""),
            "purge_data": bool(args.purge_data),
        }
        operation = client.request(
            "DELETE", f"/applications/{args.application}", body,
            idempotency_key=_idempotency_key(),
        )
        if args.wait:
            return client.poll(operation["operation_id"], timeout=args.timeout)
        return operation
    raise CliError(f"unknown command: {args.command}")


def main(argv: Sequence[str] | None = None, client: Client | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if client is None:
            base = os.environ.get("SPANEL_APPS_API_URL") or os.environ.get(
                "SPANEL_API_URL"
            )
            if not base:
                raise CliError("SPANEL_APPS_API_URL is required")
            client = Client(base, read_token())
        _print(run(args, client), args.json)
        return 0
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        print(f"ERROR: Apps API returned HTTP {exc.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
