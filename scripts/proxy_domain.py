#!/usr/bin/env python3
"""CLI de dominios de SPanel.

Diagnostica el proxy, agrega/quita dominios gestionados y consulta el estado
del certificado. Reutiliza la misma lógica que el futuro plugin `proxy`.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spanel_proxy.config import PROFILES, Settings
from spanel_proxy.core import ProxyError, build_runner
from spanel_proxy.operations import ProxyOperations


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--profile",
        choices=PROFILES,
        default=argparse.SUPPRESS,
        help="internal (laboratorio, default) o public (Let's Encrypt real)",
    )

    parser = argparse.ArgumentParser(
        prog="proxy_domain.py",
        description="Gestiona dominios y certificados del proxy de SPanel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="internal",
        help="internal (laboratorio, default) o public (Let's Encrypt real)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", parents=[common], help="verifica proxy, CA y directorios")
    sub.add_parser("list", parents=[common], help="lista los dominios gestionados")

    add = sub.add_parser("add", parents=[common], help="agrega un dominio a un sitio")
    add.add_argument("--site", required=True, help="contenedor del sitio (ej. spanel-lucas-wp)")
    add.add_argument("--domain", required=True, help="FQDN a agregar (ej. lucas.spanel)")
    add.add_argument("--www", action="store_true", help="agregar también www.<dominio>")
    add.add_argument("--dry-run", action="store_true", help="imprime el YAML sin escribir")
    add.add_argument(
        "--set-wordpress-url",
        action="store_true",
        help="actualiza home y siteurl de WordPress con wp-cli",
    )
    add.add_argument(
        "--open",
        action="store_true",
        help="abre el link en el navegador del host al terminar",
    )
    add.add_argument(
        "--wait",
        type=int,
        default=None,
        help="segundos de espera al certificado (0 = no esperar)",
    )

    status = sub.add_parser(
        "status", parents=[common], help="muestra el estado del certificado de un dominio"
    )
    status.add_argument("--domain", required=True)

    remove = sub.add_parser("remove", parents=[common], help="quita un dominio gestionado")
    remove.add_argument("--domain", required=True)
    remove.add_argument("--dry-run", action="store_true", help="no escribe ni borra")

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
            return operations.add(
                args.site,
                args.domain,
                www=args.www,
                dry_run=args.dry_run,
                set_wordpress_url=args.set_wordpress_url,
                open_browser=args.open,
                wait=args.wait,
            )
        if args.command == "status":
            return operations.status(args.domain)
        if args.command == "remove":
            return operations.remove(args.domain, dry_run=args.dry_run)
    except ProxyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrumpido", file=sys.stderr)
        return 130

    parser.error(f"comando desconocido: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
