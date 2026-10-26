#!/usr/bin/env python3
"""Set or fix the primary domain of an already adopted hosting site. Operator CLI.

Adoption and domain are two separate facts: a container can be in
`hosting_sites` with `domain = NULL` (adopted without `--domain`, or created
before the column mattered). This script fills that column for an existing
row, with the same guards Hosting applies when it creates a site:

- the container must already belong to a tenant;
- the domain must be unique across every site;
- the domain cannot be an extra route already managed by the domains plugin;
- the domain must be inside the tenant `site_suffix` unless `--allow-outside`.

It never starts, stops or writes to the container, and it records an audit row.

Usage:
    python3 scripts/hosting_set_domain.py --list
    python3 scripts/hosting_set_domain.py --container systutor --domain systutor.com --dry-run
    python3 scripts/hosting_set_domain.py --container portafolio --domain lucas.systutor.com

Exit codes: 0 ok (or dry-run), 1 validation/DB failure, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import (  # pyright: ignore[reportMissingImports]
    HostingSite,
)
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit

_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class SetDomainError(Exception):
    """A failure explained to the operator, not a traceback."""


def normalize_domain(value: str) -> str:
    text = value.strip().lower().removesuffix(".")
    if not text or _DOMAIN_RE.fullmatch(text) is None:
        raise SetDomainError(
            f"dominio invalido: {value!r} (sin esquema, puerto, ruta ni espacios)"
        )
    return text


def _site_by_container(db: Session, container_name: str) -> HostingSite:
    site = db.scalar(
        select(HostingSite).where(HostingSite.container_name == container_name)
    )
    if site is None:
        raise SetDomainError(
            f"el contenedor {container_name!r} no esta adoptado; "
            "usá scripts/hosting_adopt.py primero"
        )
    return site


def _domain_owned_by_other(db: Session, domain: str, site_id: str) -> str | None:
    row = db.scalar(
        select(HostingSite).where(
            HostingSite.domain == domain, HostingSite.id != site_id
        )
    )
    return row.container_name if row is not None else None


def _domain_is_extra(domain: str) -> str | None:
    """Container that already publishes the domain as a proxy extra, if any.

    A proxy that is not configured is a warning, not a blocker: the API works
    the same way when the domains plugin cannot reach Traefik.
    """
    import os

    try:
        from plugins.domains.backend.proxy import (  # pyright: ignore[reportMissingImports]
            ProxyOperations,
            build_runner,
        )
        from plugins.domains.backend.proxy import (
            Settings as ProxySettings,
        )

        profile = os.getenv("SPANEL_DOMAINS_PROFILE", "public").strip() or "public"
        settings = ProxySettings.from_env(profile)
        operations = ProxyOperations(settings, build_runner(settings))
        for row in operations.managed_domains():
            if row.domain.lower() == domain:
                return row.container
    except Exception as exc:  # noqa: BLE001 - visible warning for the operator
        print(f"aviso: no se pudieron leer los extras del proxy: {exc}", file=sys.stderr)
    return None


def _tenant_suffix(db: Session, tenant_id: str) -> str | None:
    try:
        from plugins.tenant.backend.models import (  # pyright: ignore[reportMissingImports]
            TenantSiteSettings,
        )
    except ImportError:
        return None
    row = db.get(TenantSiteSettings, tenant_id)
    return row.site_suffix if row is not None else None


def _inside_suffix(domain: str, suffix: str) -> bool:
    needle = suffix.strip().lower().removesuffix(".")
    return domain == needle or domain.endswith(f".{needle}")


def set_domain(
    db: Session,
    *,
    container_name: str,
    domain: str,
    dry_run: bool = False,
    allow_outside: bool = False,
) -> tuple[HostingSite, str | None]:
    needle = normalize_domain(domain)
    site = _site_by_container(db, container_name)
    previous = site.domain

    if previous == needle:
        raise SetDomainError(
            f"{container_name} ya tiene el dominio {needle}; no hay nada que hacer"
        )

    owner = _domain_owned_by_other(db, needle, site.id)
    if owner is not None:
        raise SetDomainError(
            f"el dominio {needle} ya pertenece al contenedor {owner}"
        )

    extra_owner = _domain_is_extra(needle)
    if extra_owner is not None:
        raise SetDomainError(
            f"el dominio {needle} ya esta publicado como extra por {extra_owner}"
        )

    suffix = _tenant_suffix(db, site.tenant_id)
    if suffix and not _inside_suffix(needle, suffix):
        message = (
            f"{needle} esta fuera del sufijo {suffix} del tenant; "
            "un admin de tenant no lo va a ver"
        )
        if not allow_outside:
            raise SetDomainError(message + " (usá --allow-outside para forzarlo)")
        print(f"aviso: {message}", file=sys.stderr)

    if dry_run:
        return site, previous

    site.domain = needle
    record_audit(
        db,
        tenant_id=site.tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="hosting",
        action="hosting.site.domain_set",
        entity_type="hosting_site",
        entity_id=site.id,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "container_name": container_name,
            "domain": needle,
            "previous_domain": previous,
        },
    )
    db.commit()
    return site, previous


def _list(db: Session) -> int:
    rows = list(db.scalars(select(HostingSite).order_by(HostingSite.container_name)))
    if not rows:
        print("no hay sitios adoptados")
        return 0
    print(f"{'CONTENEDOR':<28} {'DOMINIO':<40} {'TIPO':<10} TENANT")
    for site in rows:
        print(
            f"{site.container_name:<28} {(site.domain or '(sin dominio)'):<40} "
            f"{site.kind:<10} {site.tenant_id}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--container", help="nombre del contenedor ya adoptado")
    parser.add_argument("--domain", help="dominio publico del sitio")
    parser.add_argument("--list", action="store_true", help="listar sitios y dominios")
    parser.add_argument(
        "--dry-run", action="store_true", help="validar sin escribir ni auditar"
    )
    parser.add_argument(
        "--allow-outside",
        action="store_true",
        help="permitir un dominio fuera del sufijo del tenant",
    )
    args = parser.parse_args(argv)

    settings = Settings(**env_settings_kwargs())
    session_factory = build_session_factory(settings)
    with session_factory() as db:
        if args.list:
            return _list(db)
        if not args.container or not args.domain:
            parser.error("--container y --domain son obligatorios (o usá --list)")
        try:
            site, previous = set_domain(
                db,
                container_name=args.container,
                domain=args.domain,
                dry_run=args.dry_run,
                allow_outside=args.allow_outside,
            )
        except SetDomainError as exc:
            print(f"FALLO: {exc}")
            return 2
        except Exception as exc:  # noqa: BLE001 - explained to the operator
            print(f"FALLO: no se pudo actualizar el dominio: {exc}")
            return 1

    if args.dry_run:
        print(
            f"dry-run: {site.container_name}: {previous or '(sin dominio)'} -> "
            f"{args.domain.strip().lower()}; no se escribio nada"
        )
        return 0
    print(
        f"OK: {site.container_name}: {previous or '(sin dominio)'} -> {site.domain}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
