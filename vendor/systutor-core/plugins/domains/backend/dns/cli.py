"""Operator CLI parser and dispatcher for the Domains DNS capability.

The parser and presentation live here; all transport, validation, versioning
and zone-file work is delegated to the shared DNS modules.  No command shells
out to external DNS tools or the future HTTP API.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

from plugins.domains.backend.dns.cli_commands import (
    _doctor,
    _emit,
    _nameserver_apply,
    _nameserver_plan,
    _nameserver_show,
    _print_json,
    _rrsets_apply,
    _rrsets_delete,
    _rrsets_list,
    _rrsets_plan,
    _zone_export,
    _zone_import,
    _zones_create,
    _zones_delete,
    _zones_list,
    _zones_show,
)
from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import DnsError, DnsValidationError
from plugins.domains.backend.dns.resolver import verify_nameservers

ClientFactory = Callable[[], PowerDnsClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanel_dns.py",
        description=(
            "Operate the Domains PowerDNS capability. "
            "Mutations are plans unless --apply is given."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _leaf(sub, "doctor", "Check host configuration and authenticated PowerDNS health.")

    nameservers = sub.add_parser("nameservers", help="Public nameserver settings")
    nameserver_commands = nameservers.add_subparsers(dest="nameservers_command", required=True)
    show = _leaf(nameserver_commands, "show", "Show public nameserver settings.")
    _add_db(show)
    plan = _leaf(nameserver_commands, "plan", "Report settings and apex drift.")
    _add_db(plan)
    _add_ns_options(plan, required=False)
    _add_approved_zones(plan)
    apply_ = _leaf(nameserver_commands, "apply", "Persist settings and reconcile approved zones.")
    _add_db(apply_)
    _add_ns_options(apply_, required=False)
    _add_approved_zones(apply_)
    apply_.add_argument("--zone-version", action="append", default=[])
    apply_.add_argument("--expected-version", type=int)
    apply_.add_argument("--apply", action="store_true")
    verify = _leaf(nameserver_commands, "verify", "Query SOA/NS over UDP and TCP.")
    verify.add_argument("--zone", required=True)
    verify.add_argument("--resolver", required=True)
    verify.add_argument("--port", type=int, default=53)
    verify.add_argument("--timeout", type=float, default=5.0)

    zones = sub.add_parser("zones", help="Authoritative zones")
    zone_commands = zones.add_subparsers(dest="zones_command", required=True)
    _leaf(zone_commands, "list", "List zones visible to the API.")
    create = _leaf(zone_commands, "create", "Create a zone from public settings.")
    create.add_argument("zone")
    _add_ns_options(create, required=False)
    create.add_argument("--apply", action="store_true")
    show_zone = _leaf(zone_commands, "show", "Show one zone and its RRsets.")
    show_zone.add_argument("zone")
    delete_zone = _leaf(zone_commands, "delete", "Delete one zone after confirmation.")
    delete_zone.add_argument("zone")
    delete_zone.add_argument("--expected-version")
    delete_zone.add_argument("--confirm-zone")
    delete_zone.add_argument("--apply", action="store_true")

    rrsets = sub.add_parser("rrsets", help="Complete RRsets")
    rrset_commands = rrsets.add_subparsers(dest="rrsets_command", required=True)
    list_rrsets = _leaf(rrset_commands, "list", "List RRsets in a zone.")
    list_rrsets.add_argument("zone")
    plan_rrsets = _leaf(rrset_commands, "plan", "Plan one complete RRset.")
    _add_rrset_arguments(plan_rrsets)
    apply_rrsets = _leaf(rrset_commands, "apply", "Apply one complete RRset.")
    _add_rrset_arguments(apply_rrsets)
    apply_rrsets.add_argument("--expected-version", required=True)
    apply_rrsets.add_argument("--apply", action="store_true")
    delete_rrset = _leaf(rrset_commands, "delete", "Delete one RRset after confirmation.")
    delete_rrset.add_argument("zone")
    delete_rrset.add_argument("--name", required=True)
    delete_rrset.add_argument("--type", required=True)
    delete_rrset.add_argument("--expected-version", required=True)
    delete_rrset.add_argument("--confirm-name")
    delete_rrset.add_argument("--apply", action="store_true")

    zone = sub.add_parser("zone", help="Zone-file import and export")
    zone_commands = zone.add_subparsers(dest="zone_command", required=True)
    import_ = _leaf(zone_commands, "import", "Import a deterministic zone file.")
    import_.add_argument("zone")
    import_.add_argument("--file", required=True)
    import_.add_argument("--expected-version")
    import_.add_argument("--apply", action="store_true")
    export_ = _leaf(zone_commands, "export", "Export a deterministic zone file.")
    export_.add_argument("zone")
    export_.add_argument("--output")
    return parser


def _leaf(parent: argparse._SubParsersAction, name: str, help_text: str) -> argparse.ArgumentParser:
    child = parent.add_parser(name, help=help_text)
    child.add_argument("--json", action="store_true", help="Machine-stable JSON output")
    return child


def _add_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-url", default=os.getenv("SYSTUTOR_DATABASE_URL", ""))


def _add_approved_zones(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--zone", action="append", default=None)
    group.add_argument(
        "--no-zones",
        action="store_true",
        help="Explicitly approve an empty zone set (bootstrap settings only)",
    )


def _add_ns_options(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--ns1", required=required)
    parser.add_argument("--ns2", required=required)
    parser.add_argument("--ns1-ipv4")
    parser.add_argument("--ns1-ipv6")
    parser.add_argument("--ns2-ipv4")
    parser.add_argument("--ns2-ipv6")
    parser.add_argument("--soa-contact")
    parser.add_argument("--default-ttl", type=int, default=300)


def _add_rrset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("zone")
    parser.add_argument("--name", required=True)
    parser.add_argument("--type", required=True)
    parser.add_argument("--ttl", type=int, default=300)
    parser.add_argument("--value", action="append", required=True)


def run(
    argv: Sequence[str] | None = None,
    *,
    client: PowerDnsClient | None = None,
    client_factory: ClientFactory | None = None,
    session_factory: Any = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        need_client = args.command in {"doctor", "zones", "rrsets", "zone"} or (
            args.command == "nameservers" and args.nameservers_command in {"plan", "apply"}
        )
        active = client
        if active is None and need_client:
            if client_factory is not None:
                active = client_factory()
            else:
                active = PowerDnsClient(PowerDnsConfig.from_env())
        if args.command == "doctor":
            return _doctor(args, active)  # type: ignore[arg-type]
        if args.command == "nameservers":
            if args.nameservers_command == "show":
                return _nameserver_show(args, session_factory)
            if args.nameservers_command == "plan":
                return _nameserver_plan(args, active, session_factory)  # type: ignore[arg-type]
            if args.nameservers_command == "apply":
                return _nameserver_apply(args, active, session_factory)  # type: ignore[arg-type]
            if args.nameservers_command == "verify":
                payload = verify_nameservers(
                    args.zone,
                    resolver=args.resolver,
                    port=args.port,
                    timeout=args.timeout,
                )
                _emit(
                    payload,
                    json_output=args.json,
                    lines=[
                        f"zone: {args.zone}",
                        f"resolver: {args.resolver}:{args.port}",
                        "UDP/TCP SOA and NS: PASS",
                        str(payload["warning"]),
                    ],
                )
                return 0
        if args.command == "zones":
            return {
                "list": _zones_list,
                "create": _zones_create,
                "show": _zones_show,
                "delete": _zones_delete,
            }[args.zones_command](args, active)  # type: ignore[arg-type]
        if args.command == "rrsets":
            return {
                "list": _rrsets_list,
                "plan": _rrsets_plan,
                "apply": _rrsets_apply,
                "delete": _rrsets_delete,
            }[args.rrsets_command](args, active)  # type: ignore[arg-type]
        if args.command == "zone":
            return {
                "import": _zone_import,
                "export": _zone_export,
            }[args.zone_command](args, active)  # type: ignore[arg-type]
        raise DnsValidationError("unknown command")
    except DnsError as exc:
        if getattr(args, "json", False):
            _print_json({"error": {"code": exc.code, "message": str(exc)}})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)
