"""CLI de los perfiles de estáticos.

Ejemplos:
  python3 scripts/spanel-profiles.py status
  python3 scripts/spanel-profiles.py up
  python3 scripts/spanel-profiles.py up --only astro
  python3 scripts/spanel-profiles.py verify
  python3 scripts/spanel-profiles.py down
"""

from __future__ import annotations

import argparse
import sys

from spanel_static.config import build_settings
from spanel_static.provision import CliError

from . import profiles, provision


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanel-profiles.py",
        description=(
            "Verificación determinista de perfiles de estáticos "
            "(A.SPEC 0042): plain(nginx), react(caddy), boot(httpd), astro(node)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--transport", choices=("auto", "local", "ssh"), default=None,
        help="auto (default; usa SSH configurado si no hay daemon local)",
    )
    parser.add_argument(
        "--profile", choices=("internal", "public"), default=None,
        help="internal (laboratorio, default) o public",
    )
    parser.add_argument("--network", default=None, help="red Docker del proxy")
    parser.add_argument("--wait", type=int, default=None, help="segundos máx. de health check")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="provisiona volúmenes, contenedores, seed y rutas")
    up.add_argument("--only", action="append", help="repite para varios: --only astro")
    up.add_argument("--force", action="store_true", help="reescribe el seed del contendio")
    up.add_argument("--dry-run", action="store_true", help="imprime qué haría, sin ejecutar")

    verify = sub.add_parser("verify", help="verifica contenedor, imagen, labels, health y HTTPS")
    verify.add_argument("--only", action="append", help="verifica perfiles concretos")

    down = sub.add_parser("down", help="quita contenedores, volúmenes y rutas de los perfiles")
    down.add_argument("--only", action="append", help="baja solo estos perfiles")
    down.add_argument("--keep-routes", action="store_true", help="no toca el file provider")

    sub.add_parser("status", help="lista el estado de cada perfil")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = build_settings(
            profile=args.profile,
            transport=args.transport,
            network=args.network,
            wait_seconds=args.wait,
        )
        only = args.only if getattr(args, "only", None) else None
        if only:
            get = profiles.get_profiles  # valida los slugs lo antes posible
            get(only)
        if args.command == "up":
            if args.dry_run:
                for spec in profiles.get_profiles(only).values():
                    print(
                        f"# {spec.slug}: docker run -v {spec.volume}:{spec.web_root} "
                        f"{spec.image}{' sh -c <command>' if spec.command else ''}"
                    )
                    print(f"# route: proxy_domain.py add --site {spec.container} --domain {spec.domain}")
                return 0
            return provision.up(settings, only, force=args.force)
        if args.command == "verify":
            return provision.verify(settings, only)
        if args.command == "down":
            return provision.down(settings, only, keep_routes=args.keep_routes)
        if args.command == "status":
            return provision.status(settings, only)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
