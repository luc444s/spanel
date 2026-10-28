#!/usr/bin/env python3
"""Consolidate several tenants into one, preserving audit and containers.

The ownership of a domain is explicit (`tenant_domains`). This operator script
claims domains, moves hosting sites and users to the target tenant, deactivates
the origin tenants and writes a JSON plan that `--rollback` replays exactly.

It never runs Docker, rewrites proxy YAML, restarts containers or issues
certificates. It never deletes users or audit rows.

Usage:
    python3 scripts/unify_tenants.py --target-slug demo \\
        --domain spanel.site --domain debug.com --domain test2.com \\
        --all-others --apply

    python3 scripts/unify_tenants.py --rollback logs/unify-tenants-20260101-120000.json --apply
    python3 scripts/unify_tenants.py --target-slug demo --from-slug viejo --apply --purge

Exit codes: 0 ok, 1 error or purge blocked, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.models import (
    HostingSite,  # pyright: ignore[reportMissingImports]
)
from plugins.tenant.backend.models import (  # pyright: ignore[reportMissingImports]
    TenantDomain,
    TenantSiteSettings,
)
from systutor.contracts.domain_namespace import normalize_domain_name
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.models import Tenant

DEFAULT_PLAN_DIR = REPO_ROOT / "logs"
DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("users", "users"),
    ("hosting_sites", "hosting_sites"),
    ("branches", "branches"),
    ("user_context_claims", "user_context_claims"),
    ("documents", "core_document_versions"),
    ("signatures", "core_signature_sessions"),
    ("tenant_permissions", "tenant_permissions"),
    ("events", "event_logs"),
    ("audit_logs", "audit_logs"),
    ("tenant_domains", "tenant_domains"),
)


class UnifyError(Exception):
    """A failure explained to the operator, not a traceback."""


@dataclass
class ChangeSet:
    domains: list[dict[str, Any]]
    sites: list[dict[str, Any]]
    users: list[dict[str, Any]]
    deactivated: list[dict[str, Any]]
    primaries: list[dict[str, Any]]

    def __len__(self) -> int:
        return (
            len(self.domains)
            + len(self.sites)
            + len(self.users)
            + len(self.deactivated)
            + len(self.primaries)
        )

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "domains": self.domains,
            "sites": self.sites,
            "users": self.users,
            "deactivated_tenants": self.deactivated,
            "cleared_primaries": self.primaries,
        }


def _slug(db: Session, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise UnifyError(f"no existe el tenant con slug {slug!r}")
    return tenant


def _normalize_all(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        try:
            domain = normalize_domain_name(value)
        except ValueError as exc:
            raise UnifyError(f"dominio inválido {value!r}: {exc}") from exc
        if domain not in normalized:
            normalized.append(domain)
    return normalized


def _normalize_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_domain_name(value)
    except ValueError:
        return None


def _domain_row(db: Session, domain: str) -> TenantDomain | None:
    return db.get(TenantDomain, domain)


def _audit(db: Session, *, tenant_id: str, action: str, entity_type: str, entity_id: str, details: dict[str, Any]) -> None:
    record_audit(
        db,
        tenant_id=tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="tenant",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        result="success",
        correlation_id=None,
        request_id=None,
        details=details,
    )


def _resolve(args: argparse.Namespace, db: Session) -> tuple[Tenant, list[Tenant]]:
    target = _slug(db, args.target_slug)
    if args.all_others:
        origins = list(
            db.scalars(
                select(Tenant).where(Tenant.id != target.id).order_by(Tenant.slug)
            )
        )
    else:
        origins = [_slug(db, slug) for slug in dict.fromkeys(args.from_slug or [])]
        if target in origins:
            origins = [tenant for tenant in origins if tenant.id != target.id]
    if not origins and not args.domain:
        raise UnifyError("no hay tenants origen ni dominios que consolidar")
    for origin in origins:
        if origin.id == target.id:
            raise UnifyError("el target no puede ser también un origen")
    return target, origins


def _plan_changes(
    db: Session,
    *,
    target: Tenant,
    origins: list[Tenant],
    domains: list[str],
    move_all_sites: bool,
) -> ChangeSet:
    origin_ids = {tenant.id for tenant in origins}
    changes = ChangeSet(domains=[], sites=[], users=[], deactivated=[], primaries=[])
    target_primary = _normalize_or_none(target.domain)

    for origin in origins:
        sites = list(
            db.scalars(
                select(HostingSite)
                .where(HostingSite.tenant_id == origin.id)
                .order_by(HostingSite.container_name)
            )
        )
        for site in sites:
            if not move_all_sites and site.domain not in domains:
                continue
            changes.sites.append(
                {
                    "site_id": site.id,
                    "container_name": site.container_name,
                    "domain": site.domain,
                    "from_tenant_id": origin.id,
                    "to_tenant_id": target.id,
                }
            )
            if site.domain and site.domain not in domains:
                try:
                    domains.append(normalize_domain_name(site.domain))
                except ValueError:
                    pass

    for domain in domains:
        row = _domain_row(db, domain)
        if row is None:
            if domain == target_primary:
                # El primario del target no es un secundario: no se inserta.
                continue
            changes.domains.append(
                {"domain": domain, "action": "created", "previous_owner": None}
            )
        elif row.tenant_id == target.id:
            continue
        elif row.tenant_id not in origin_ids:
            raise UnifyError(
                f"el dominio {domain} pertenece a un tenant que no es origen "
                f"({row.tenant_id}); no se escribe nada"
            )
        else:
            changes.domains.append(
                {
                    "domain": domain,
                    "action": "moved",
                    "previous_owner": row.tenant_id,
                }
            )

    for origin in origins:
        primary = _normalize_or_none(origin.domain)
        if primary is not None and primary in domains:
            changes.primaries.append(
                {
                    "tenant_id": origin.id,
                    "slug": origin.slug,
                    "domain": primary,
                }
            )

    target_emails = set(
        db.scalars(select(User.email).where(User.tenant_id == target.id))
    )
    for origin in origins:
        users = list(
            db.scalars(
                select(User).where(User.tenant_id == origin.id).order_by(User.email)
            )
        )
        for user in users:
            if user.email in target_emails:
                if user.is_active:
                    changes.users.append(
                        {
                            "user_id": user.id,
                            "email": user.email,
                            "action": "deactivated",
                            "from_tenant_id": origin.id,
                        }
                    )
            else:
                target_emails.add(user.email)
                changes.users.append(
                    {
                        "user_id": user.id,
                        "email": user.email,
                        "action": "moved",
                        "from_tenant_id": origin.id,
                    }
                )
        if origin.is_active:
            changes.deactivated.append(
                {"tenant_id": origin.id, "slug": origin.slug}
            )
    return changes


def _apply_changes(
    db: Session, *, target: Tenant, changes: ChangeSet
) -> None:
    for item in changes.domains:
        row = _domain_row(db, item["domain"])
        if item["action"] == "created":
            if row is None:
                db.add(TenantDomain(domain=item["domain"], tenant_id=target.id))
        elif row is not None:
            row.tenant_id = target.id
        _audit(
            db,
            tenant_id=target.id,
            action="tenant.unify.domain",
            entity_type="tenant_domain",
            entity_id=item["domain"],
            details={"previous_owner": item["previous_owner"]},
        )

    for item in changes.primaries:
        tenant = db.get(Tenant, item["tenant_id"])
        if tenant is not None and _normalize_or_none(tenant.domain) == item["domain"]:
            tenant.domain = None
            _audit(
                db,
                tenant_id=target.id,
                action="tenant.unify.primary_cleared",
                entity_type="tenant",
                entity_id=tenant.id,
                details={"slug": item["slug"], "domain": item["domain"]},
            )

    for item in changes.sites:
        site = db.get(HostingSite, item["site_id"])
        if site is not None:
            site.tenant_id = target.id
            _audit(
                db,
                tenant_id=target.id,
                action="tenant.unify.site",
                entity_type="hosting_site",
                entity_id=site.id,
                details={
                    "container_name": site.container_name,
                    "from_tenant_id": item["from_tenant_id"],
                },
            )

    for item in changes.users:
        user = db.get(User, item["user_id"])
        if user is None:
            continue
        if item["action"] == "moved":
            user.tenant_id = target.id
        else:
            user.is_active = False
        _audit(
            db,
            tenant_id=target.id,
            action="tenant.unify.user",
            entity_type="user",
            entity_id=user.id,
            details={
                "email": user.email,
                "action": item["action"],
                "from_tenant_id": item["from_tenant_id"],
            },
        )

    for item in changes.deactivated:
        tenant = db.get(Tenant, item["tenant_id"])
        if tenant is not None:
            tenant.is_active = False
            _audit(
                db,
                tenant_id=target.id,
                action="tenant.unify.deactivate",
                entity_type="tenant",
                entity_id=tenant.id,
                details={"slug": tenant.slug},
            )
    db.commit()


def _dependency_counts(db: Session, tenant_id: str) -> dict[str, int]:
    bind = db.get_bind()
    inspector = inspect(bind)
    counts: dict[str, int] = {}
    for label, table_name in DEPENDENCIES:
        if not inspector.has_table(table_name):
            continue
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "tenant_id" not in columns:
            continue
        table = _table(bind, table_name)
        count = db.execute(
            select(func.count())
            .select_from(table)
            .where(table.c.tenant_id == tenant_id)
        ).scalar()
        if count:
            counts[label] = int(count)
    return counts


def _table(bind, table_name: str):
    from sqlalchemy import MetaData, Table

    return Table(table_name, MetaData(), autoload_with=bind)


def _blocked_counts(db: Session, origins: list[Tenant]) -> dict[str, dict[str, int]]:
    blocked: dict[str, dict[str, int]] = {}
    for origin in origins:
        counts = _dependency_counts(db, origin.id)
        if counts:
            blocked[origin.slug] = counts
    return blocked


def _purge(db: Session, *, origins: list[Tenant], apply: bool) -> int:
    blocked = _blocked_counts(db, origins)
    if blocked:
        for slug, counts in blocked.items():
            detail = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
            print(f"purge bloqueado para {slug}: {detail}")
        return 1
    if not apply:
        print(f"dry-run: se borrarían {len(origins)} tenants sin dependencias")
        return 0
    for origin in origins:
        db.execute(
            delete(TenantSiteSettings).where(TenantSiteSettings.tenant_id == origin.id)
        )
        db.execute(delete(TenantDomain).where(TenantDomain.tenant_id == origin.id))
        db.delete(origin)
    db.commit()
    print(f"purgados {len(origins)} tenants sin dependencias")
    return 0


def _write_plan(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _default_plan_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return DEFAULT_PLAN_DIR / f"unify-tenants-{stamp}.json"


def _rollback(db: Session, payload: dict[str, Any]) -> int:
    if not payload.get("applied"):
        raise UnifyError("el plan no se aplicó; no hay nada que revertir")
    reversed_changes = 0
    for item in payload.get("users", []):
        user = db.get(User, item["user_id"])
        if user is None:
            continue
        if item["action"] == "moved" and user.tenant_id != item["from_tenant_id"]:
            user.tenant_id = item["from_tenant_id"]
            reversed_changes += 1
        elif item["action"] == "deactivated" and not user.is_active:
            user.is_active = True
            reversed_changes += 1
    for item in payload.get("sites", []):
        site = db.get(HostingSite, item["site_id"])
        if site is not None and site.tenant_id != item["from_tenant_id"]:
            site.tenant_id = item["from_tenant_id"]
            reversed_changes += 1
    for item in payload.get("domains", []):
        row = _domain_row(db, item["domain"])
        if item["action"] == "created":
            if row is not None and row.tenant_id == payload["target"]["id"]:
                db.delete(row)
                reversed_changes += 1
        elif row is not None and row.tenant_id == payload["target"]["id"]:
            row.tenant_id = item["previous_owner"]
            reversed_changes += 1
    for item in payload.get("cleared_primaries", []):
        tenant = db.get(Tenant, item["tenant_id"])
        if tenant is not None and _normalize_or_none(tenant.domain) != item["domain"]:
            tenant.domain = item["domain"]
            reversed_changes += 1
    for item in payload.get("deactivated_tenants", []):
        tenant = db.get(Tenant, item["tenant_id"])
        if tenant is not None and not tenant.is_active:
            tenant.is_active = True
            reversed_changes += 1
    db.commit()
    print(f"rollback aplicado: {reversed_changes} cambios revertidos")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target-slug", help="tenant que recibe todo")
    parser.add_argument("--domain", action="append", default=[], help="dominio a reclamar (repetible)")
    parser.add_argument("--from-slug", action="append", default=[], help="tenant origen (repetible)")
    parser.add_argument("--all-others", action="store_true", help="todos los demás tenants como origen")
    parser.add_argument("--move-all-sites", action="store_true", help="mover todos los sitios de los orígenes")
    parser.add_argument("--apply", action="store_true", help="escribir cambios; sin esto es dry-run")
    parser.add_argument("--plan", help="ruta del plan JSON")
    parser.add_argument("--rollback", help="revierte el plan JSON indicado")
    parser.add_argument("--purge", action="store_true", help="borrar orígenes sin dependencias")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    if not args.rollback and not args.target_slug:
        print("FALLO: --target-slug es obligatorio")
        return 1

    factory = session_factory
    if factory is None:
        settings = Settings(**env_settings_kwargs())
        factory = build_session_factory(settings)

    with factory() as db:
        try:
            if args.rollback:
                path = Path(args.rollback)
                if not path.exists():
                    raise UnifyError(f"no existe el plan {path}")
                return _rollback(db, json.loads(path.read_text(encoding="utf-8")))
            return _run_unify(db, args, apply=args.apply)
        except (UnifyError, ValueError) as exc:
            print(f"FALLO: {exc}")
            db.rollback()
            return 1


def _run_unify(db: Session, args: argparse.Namespace, *, apply: bool) -> int:
    target, origins = _resolve(args, db)
    domains = _normalize_all(list(args.domain))
    changes = _plan_changes(
        db,
        target=target,
        origins=origins,
        domains=domains,
        move_all_sites=args.move_all_sites,
    )
    plan_path = Path(args.plan) if args.plan else _default_plan_path()
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "applied": bool(apply),
        "target": {"id": target.id, "slug": target.slug},
        "origins": [{"id": tenant.id, "slug": tenant.slug} for tenant in origins],
        **changes.as_dict(),
    }
    _write_plan(plan_path, payload)
    print(f"plan: {plan_path}")

    if not apply:
        print(
            f"dry-run: {len(changes)} cambios previstos "
            f"(dominios={len(changes.domains)}, sitios={len(changes.sites)}, "
            f"usuarios={len(changes.users)}, bajas={len(changes.deactivated)})"
        )
        if args.purge:
            return _purge(db, origins=origins, apply=False)
        return 0

    if args.purge:
        blocked = _blocked_counts(db, origins)
        if blocked:
            for slug, counts in blocked.items():
                detail = ", ".join(
                    f"{name}={count}" for name, count in sorted(counts.items())
                )
                print(f"purge bloqueado para {slug}: {detail}")
            return 1

    if changes:
        _apply_changes(db, target=target, changes=changes)
    print(f"aplicado: {len(changes)} cambios")
    if args.purge:
        return _purge(db, origins=origins, apply=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
