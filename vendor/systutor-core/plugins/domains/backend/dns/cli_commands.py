"""Command implementations for the Domains DNS operator CLI."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from plugins.domains.backend.cloudflare.client import (
    CloudflareClient,
    CloudflareNotFound,
    CloudflareValidation,
)
from plugins.domains.backend.cloudflare.config import (
    CloudflareConfig,
    CloudflareConfigError,
)
from plugins.domains.backend.cloudflare.provisioning import CloudflareProvisioningService
from plugins.domains.backend.cloudflare.service import CloudflareService
from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.errors import (
    DnsConfigError,
    DnsConflictError,
    DnsNotFoundError,
    DnsValidationError,
    PowerDnsNotFound,
)
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.nameservers import (
    PublicNameserverSettings,
    build_settings,
    default_serial,
    seed_rrsets,
)
from plugins.domains.backend.dns.validation import (
    normalize_owner,
    normalize_rrset,
    normalize_zone_name,
    plan_changes,
    validate_zone_changes,
    zone_version,
)
from plugins.domains.backend.dns.zonefile import export_zone, parse_zonefile


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))


def _emit(payload: dict[str, Any], *, json_output: bool, lines: Sequence[str]) -> None:
    if json_output:
        _print_json(payload)
    else:
        for line in lines:
            print(line)


def _serialize_zone(zone: DnsZone) -> dict[str, Any]:
    return {
        "name": zone.name,
        "kind": zone.kind,
        "version": zone_version(zone),
        "serial": zone.serial,
        "rrsets": [rrset.to_dict() for rrset in zone.rrsets],
    }


def _settings_from_args(
    args: argparse.Namespace,
    current: PublicNameserverSettings | None = None,
) -> PublicNameserverSettings:
    ns1 = args.ns1 or (current.ns1_hostname if current else None)
    ns2 = args.ns2 or (current.ns2_hostname if current else None)
    if not ns1 or not ns2:
        raise DnsValidationError("--ns1 and --ns2 are required")
    fallback_zone = getattr(args, "zone", "") or "example.com"
    if not isinstance(fallback_zone, str):
        fallback_zone = "example.com"
    return build_settings(
        ns1_hostname=ns1,
        ns2_hostname=ns2,
        ns1_ipv4=args.ns1_ipv4 or (current.ns1_ipv4 if current else None),
        ns1_ipv6=args.ns1_ipv6 or (current.ns1_ipv6 if current else None),
        ns2_ipv4=args.ns2_ipv4 or (current.ns2_ipv4 if current else None),
        ns2_ipv6=args.ns2_ipv6 or (current.ns2_ipv6 if current else None),
        soa_contact=args.soa_contact
        or (current.soa_contact if current else f"hostmaster.{fallback_zone}"),
        default_ttl=args.default_ttl or (current.default_ttl if current else 300),
    )


def _zone_seed(
    zone: str, settings: PublicNameserverSettings | None
) -> tuple[PublicNameserverSettings, list[DnsRRset]]:
    if settings is None:
        settings = build_settings(
            ns1_hostname=f"ns1.{zone}",
            ns2_hostname=f"ns2.{zone}",
            soa_contact=f"hostmaster.{zone}",
            default_ttl=300,
        )
    return settings, seed_rrsets(zone, settings, serial=default_serial())


def _confirm(args: argparse.Namespace, expected: str, flag: str) -> None:
    supplied = getattr(args, flag, None)
    if supplied == expected:
        return
    if sys.stdin.isatty():
        answer = input(f"Type {expected} to confirm: ")
        if answer == expected:
            return
    raise DnsValidationError(f"{flag.replace('_', '-')} must exactly match {expected}")


def _current_or_none(client: PowerDnsClient, zone: str) -> DnsZone | None:
    try:
        return client.get_zone(zone)
    except PowerDnsNotFound:
        return None


def _session_factory(args: argparse.Namespace, factory: Any = None):
    if factory is not None:
        return factory
    url = getattr(args, "db_url", "") or os.getenv("SYSTUTOR_DATABASE_URL", "")
    if not url:
        raise DnsConfigError("SYSTUTOR_DATABASE_URL is required for nameserver commands")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return sessionmaker(bind=create_engine(url, connect_args=connect_args), expire_on_commit=False)


def _approved_zones(args: argparse.Namespace, *, for_apply: bool) -> list[str] | None:
    zones = getattr(args, "zone", None)
    no_zones = bool(getattr(args, "no_zones", False))
    if no_zones:
        return []
    if zones:
        return list(zones)
    if for_apply:
        raise DnsValidationError(
            "--zone or --no-zones is required; omitted zones never mean all zones"
        )
    return None


def _expected_zone_versions(values: Sequence[str] | None) -> dict[str, str]:
    expected: dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise DnsValidationError("--zone-version must use ZONE=VERSION")
        zone, version = raw.split("=", 1)
        zone_name = normalize_zone_name(zone)
        value = version.strip()
        if not value:
            raise DnsValidationError("--zone-version requires a non-empty version")
        previous = expected.get(zone_name)
        if previous is not None and previous != value:
            raise DnsValidationError("conflicting --zone-version values for one zone")
        expected[zone_name] = value
    return expected


def _doctor(args: argparse.Namespace, client: PowerDnsClient) -> int:
    config = client.config
    checks = config.inspect_key_file()
    if not (checks["exists"] and checks["regular"] and checks["private"] and checks["readable"]):
        raise DnsConfigError("the PowerDNS API key file is missing, unreadable or not owner-only")
    client.health()
    payload = {
        "ok": True,
        "authenticated": True,
        "server_id": config.server_id,
        "timeout_seconds": config.timeout_seconds,
        "checks": checks,
    }
    _emit(
        payload,
        json_output=args.json,
        lines=[
            "PowerDNS doctor",
            f"server: {config.server_id}",
            f"timeout: {config.timeout_seconds}s",
            "api key file: owner-only",
            "authenticated health: PASS",
        ],
    )
    return 0


def _nameserver_show(args: argparse.Namespace, factory: Any) -> int:
    from plugins.domains.backend.dns.store import get_public_settings

    sessions = _session_factory(args, factory)
    with sessions() as db:
        settings = get_public_settings(db)
    if settings is None:
        payload = {"configured": False, "physical_redundancy": False}
        _emit(payload, json_output=args.json, lines=["nameservers: not configured"])
        return 0
    payload = {"configured": True, **settings.to_dict()}
    lines = [
        f"ns1: {settings.ns1_hostname}",
        f"ns2: {settings.ns2_hostname}",
        f"SOA contact: {settings.soa_contact}",
        f"default TTL: {settings.default_ttl}",
        f"version: {settings.version}",
        settings.warning,
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _nameserver_plan(args: argparse.Namespace, client: PowerDnsClient, factory: Any) -> int:
    from plugins.domains.backend.dns.service import DnsService
    from plugins.domains.backend.dns.store import get_public_settings

    sessions = _session_factory(args, factory)
    with sessions() as db:
        current = get_public_settings(db)
        desired = _settings_from_args(args, current)
        service = DnsService(db, None, client)
        payload = service.plan_nameservers(desired, zones=_approved_zones(args, for_apply=False))
    _emit(
        payload,
        json_output=args.json,
        lines=[
            "nameserver plan",
            f"settings drift: {payload['settings_fields']}",
            f"zone changes: {len(cast(list[object], payload['zones']))}",
            str(payload["warning"]),
        ],
    )
    return 3 if payload["drift"] else 0


def _nameserver_apply(args: argparse.Namespace, client: PowerDnsClient, factory: Any) -> int:
    from plugins.domains.backend.dns.service import DnsService
    from plugins.domains.backend.dns.store import get_public_settings

    sessions = _session_factory(args, factory)
    with sessions() as db:
        current = get_public_settings(db)
        desired = _settings_from_args(args, current)
        zones = _approved_zones(args, for_apply=True)
        expected_versions = _expected_zone_versions(getattr(args, "zone_version", None))
        if not args.apply:
            payload = {
                "applied": False,
                "settings": desired.to_dict(),
                "zones": sorted(zones or []),
                "zone_versions": expected_versions,
                "physical_redundancy": False,
                "warning": desired.warning,
            }
            _emit(
                payload,
                json_output=args.json,
                lines=["nameserver apply plan", str(desired.warning)],
            )
            return 3
        service = DnsService(db, None, client)
        payload = service.apply_nameservers(
            desired,
            zones=zones,
            expected_versions=expected_versions,
            expected_version=args.expected_version,
        )
    _emit(
        payload,
        json_output=args.json,
        lines=[
            "nameserver settings applied",
            f"changed zones: {payload['changed_zones']}",
            str(payload["warning"]),
        ],
    )
    return 0


def _zones_list(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zones = client.list_zones()
    items = sorted(
        (
            {"name": str(item.get("name", "")).rstrip("."), "kind": item.get("kind")}
            for item in zones
        ),
        key=lambda item: item["name"],
    )
    _emit({"zones": items}, json_output=args.json, lines=[item["name"] for item in items])
    return 0


def _zones_create(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    settings = (
        _settings_from_args(args, None)
        if args.ns1 or args.ns2
        else build_settings(
            ns1_hostname=f"ns1.{zone}",
            ns2_hostname=f"ns2.{zone}",
            soa_contact=f"hostmaster.{zone}",
            default_ttl=args.default_ttl or 300,
        )
    )
    seed = seed_rrsets(zone, settings, serial=default_serial())
    current = _current_or_none(client, zone)
    if current is None:
        payload = {
            "zone": zone,
            "applied": False,
            "drift": True,
            "action": "create",
            "rrsets": [rrset.to_dict() for rrset in seed],
            "warning": settings.warning,
        }
        _emit(
            payload,
            json_output=args.json,
            lines=[f"would create {zone}", f"records: {len(seed)}", settings.warning],
        )
        if not args.apply:
            return 3
        client.create_zone(zone, nameservers=[settings.ns1_hostname, settings.ns2_hostname])
        try:
            client.patch_rrsets(zone, seed)
        except Exception:
            try:
                client.delete_zone(zone)
            except Exception:  # noqa: BLE001 - compensation is best effort
                pass
            raise
        current = client.get_zone(zone)
    else:
        changes = plan_changes(current, {(r.name, r.type): r for r in seed})
        if not changes:
            _emit(
                {"zone": zone, "applied": False, "drift": False, **_serialize_zone(current)},
                json_output=args.json,
                lines=[f"{zone}: no drift"],
            )
            return 0
        if not args.apply:
            _emit(
                {"zone": zone, "applied": False, "drift": True, "changes": changes},
                json_output=args.json,
                lines=[f"{zone}: {len(changes)} RRset(s) would change"],
            )
            return 3
        client.patch_rrsets(zone, seed)
        current = client.get_zone(zone)
    result = {"zone": zone, "applied": True, "drift": False, **_serialize_zone(current)}
    _emit(
        result,
        json_output=args.json,
        lines=[f"{zone}: applied", f"version: {result['version']}"],
    )
    return 0


def _zones_show(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    payload = _serialize_zone(client.get_zone(zone))
    lines = [
        f"zone: {zone}",
        f"version: {payload['version']}",
        f"serial: {payload['serial']}",
        *(
            f"{rrset['name']} {rrset['type']} {rrset['ttl']} {rrset['records']}"
            for rrset in payload["rrsets"]
        ),
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _zones_delete(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    current = _current_or_none(client, zone)
    if current is None:
        _emit(
            {"zone": zone, "deleted": False, "drift": False},
            json_output=args.json,
            lines=[f"{zone}: absent"],
        )
        return 0
    version = zone_version(current)
    if args.expected_version and args.expected_version != version:
        raise DnsConflictError("the zone version changed; refresh and try again")
    if not args.apply:
        _emit(
            {"zone": zone, "deleted": False, "drift": True, "version": version},
            json_output=args.json,
            lines=[f"would delete {zone}", f"version: {version}"],
        )
        return 3
    _confirm(args, zone, "confirm_zone")
    client.delete_zone(zone)
    _emit({"zone": zone, "deleted": True}, json_output=args.json, lines=[f"{zone}: deleted"])
    return 0


def _rrset(args: argparse.Namespace, zone: str) -> DnsRRset:
    return normalize_rrset(
        zone=zone,
        name=args.name,
        type=args.type,
        ttl=args.ttl,
        values=args.value,
    )


def _rrsets_list(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    payload = _serialize_zone(client.get_zone(zone))
    lines = [
        (
            f"{rrset['name']} {rrset['type']} {rrset['ttl']} {rrset['records']}"
        )
        for rrset in payload["rrsets"]
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _rrsets_plan(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    rrset = _rrset(args, zone)
    current = client.get_zone(zone)
    validate_zone_changes(current, [rrset])
    changes = plan_changes(current, {(rrset.name, rrset.type): rrset})
    payload = {
        "zone": zone,
        "version": zone_version(current),
        "drift": bool(changes),
        "changes": changes,
        "rrset": rrset.to_dict(),
    }
    _emit(
        payload,
        json_output=args.json,
        lines=[
            f"zone: {zone}",
            f"version: {payload['version']}",
            f"changes: {len(changes)}",
        ],
    )
    return 3 if changes else 0


def _rrsets_apply(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    rrset = _rrset(args, zone)
    current = client.get_zone(zone)
    if zone_version(current) != args.expected_version:
        raise DnsConflictError("the zone version changed; refresh and try again")
    validate_zone_changes(current, [rrset])
    if not args.apply:
        changes = plan_changes(current, {(rrset.name, rrset.type): rrset})
        _emit(
            {"zone": zone, "applied": False, "drift": bool(changes), "changes": changes},
            json_output=args.json,
            lines=["dry-run; pass --apply to write", f"changes: {len(changes)}"],
        )
        return 3 if changes else 0
    client.patch_rrsets(zone, [rrset])
    payload = {
        "zone": zone,
        "applied": True,
        "drift": False,
        **_serialize_zone(client.get_zone(zone)),
    }
    _emit(
        payload,
        json_output=args.json,
        lines=[f"{zone}: applied {rrset.type} {rrset.name}"],
    )
    return 0


def _rrsets_delete(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    type_ = args.type.strip().upper()
    owner = normalize_owner(args.name, zone)
    if type_ == "SOA":
        raise DnsValidationError("the apex SOA cannot be deleted here")
    if type_ == "NS" and owner == zone:
        raise DnsValidationError("apex NS records cannot be deleted here")
    current = client.get_zone(zone)
    if zone_version(current) != args.expected_version:
        raise DnsConflictError("the zone version changed; refresh and try again")
    existing = current.rrset(owner, type_)
    if existing is None:
        raise DnsNotFoundError("RRset not found")
    if not args.apply:
        _emit(
            {"zone": zone, "deleted": False, "drift": True, "rrset": existing.to_dict()},
            json_output=args.json,
            lines=[f"would delete {type_} {owner}"],
        )
        return 3
    _confirm(args, owner, "confirm_name")
    client.delete_rrset(zone, owner, type_)
    _emit(
        {"zone": zone, "deleted": True, "rrset": existing.to_dict()},
        json_output=args.json,
        lines=[f"deleted {type_} {owner}"],
    )
    return 0


def _zone_import(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    path = Path(args.file)
    if not path.is_file():
        raise DnsValidationError("the zone file does not exist")
    content = path.read_text(encoding="utf-8")
    parsed = parse_zonefile(content, origin=zone, default_ttl=300)
    current = _current_or_none(client, zone)
    if current is not None:
        if not args.expected_version:
            raise DnsConflictError("--expected-version is required to replace an existing zone")
        if zone_version(current) != args.expected_version:
            raise DnsConflictError("the zone version changed; refresh and try again")
    if current is not None:
        validate_zone_changes(current, parsed, allow_soa=True)
        changes = plan_changes(current, {(item.name, item.type): item for item in parsed})
    else:
        changes = [{"action": "add", **item.to_dict()} for item in parsed]
    if not args.apply:
        _emit(
            {"zone": zone, "applied": False, "drift": bool(changes), "changes": changes},
            json_output=args.json,
            lines=[f"would import {len(parsed)} RRsets into {zone}"],
        )
        return 3 if changes else 0
    if current is None:
        client.create_zone(zone)
    try:
        client.patch_rrsets(zone, parsed)
    except Exception:
        if current is None:
            try:
                client.delete_zone(zone)
            except Exception:  # noqa: BLE001 - compensation is best effort
                pass
        raise
    payload = {
        "zone": zone,
        "applied": True,
        "drift": False,
        **_serialize_zone(client.get_zone(zone)),
    }
    _emit(
        payload,
        json_output=args.json,
        lines=[f"{zone}: imported", f"version: {payload['version']}"],
    )
    return 0


def _zone_export(args: argparse.Namespace, client: PowerDnsClient) -> int:
    zone = normalize_zone_name(args.zone)
    content = export_zone(client.get_zone(zone))
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    if args.json:
        _print_json({"zone": zone, "content": content})
    else:
        sys.stdout.write(content)
    return 0


# -- Cloudflare OAuth and discovery CLI -----------------------------------
def _cloudflare_runtime(args: argparse.Namespace, factory: Any):
    config = getattr(args, "cloudflare_config", None)
    if config is None:
        try:
            config = CloudflareConfig.from_env()
        except CloudflareConfigError as exc:
            raise CloudflareValidation(str(exc)) from exc
    client = getattr(args, "cloudflare_client", None) or CloudflareClient(config)
    sessions = _session_factory(args, factory)
    return config, client, sessions


def _tenant_id(db: Any, slug_or_id: str) -> str:
    """Resolve the operator-facing tenant slug, retaining ID compatibility."""
    from sqlalchemy import select

    from systutor.kernel.tenants.models import Tenant

    needle = (slug_or_id or "").strip()
    row = db.scalar(select(Tenant).where(Tenant.slug == needle))
    if row is None:
        row = db.get(Tenant, needle)
    if row is None:
        raise CloudflareNotFound("tenant not found")
    return str(row.id)


def _read_bootstrap_token(path_value: str) -> str:
    path = Path(path_value)
    try:
        details = path.stat()
    except OSError as exc:
        raise CloudflareValidation("the bootstrap token file is missing") from exc
    if not stat.S_ISREG(details.st_mode):
        raise CloudflareValidation("the bootstrap token file must be a regular file")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise CloudflareValidation("the bootstrap token file must be owner-only")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CloudflareValidation("the bootstrap token file cannot be read") from exc
    if not value or "\x00" in value:
        raise CloudflareValidation("the bootstrap token file is empty or malformed")
    return value


def _cloudflare_register(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    token = _read_bootstrap_token(args.bootstrap_token_file) if args.apply else "<not-read>"
    with sessions() as db:
        service = CloudflareService(db, None, client, config)
        payload = service.register(
            account_id=args.account_id,
            bootstrap_token=token,
            redirect_uri=args.redirect_uri,
            apply=args.apply,
        )
    if args.apply:
        lines = [
            "SUCCESS Cloudflare OAuth client registered",
            f"Client ID: {payload['client_id']}",
            f"Redirect URI: {payload['redirect_uri']}",
            f"Scopes: {', '.join(cast(list[str], payload['scopes']))}",
        ]
    else:
        lines = [
            "Cloudflare OAuth registration plan",
            f"Account: {payload['account_id']}",
            f"Redirect URI: {payload['redirect_uri']}",
            f"Scopes: {', '.join(cast(list[str], payload['scopes']))}",
            "Client secret: <redacted>",
            "dry-run; pass --apply to register",
        ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _cloudflare_connect(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    wait_seconds = max(0, min(int(args.wait_seconds), 900))
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        service = CloudflareService(db, None, client, config)
        start = service.start_flow_for_tenant(tenant_id)
    if not args.json:
        print(f"Authorize Cloudflare in the browser: {start['authorization_url']}")
        print(f"Waiting up to {wait_seconds}s for the callback...")
    if wait_seconds == 0:
        payload = {**start, "status": "pending"}
        _emit(
            payload,
            json_output=args.json,
            lines=[f"flow: {start['flow_id']}", "status: pending"],
        )
        return 0

    deadline = time.monotonic() + wait_seconds
    state = "pending"
    while time.monotonic() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        with sessions() as db:
            state = str(
                CloudflareService(db, None, client, config).flow_status(str(start["flow_id"]))[
                    "status"
                ]
            )
        if state != "pending":
            break
    if state != "consumed":
        payload = {**start, "status": state}
        _emit(
            payload,
            json_output=args.json,
            lines=[f"authorization {state}", "run connect again if the flow expired"],
        )
        return 4 if state in {"failed", "denied", "expired"} else 3

    with sessions() as db:
        service = CloudflareService(db, None, client, config)
        connection = service.status_for_tenant(tenant_id)
        zones = service.zones_for_tenant(tenant_id)
    payload = {
        "tenant_slug": args.tenant,
        "connection": connection,
        "zones": zones,
        "status": "consumed",
    }
    lines = [
        "SUCCESS Cloudflare connected",
        f"Account: {connection['account_name']}",
        f"Scopes: {', '.join(cast(list[str], connection['scopes']))}",
        f"Zones discovered: {len(zones)}",
        *(
            f"  {zone['name']}  {zone['status']}  {zone['record_count']} records"
            for zone in zones
        ),
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _cloudflare_status(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        payload = CloudflareService(db, None, client, config).status_for_tenant(tenant_id)
    payload["tenant_slug"] = args.tenant
    lines = [
        "Cloudflare connected" if payload["connected"] else "Cloudflare not connected",
        f"Account: {payload['account_name']} ({payload['account_id']})",
        f"Scopes: {', '.join(cast(list[str], payload['scopes']))}",
        f"Status: {payload['status']}",
        f"Expires: {payload['expires_at']}",
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _cloudflare_disconnect(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        payload = CloudflareService(db, None, client, config).disconnect_for_tenant(
            tenant_id, apply=args.apply
        )
    payload["tenant_slug"] = args.tenant
    if payload["already_disconnected"]:
        lines = ["Cloudflare is already disconnected"]
    elif args.apply:
        lines = [
            "Cloudflare disconnected",
            f"Revoked upstream: {'yes' if payload['revoked'] else 'no'}",
            f"Cancelled flows: {payload.get('cancelled_flows', 0)}",
        ]
    else:
        lines = [
            "Cloudflare disconnect plan",
            f"Account: {payload['account_name']} ({payload['account_id']})",
            f"Pending flows: {payload.get('pending_flows', 0)}",
            "dry-run; pass --apply to revoke and remove credentials",
        ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _cloudflare_zones_list(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        zones = CloudflareService(db, None, client, config).zones_for_tenant(tenant_id)
    payload = {"tenant_id": tenant_id, "tenant_slug": args.tenant, "zones": zones}
    lines = [
        f"Zones discovered: {len(zones)}",
        *(
            f"  {zone['name']}  {zone['status']}  {zone['record_count']} records"
            for zone in zones
        ),
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _cloudflare_records_list(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    needle = args.zone.strip()
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        service = CloudflareService(db, None, client, config)
        zones = service.zones_for_tenant(tenant_id)
        match = next(
            (
                zone
                for zone in zones
                if zone["id"] == needle
                or str(zone["name"]).rstrip(".") == needle.rstrip(".")
            ),
            None,
        )
        if match is None:
            raise CloudflareNotFound("Cloudflare zone not found")
        zone_id = str(match["id"])
        records = service.records_for_tenant(tenant_id, zone_id)
    payload = {
        "tenant_id": tenant_id,
        "tenant_slug": args.tenant,
        "zone_id": zone_id,
        "zone": match["name"],
        "records": records,
    }
    lines = [
        f"zone: {match['name']}",
        *(
            f"  {record['name']}  {record['type']}  TTL {record['ttl']}"
            f"  proxied={'yes' if record['proxied'] else 'no'}"
            for record in records
        ),
    ]
    _emit(payload, json_output=args.json, lines=lines)
    return 0


def _provision_lines(payload: dict[str, Any], *, applied: bool) -> list[str]:
    action = "created" if payload["zone_action"] == "create" else "reused"
    zone = payload.get("zone")
    zone_name = zone.get("name") if isinstance(zone, dict) else payload["domain"]
    lines = [
        f"Cloudflare zone: {action} {zone_name}",
        *(
            f"  {record['name']} {record['type']} {record['content']} "
            f"TTL Auto, DNS only"
            for record in payload["desired_records"]
        ),
    ]
    nameservers = payload.get("nameservers") or []
    if nameservers:
        lines.append("Nameservers: " + ", ".join(str(value) for value in nameservers))
    if payload["zone_action"] == "create":
        lines.append(
            "Delegate the domain at its registrar to the returned Cloudflare nameservers."
        )
    if payload.get("conflicts") and not payload.get("replace"):
        lines.append("Conflicts block apply; repeat plan and apply with --replace.")
    lines.append("Applied successfully." if applied else "Plan only; no Cloudflare changes made.")
    return lines


def _cloudflare_provision(args: argparse.Namespace, factory: Any) -> int:
    config, client, sessions = _cloudflare_runtime(args, factory)
    with sessions() as db:
        tenant_id = _tenant_id(db, args.tenant)
        provisioner = CloudflareProvisioningService(
            CloudflareService(db, None, client, config)
        )
        should_apply = (
            args.cloudflare_provision_command == "apply" and bool(args.apply)
        )
        if should_apply:
            payload = provisioner.apply_for_tenant(
                tenant_id,
                domain=args.domain,
                ipv4=args.ipv4,
                www=args.www,
                replace=args.replace,
            )
        else:
            payload = provisioner.plan_for_tenant(
                tenant_id,
                domain=args.domain,
                ipv4=args.ipv4,
                www=args.www,
                replace=args.replace,
            )
        payload["tenant_id"] = tenant_id
        payload["tenant_slug"] = args.tenant
    _emit(
        payload,
        json_output=args.json,
        lines=_provision_lines(payload, applied=should_apply),
    )
    return 0 if should_apply or not payload["drift"] else 3
