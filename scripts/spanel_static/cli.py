"""CLI de sitios estáticos.

Ejemplos:
  python3 scripts/spanel-static.py create --name paginadelucas
  python3 scripts/spanel-static.py create --name paginadelucas --domain paginadelucas.spanel
  python3 scripts/spanel-static.py list
  python3 scripts/spanel-static.py status --domain paginadelucas.spanel
  python3 scripts/spanel-static.py delete --name paginadelucas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import StaticSettings, build_settings
from .docker import DockerError
from .names import SiteNames, slugify, validate_domain
from .provision import (
    CliError,
    create_site,
    delete_site,
    doctor,
    list_sites,
    status_site,
)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", choices=("internal", "public"), default=argparse.SUPPRESS)
    common.add_argument(
        "--transport", choices=("auto", "local", "ssh"), default=argparse.SUPPRESS
    )

    parser = argparse.ArgumentParser(
        prog="spanel-static.py",
        description="Crea y administra sitios estáticos (HTML/CSS/JS) en Docker + Traefik.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--profile", choices=("internal", "public"), default=None)
    parser.add_argument("--transport", choices=("auto", "local", "ssh"), default=None)
    parser.add_argument("--network", default=None, help="red Docker del proxy")
    parser.add_argument("--image", default=None, help="imagen nginx (default nginx:alpine)")
    parser.add_argument(
        "--wait", type=int, default=None, help="segundos de espera al certificado"
    )
    parser.add_argument(
        "--remote-repo",
        default=None,
        help="ruta del repo en el host Docker (transporte SSH)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser(
        "create",
        parents=[common],
        help="crea el contenedor, el volumen, el contenido y la ruta HTTPS",
    )
    create.add_argument("--name", required=True, help="nombre corto (ej. paginadelucas)")
    create.add_argument("--domain", default=None, help="FQDN (default <slug>.spanel)")
    create.add_argument("--www", action="store_true", help="publicar también www.<dominio>")
    create.add_argument("--force", action="store_true", help="sobrescribir el contenido inicial")
    create.add_argument(
        "--reuse", action="store_true", help="reutilizar un contenedor/volumen ya existente"
    )
    create.add_argument("--dry-run", action="store_true", help="imprime los comandos sin ejecutar")

    sub.add_parser("list", parents=[common], help="lista los sitios estáticos")

    status = sub.add_parser("status", parents=[common], help="estado del contenedor y del certificado")
    status.add_argument("--name", default=None)
    status.add_argument("--domain", default=None)

    delete = sub.add_parser("delete", parents=[common], help="borra contenedor, volumen y ruta")
    delete.add_argument("--name", default=None)
    delete.add_argument("--domain", default=None)
    delete.add_argument("--keep-volume", action="store_true", help="conservar los datos")
    delete.add_argument("--dry-run", action="store_true")

    sub.add_parser("doctor", parents=[common], help="verifica Docker, red, imagen y proxy")
    return parser


def _settings(args: argparse.Namespace) -> StaticSettings:
    return build_settings(
        profile=getattr(args, "profile", None),
        transport=getattr(args, "transport", None),
        network=getattr(args, "network", None),
        image=getattr(args, "image", None),
        wait_seconds=getattr(args, "wait", None),
        remote_repo=getattr(args, "remote_repo", None),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = _settings(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "create":
            slug = slugify(args.name)
            domain = args.domain or (
                f"{slug}.spanel" if settings.profile == "internal" else ""
            )
            if not domain:
                raise CliError("En perfil public --domain es obligatorio", 2)
            domain = validate_domain(domain, settings.profile)
            return create_site(
                settings,
                name=slug,
                domain=domain,
                force=args.force,
                reuse=args.reuse,
                www=args.www,
                dry_run=args.dry_run,
            )
        if args.command == "list":
            return list_sites(settings)
        if args.command == "status":
            name = slugify(args.name) if args.name else None
            domain = validate_domain(args.domain, settings.profile) if args.domain else None
            if not name and not domain:
                raise CliError("Indicá --name o --domain", 2)
            return status_site(settings, name=name, domain=domain)
        if args.command == "delete":
            name = slugify(args.name) if args.name else None
            domain = validate_domain(args.domain, settings.profile) if args.domain else None
            if not name and not domain:
                raise CliError("Indicá --name o --domain", 2)
            return delete_site(
                settings,
                name=name,
                domain=domain,
                keep_volume=args.keep_volume,
                dry_run=args.dry_run,
            )
        if args.command == "doctor":
            return doctor(settings)
    except (CliError, DockerError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 2)
    except KeyboardInterrupt:
        print("interrumpido", file=sys.stderr)
        return 130

    parser.error(f"comando desconocido: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
