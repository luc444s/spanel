#!/usr/bin/env python3
"""CLI de dominios de SPanel.

Diagnostica el proxy, agrega/quita dominios gestionados y consulta el estado
del certificado. Es una entrada delgada sobre el plugin `domains`: la
implementación vive en `vendor/systutor-core/plugins/domains/backend/proxy/`.

Perfiles:
  internal  laboratorio Docker aislado con CA ACME local (por defecto)
  public    Traefik real con Let's Encrypt (requiere FQDN público)

Ejemplos:
  python3 scripts/proxy_domain.py doctor
  python3 scripts/proxy_domain.py add --site spanel-lucas-test-wp --domain lucas.spanel
  python3 scripts/proxy_domain.py status --domain lucas.spanel
  python3 scripts/proxy_domain.py list
  python3 scripts/proxy_domain.py remove --domain lucas.spanel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_VENDOR_CORE = Path(__file__).resolve().parents[1] / "vendor" / "systutor-core"
if str(_VENDOR_CORE) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CORE))

from plugins.domains.backend.proxy import (  # pyright: ignore[reportMissingImports]
    PROFILES,
    PathRoute,
    ProxyError,
    ProxyOperations,
    Settings,
    build_runner,
)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--profile",
        choices=PROFILES,
        default=argparse.SUPPRESS,
        help="internal (lab, default) or public (real Let's Encrypt)",
    )

    parser = argparse.ArgumentParser(
        prog="proxy_domain.py",
        description="Manage SPanel proxy domains and certificates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="internal",
        help="internal (lab, default) or public (real Let's Encrypt)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", parents=[common], help="check proxy, CA and directories")
    sub.add_parser("list", parents=[common], help="list managed domains")

    add = sub.add_parser("add", parents=[common], help="add a domain to a site")
    add.add_argument("--site", required=True, help="site container (e.g. spanel-lucas-wp)")
    add.add_argument("--domain", required=True, help="FQDN to add (e.g. lucas.spanel)")
    add.add_argument("--site-port", type=int, help="site internal port (default 80)")
    add.add_argument("--path-prefix", help="same-origin path prefix (e.g. /api)")
    add.add_argument("--path-site", help="container serving the prefix")
    add.add_argument("--path-port", type=int, help="internal port of the route container")
    add.add_argument("--www", action="store_true", help="also add www.<domain>")
    add.add_argument("--dry-run", action="store_true", help="print the YAML without writing")
    add.add_argument(
        "--set-wordpress-url",
        action="store_true",
        help="update WordPress home and siteurl with wp-cli",
    )
    add.add_argument(
        "--open",
        action="store_true",
        help="open the link in the host browser when done",
    )
    add.add_argument(
        "--wait",
        type=int,
        default=None,
        help="seconds to wait for the certificate (0 = do not wait)",
    )

    status = sub.add_parser(
        "status", parents=[common], help="show the certificate status of a domain"
    )
    status.add_argument("--domain", required=True)

    remove = sub.add_parser("remove", parents=[common], help="remove a managed domain")
    remove.add_argument("--domain", required=True)
    remove.add_argument("--dry-run", action="store_true", help="does not write or delete")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profile = getattr(args, "profile", "internal")

    try:
        settings = Settings.from_env(profile)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    operations = ProxyOperations(settings, build_runner(settings))

    try:
        if args.command == "doctor":
            return operations.doctor()
        if args.command == "list":
            return operations.list_domains()
        if args.command == "add":
            route_values = (args.path_prefix, args.path_site, args.path_port)
            if any(value is not None for value in route_values) and not all(
                value is not None for value in route_values
            ):
                parser.error("--path-prefix, --path-site and --path-port must be used together")
            routes = (
                [PathRoute(args.path_prefix, args.path_site, args.path_port)]
                if all(value is not None for value in route_values)
                else None
            )
            return operations.add(
                args.site,
                args.domain,
                www=args.www,
                dry_run=args.dry_run,
                set_wordpress_url=args.set_wordpress_url,
                open_browser=args.open,
                wait=args.wait,
                port=args.site_port,
                path_routes=routes,
            )
        if args.command == "status":
            return operations.status(args.domain)
        if args.command == "remove":
            return operations.remove(args.domain, dry_run=args.dry_run)
    except ProxyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
