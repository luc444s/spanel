#!/usr/bin/env python3
"""Adopt the real sites running on the Docker host and set their primary domain.

Production had real containers that were never adopted (their names do not
follow the `spanel-*` convention) and adopted rows with `domain = NULL`. This
operator CLI reconciles both in one idempotent pass:

- a container that is not in `hosting_sites` is adopted;
- a container that is already adopted but has no domain gets one;
- a container whose `kind` is overridden in the manifest is fixed;
- a stale row can be released with `--retire-old` after the good container is
  adopted (DB only: the container is never touched).

Every write is audited. The tenant is resolved by `site_suffix`/`domain` of
the existing tenants; if zero or several match, the item is skipped and the
operator has to pass `--assign <container>=<tenant-slug>`.

Usage:
    python3 scripts/hosting_sync.py
    python3 scripts/hosting_sync.py --only systutor --only portafolio
    python3 scripts/hosting_sync.py --assign ardiffx=ardiffx --apply
    python3 scripts/hosting_sync.py --apply --retire-old

Exit codes: 0 ok, 1 at least one item failed, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import systutor.kernel.models  # noqa: F401
from hosting_set_domain import (  # pyright: ignore[reportMissingImports]
    SetDomainError,
    _domain_is_extra,
    _inside_suffix,
    _tenant_suffix,
    normalize_domain,
)
from plugins.hosting.backend.inspection import (  # pyright: ignore[reportMissingImports]
    candidato_de,
    inspect_container,
)
from plugins.hosting.backend.models import (  # pyright: ignore[reportMissingImports]
    HostingSite,
)
from plugins.hosting.backend.settings import (  # pyright: ignore[reportMissingImports]
    docker_env,
)
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant, new_uuid

# container -> (domain, kind override, why it matters)
MANIFEST: dict[str, tuple[str, str | None]] = {
    "systutor": ("systutor.com", None),
    "portafolio": ("lucas.systutor.com", None),
    "infra": ("infra.systutor.com", None),
    "gestiongases": ("gestiongasesindustriales.com", None),
    "ardiffx": ("ardiffx.com", None),
    "tutorabusiness": ("tutorabusiness.com", None),
    "legalattorney-clean": ("legalattorney.pe", None),
    "consorciozia": ("consorciozia.com", "static"),
}

# Old/unpublished containers that should disappear from the panel once the good
# one is adopted. Their Docker containers stay untouched.
RETIRE: dict[str, str] = {
    "legalattorney": "legalattorney-clean",
}

# Containers that are deliberately not sites: mail landing, webmail, panel,
# proxy, databases and the whoami test that duplicates mail.miltonarquez.com.
IGNORED = (
    "mail-landing",
    "whoami-mail",
    "webmail",
    "spanel-app-1",
    "spanel-postgres-1",
    "spanel-redis-1",
    "spanel-phpmyadmin",
    "traefik",
    "acme-webroot",
    "mailserver",
    "mariadb",
    "legalattorney-clean-db",
)


class SyncError(Exception):
    """A failure explained to the operator, not a traceback."""


def _site_by_container(db: Session, container_name: str) -> HostingSite | None:
    return db.scalar(
        select(HostingSite).where(HostingSite.container_name == container_name)
    )


def _tenant_by_slug(db: Session, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise SyncError(f"no existe el tenant con slug {slug!r}")
    return tenant


def _resolve_tenant(db: Session, domain: str, explicit: str | None) -> Tenant:
    if explicit:
        return _tenant_by_slug(db, explicit)

    matches: dict[str, Tenant] = {}
    for tenant in db.scalars(select(Tenant)):
        suffix = _tenant_suffix(db, tenant.id)
        tenant_domain = (tenant.domain or "").strip().lower()
        covered = bool(suffix and _inside_suffix(domain, suffix)) or bool(
            tenant_domain and _inside_suffix(domain, tenant_domain)
        )
        if covered:
            matches[tenant.id] = tenant

    if len(matches) == 1:
        return next(iter(matches.values()))
    if not matches:
        raise SyncError(
            f"{domain}: ningún tenant tiene ese dominio ni un site_suffix que lo "
            "cubra; creá el tenant o pasá --assign <contenedor>=<slug>"
        )
    names = ", ".join(sorted(t.slug for t in matches.values()))
    raise SyncError(
        f"{domain}: varios tenants lo cubren ({names}); elegí con "
        "--assign <contenedor>=<slug>"
    )


def _check_domain(
    db: Session,
    domain: str,
    *,
    site_id: str | None,
    tenant: Tenant,
    allow_outside: bool,
) -> None:
    conditions = [HostingSite.domain == domain]
    if site_id:
        conditions.append(HostingSite.id != site_id)
    other = db.scalar(select(HostingSite).where(*conditions))
    if other is not None:
        raise SyncError(f"{domain}: ya pertenece al contenedor {other.container_name}")

    extra_owner = _domain_is_extra(domain)
    if extra_owner is not None:
        raise SyncError(f"{domain}: ya está publicado como extra por {extra_owner}")

    suffix = _tenant_suffix(db, tenant.id)
    if suffix and not _inside_suffix(domain, suffix):
        message = f"{domain}: fuera del sufijo {suffix} del tenant {tenant.slug}"
        if not allow_outside:
            raise SyncError(message + " (usá --allow-outside para forzarlo)")
        print(f"aviso: {message}")


def _assert_site_container(container_name: str) -> None:
    info = inspect_container(
        docker_env(), container_name, timeout=60
    )
    if info is None:
        raise SyncError(f"{container_name}: el contenedor no existe o no responde")
    candidato = candidato_de(info)
    if not candidato.es_sitio:
        raise SyncError(f"{container_name}: no parece un sitio ({candidato.por_que})")


def _audit(db: Session, *, tenant_id: str, action: str, entity_id: str, details: dict) -> None:
    record_audit(
        db,
        tenant_id=tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="hosting",
        action=action,
        entity_type="hosting_site",
        entity_id=entity_id,
        result="success",
        correlation_id=None,
        request_id=None,
        details=details,
    )


def _sync_one(
    db: Session,
    *,
    container_name: str,
    domain_value: str,
    kind: str | None,
    assign: dict[str, str],
    apply: bool,
    allow_outside: bool,
) -> str:
    domain = normalize_domain(domain_value)
    site = _site_by_container(db, container_name)
    changes: list[str] = []

    if site is not None:
        if site.domain != domain:
            _check_domain(
                db,
                domain,
                site_id=site.id,
                tenant=_tenant_by_slug_by_id(db, site.tenant_id),
                allow_outside=allow_outside,
            )
            changes.append(f"dominio {site.domain or '(sin dominio)'} -> {domain}")
        if kind and site.kind != kind:
            changes.append(f"tipo {site.kind} -> {kind}")
        if not changes:
            return f"sin cambios: {container_name} ({domain}, {site.kind})"
        if apply:
            previous = site.domain
            site.domain = domain
            if kind:
                site.kind = kind
            _audit(
                db,
                tenant_id=site.tenant_id,
                action="hosting.site.domain_set",
                entity_id=site.id,
                details={
                    "container_name": container_name,
                    "domain": domain,
                    "previous_domain": previous,
                    "kind": kind or site.kind,
                    "source": "hosting_sync",
                },
            )
            db.commit()
    else:
        tenant = _resolve_tenant(db, domain, assign.get(container_name))
        _check_domain(
            db, domain, site_id=None, tenant=tenant, allow_outside=allow_outside
        )
        _assert_site_container(container_name)
        changes.append(f"adoptar en tenant {tenant.slug} con dominio {domain}")
        if kind:
            changes.append(f"tipo {kind}")
        if apply:
            site = HostingSite(
                id=new_uuid(),
                tenant_id=tenant.id,
                container_name=container_name,
                domain=domain,
                kind=kind or "wordpress",
            )
            db.add(site)
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise SyncError(
                    f"{container_name}: no se pudo adoptar (dominio o contenedor duplicado)"
                ) from exc
            _audit(
                db,
                tenant_id=tenant.id,
                action="hosting.site.adopted",
                entity_id=site.id,
                details={
                    "container_name": container_name,
                    "domain": domain,
                    "kind": site.kind,
                    "source": "hosting_sync",
                },
            )
            db.commit()

    prefix = "aplicado" if apply else "dry-run"
    return f"{prefix}: {container_name}: " + "; ".join(changes)


def _tenant_by_slug_by_id(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise SyncError(f"el tenant {tenant_id} no existe")
    return tenant


def _retire_old(db: Session, *, apply: bool) -> list[str]:
    lines: list[str] = []
    for old, replacement in RETIRE.items():
        old_row = _site_by_container(db, old)
        if old_row is None:
            continue
        if _site_by_container(db, replacement) is None:
            lines.append(f"no retiro {old}: {replacement} todavía no está adoptado")
            continue
        if not apply:
            lines.append(f"dry-run: retiraría la fila {old} (contenedor intacto)")
            continue
        _audit(
            db,
            tenant_id=old_row.tenant_id,
            action="hosting.site.released",
            entity_id=old_row.id,
            details={
                "container_name": old,
                "replaced_by": replacement,
                "source": "hosting_sync",
            },
        )
        db.delete(old_row)
        db.commit()
        lines.append(f"retirada la fila {old}; el contenedor sigue intacto")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="CONTENEDOR",
        help="procesar solo estos contenedores (repetible)",
    )
    parser.add_argument(
        "--assign",
        action="append",
        default=[],
        metavar="CONTENEDOR=SLUG",
        help="forzar el tenant de un contenedor (repetible)",
    )
    parser.add_argument(
        "--allow-outside",
        action="store_true",
        help="permitir dominios fuera del sufijo del tenant",
    )
    parser.add_argument(
        "--retire-old",
        action="store_true",
        help="borrar filas viejas cuando el contenedor bueno ya está adoptado",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escribir cambios (sin esto es dry-run)",
    )
    args = parser.parse_args(argv)

    assign: dict[str, str] = {}
    for item in args.assign:
        if "=" not in item:
            parser.error("--assign espera CONTENEDOR=SLUG")
        container, slug = item.split("=", 1)
        assign[container.strip()] = slug.strip()

    selected = [
        (container, *MANIFEST[container])
        for container in MANIFEST
        if not args.only or container in args.only
    ]
    if not selected:
        print("no hay nada para procesar con --only")
        return 2

    settings = Settings(**env_settings_kwargs())
    factory = build_session_factory(settings)
    failures = 0
    with factory() as db:
        for container, domain, kind in selected:
            try:
                print(
                    _sync_one(
                        db,
                        container_name=container,
                        domain_value=domain,
                        kind=kind,
                        assign=assign,
                        apply=args.apply,
                        allow_outside=args.allow_outside,
                    )
                )
            except (SyncError, SetDomainError) as exc:
                db.rollback()
                failures += 1
                print(f"FALLO: {exc}")

        if args.retire_old:
            for line in _retire_old(db, apply=args.apply):
                print(line)

    if not args.apply:
        print("\ndry-run: no se escribió nada. Repetí con --apply para confirmar.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
