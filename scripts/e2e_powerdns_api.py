#!/usr/bin/env python3
"""Black-box DNS E2E using a real PanelTutor login and real PowerDNS."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit


class E2EError(RuntimeError):
    """Safe E2E failure that never includes credentials."""


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
        expected: tuple[int, ...] = (200,),
        timeout: int = 15,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                content = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            content = exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise E2EError(f"{method} {path} could not reach the API") from exc
        if status not in expected:
            raise E2EError(f"{method} {path} returned HTTP {status}, expected {expected}")
        print(f"PASS {method} {path} -> {status}")
        if not content:
            return status, None
        try:
            return status, json.loads(content)
        except json.JSONDecodeError:
            return status, content.decode(errors="replace")


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise E2EError("base URL must be an absolute HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise E2EError("base URL must not contain credentials, query, or fragment")
    return value.strip().rstrip("/")


def read_password(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise E2EError("password file must be a regular non-symlink file")
    if resolved.stat().st_mode & 0o077:
        raise E2EError("password file must be owner-only")
    password = resolved.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise E2EError("password file is empty")
    return password


def rrset(name: str, type_: str, value: str) -> dict[str, object]:
    return {"name": name, "type": type_, "ttl": 300, "values": [value]}


def assert_rrset(zone: dict[str, Any], name: str, type_: str, value: str) -> None:
    for item in zone.get("rrsets", []):
        if (
            item.get("name") == name
            and item.get("type") == type_
            and value in item.get("records", [])
        ):
            return
    raise E2EError(f"API zone does not contain expected {name} {type_} RRset")


def dig(resolver: str, port: int, name: str, type_: str, value: str) -> None:
    for transport in ("UDP", "TCP"):
        command = ["dig", f"@{resolver}", "-p", str(port), "+norecurse", "+comments", "+answer"]
        if transport == "TCP":
            command.append("+tcp")
        command.extend([name, type_])
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=15, check=False
        )
        if result.returncode or " aa" not in result.stdout or value not in result.stdout:
            raise E2EError(f"{transport} {type_} did not return authoritative expected data")
        print(f"PASS dig {transport} {name} {type_}")


def docker(action: str, container: str) -> None:
    if not container or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in container):
        raise E2EError("invalid PowerDNS container name")
    result = subprocess.run(
        ["docker", action, container],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise E2EError(f"docker {action} failed for the PowerDNS container")


def login(client: ApiClient, email: str, password: str) -> dict[str, Any]:
    _, payload = client.request(
        "POST", "/api/v1/auth/login", {"email": email, "password": password}
    )
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise E2EError("real login did not return an access token")
    client.token = token
    _, profile = client.request("GET", "/api/v1/auth/me")
    return profile


def run(args: argparse.Namespace) -> int:
    base_url = validate_base_url(args.base_url)
    email = os.environ.get("SPANEL_E2E_EMAIL", "").strip()
    password_file = os.environ.get("SPANEL_E2E_PASSWORD_FILE", "").strip()
    if not email or not password_file:
        raise E2EError("SPANEL_E2E_EMAIL and SPANEL_E2E_PASSWORD_FILE are required")
    password = read_password(Path(password_file))
    suffix = f"{int(time.time())}-{secrets.token_hex(3)}"
    zone = f"dns-api-{suffix}.test"
    zone_path = f"/api/v1/plugins/domains/dns/zones/{quote(zone)}"
    client = ApiClient(base_url)
    created_domain = False
    created_zone = False
    pdns_stopped = False
    primary_error: Exception | None = None

    try:
        print("[1/9] Real login and RBAC contract")
        profile = login(client, email, password)
        password = ""
        required = {
            "domains.domains.manage",
            "domains.dns.read",
            "domains.dns.records",
            "domains.dns.zones",
            "domains.dns.nameservers",
            "core.platform.admin",
        }
        missing = required.difference(profile.get("permissions", []))
        if missing:
            raise E2EError(f"login lacks required permissions: {', '.join(sorted(missing))}")
        anonymous = ApiClient(base_url)
        anonymous.request("GET", "/api/v1/plugins/domains/dns/zones", expected=(401,))

        print("[2/9] Authenticated PowerDNS health")
        _, doctor = client.request("GET", "/api/v1/plugins/domains/dns/doctor")
        if not doctor.get("ok") or not doctor.get("authenticated"):
            raise E2EError("PowerDNS doctor is not healthy and authenticated")

        print("[3/9] Idempotent nameserver configuration")
        settings = {
            "ns1": "ns1.dns-lab.test",
            "ns2": "ns2.dns-lab.test",
            "ns1_ipv4": "192.0.2.53",
            "ns1_ipv6": None,
            "ns2_ipv4": "192.0.2.53",
            "ns2_ipv6": None,
            "soa_contact": "hostmaster.dns-lab.test",
            "default_ttl": 300,
        }
        _, current_settings = client.request("GET", "/api/v1/plugins/domains/dns/nameservers")
        _, plan = client.request(
            "POST", "/api/v1/plugins/domains/dns/nameservers/plan", {"settings": settings, "zones": []}
        )
        if not current_settings.get("configured") or plan.get("settings_changed"):
            client.request(
                "POST",
                "/api/v1/plugins/domains/dns/nameservers/apply",
                {
                    "settings": settings,
                    "zones": [],
                    "zone_versions": {},
                    "expected_version": current_settings.get("version", 0),
                },
            )

        print("[4/9] Domain reservation and zone creation through API")
        client.request(
            "POST", "/api/v1/plugins/domains", {"site_id": None, "domain": zone, "www": False}, expected=(201,)
        )
        created_domain = True
        client.request("POST", "/api/v1/plugins/domains/dns/zones", {"zone_name": zone}, expected=(201,))
        created_zone = True

        print("[5/9] RRset plan/apply and API-authoritative consistency")
        desired_a = rrset("www", "A", "192.0.2.80")
        _, plan = client.request("POST", f"{zone_path}/rrsets/plan", {"rrsets": [desired_a]})
        _, current = client.request(
            "POST", f"{zone_path}/rrsets/apply", {"rrsets": [desired_a], "expected_version": plan["version"]}
        )
        assert_rrset(current, f"www.{zone}", "A", "192.0.2.80")
        dig(args.resolver, args.dns_port, f"www.{zone}", "A", "192.0.2.80")
        client.request(
            "POST",
            "/api/v1/plugins/domains/dns/nameservers/verify",
            {"zone": zone, "resolver": args.internal_resolver, "port": 53, "timeout": 5},
        )

        print("[6/9] Optimistic conflict rejects stale writes")
        stale_version = current["version"]
        txt = rrset("@", "TXT", f"e2e-{suffix}")
        _, fresh_plan = client.request("POST", f"{zone_path}/rrsets/plan", {"rrsets": [txt]})
        client.request(
            "POST", f"{zone_path}/rrsets/apply", {"rrsets": [txt], "expected_version": fresh_plan["version"]}
        )
        stale_a = rrset("www", "A", "192.0.2.81")
        client.request(
            "POST",
            f"{zone_path}/rrsets/apply",
            {"rrsets": [stale_a], "expected_version": stale_version},
            expected=(409,),
        )
        _, current = client.request("GET", f"{zone_path}/rrsets")
        assert_rrset(current, f"www.{zone}", "A", "192.0.2.80")

        print("[7/9] Fresh edit and real UDP/TCP answer")
        _, fresh_plan = client.request("POST", f"{zone_path}/rrsets/plan", {"rrsets": [stale_a]})
        client.request(
            "POST", f"{zone_path}/rrsets/apply", {"rrsets": [stale_a], "expected_version": fresh_plan["version"]}
        )
        dig(args.resolver, args.dns_port, f"www.{zone}", "A", "192.0.2.81")

        print("[8/9] PowerDNS outage and explicit recovery")
        docker("stop", args.pdns_container)
        pdns_stopped = True
        client.request("GET", "/api/v1/plugins/domains/dns/doctor", expected=(503, 504))
        docker("start", args.pdns_container)
        pdns_stopped = False
        for attempt in range(30):
            try:
                client.request("GET", "/api/v1/plugins/domains/dns/doctor", timeout=3)
                break
            except E2EError:
                if attempt == 29:
                    raise
                time.sleep(1)
        client.request("GET", f"{zone_path}/rrsets")

        print("[9/9] RRset and ownership cleanup")
        _, current = client.request("GET", f"{zone_path}/rrsets")
        client.request(
            "POST",
            f"{zone_path}/rrsets/delete",
            {"name": "www", "type": "A", "expected_version": current["version"]},
        )
        _, current = client.request("GET", f"{zone_path}/rrsets")
        client.request("DELETE", f"{zone_path}?expected_version={quote(current['version'])}")
        created_zone = False
        client.request("DELETE", f"/api/v1/plugins/domains/{quote(zone)}", expected=(204,))
        created_domain = False
        print("PASS real-login PowerDNS API E2E")
        return 0
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        password = ""
        if pdns_stopped:
            try:
                docker("start", args.pdns_container)
            except Exception:
                if primary_error is None:
                    raise
        if client.token and created_zone:
            try:
                _, current = client.request("GET", f"{zone_path}/rrsets")
                client.request("DELETE", f"{zone_path}?expected_version={quote(current['version'])}")
            except Exception:
                if primary_error is None:
                    raise
        if client.token and created_domain:
            try:
                client.request("DELETE", f"/api/v1/plugins/domains/{quote(zone)}", expected=(204,))
            except Exception:
                if primary_error is None:
                    raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:3000")
    result.add_argument("--resolver", default="127.0.0.1")
    result.add_argument("--dns-port", type=int, default=5300)
    result.add_argument("--internal-resolver", default="spanel-powerdns-lab-auth")
    result.add_argument("--pdns-container", default="spanel-powerdns-lab-auth")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except E2EError as exc:
        print(f"FAIL: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from None
