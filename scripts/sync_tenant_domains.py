#!/usr/bin/env python3
"""Synchronize `tenant_domains` with the primary domain of each tenant.

The primary mail domain lives in `tenants.domain`; `tenant_domains` holds only
additional domains. This operator script removes redundant rows that duplicate
a tenant's own primary and reports rows that collide with another tenant's
primary. It never inserts primary rows, never deletes users, sites or audit
rows, and never touches Docker, the proxy or certificates.

Dry-run by default; it always writes a JSON report.

Usage:
    python3 scripts/sync_tenant_domains.py
    python3 scripts/sync_tenant_domains.py --apply

Exit codes: 0 ok, 1 conflicts or error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.tenant.backend.models import (  # pyright: ignore[reportMissingImports]
    TenantDomain,
)
from systutor.contracts.domain_namespace import normalize_domain_name
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant

DEFAULT_REPORT_DIR = REPO_ROOT / "logs"


class SyncError(Exception):
    """A failure explained to the operator, not a traceback."""


@dataclass
class ChangeSet:
    remove: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changes(self) -> int:
        return len(self.remove)

    def as_dict(self) -> dict[str, Any]:
        return {
            "remove": self.remove,
            "conflicts": self.conflicts,
            "invalid": self.invalid,
            "counts": {
                "remove": len(self.remove),
                "conflicts": len(self.conflicts),
                "invalid": len(self.invalid),
            },
        }


def _normalize(value: str | None) -> str | None:
    try:
        return normalize_domain_name(value)
    except ValueError:
        return None


def _plan(db: Session) -> ChangeSet:
    changes = ChangeSet()
    tenants = list(db.scalars(select(Tenant).order_by(Tenant.slug)))
    primaries: dict[str, Tenant] = {}
    normalized: dict[str, str] = {}

    for tenant in tenants:
        domain = _normalize(tenant.domain)
        if tenant.domain and domain is None:
            changes.invalid.append(
                {
                    "tenant_id": tenant.id,
                    "slug": tenant.slug,
                    "domain": tenant.domain,
                }
            )
            continue
        if domain is not None:
            normalized[tenant.id] = domain
            primaries.setdefault(domain, tenant)

    for tenant in tenants:
        primary = normalized.get(tenant.id)
        rows = list(
            db.scalars(
                select(TenantDomain)
                .where(TenantDomain.tenant_id == tenant.id)
                .order_by(TenantDomain.domain)
            )
        )
        for row in rows:
            if primary is not None and row.domain == primary:
                changes.remove.append(
                    {
                        "domain": row.domain,
                        "tenant_id": tenant.id,
                        "slug": tenant.slug,
                    }
                )
                continue
            owner = primaries.get(row.domain)
            if owner is not None and owner.id != tenant.id:
                changes.conflicts.append(
                    {
                        "domain": row.domain,
                        "tenant_id": tenant.id,
                        "slug": tenant.slug,
                        "primary_tenant_id": owner.id,
                        "primary_tenant_slug": owner.slug,
                    }
                )
    return changes


def _audit(
    db: Session,
    *,
    tenant_id: str,
    action: str,
    domain: str,
    details: dict[str, Any],
) -> None:
    record_audit(
        db,
        tenant_id=tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="tenant",
        action=action,
        entity_type="tenant_domain",
        entity_id=domain,
        result="success",
        correlation_id=None,
        request_id=None,
        details=details,
    )


def _apply(db: Session, changes: ChangeSet) -> None:
    for item in changes.remove:
        row = db.get(TenantDomain, item["domain"])
        if row is None or row.tenant_id != item["tenant_id"]:
            continue
        db.delete(row)
        _audit(
            db,
            tenant_id=item["tenant_id"],
            action="tenant.sync.primary_row_removed",
            domain=item["domain"],
            details={"reason": "domain is the tenant primary"},
        )
        db.flush()
    db.commit()


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _default_report_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return DEFAULT_REPORT_DIR / f"sync-tenant-domains-{stamp}.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="escribir cambios; sin esto es dry-run")
    parser.add_argument("--report", help="ruta del informe JSON")
    return parser


def _run(db: Session, args: argparse.Namespace) -> int:
    changes = _plan(db)
    report_path = Path(args.report) if args.report else _default_report_path()
    payload = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "applied": bool(args.apply),
        **changes.as_dict(),
    }
    _write_report(report_path, payload)
    print(f"informe: {report_path}")

    if changes.invalid:
        for item in changes.invalid:
            print(f"dominio inválido en {item['slug']}: {item['domain']!r} (omitido)")

    if not args.apply:
        print(
            f"dry-run: {changes.changes} filas redundantes a eliminar, "
            f"{len(changes.conflicts)} conflictos, {len(changes.invalid)} inválidos"
        )
        return 1 if changes.conflicts else 0

    if changes.conflicts:
        for item in changes.conflicts:
            print(
                f"CONFLICTO: {item['domain']} está en tenant_domains de "
                f"{item['slug']} pero es el primario de {item['primary_tenant_slug']}"
            )
        print("no se escribe nada hasta resolver los conflictos")
        db.rollback()
        return 1

    if changes.remove:
        _apply(db, changes)
    print(f"aplicado: {changes.changes} filas eliminadas")
    return 0


def run(
    argv: list[str] | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)

    factory = session_factory
    if factory is None:
        settings = Settings(**env_settings_kwargs())
        factory = build_session_factory(settings)

    with factory() as db:
        try:
            return _run(db, args)
        except SyncError as exc:
            print(f"FALLO: {exc}")
            db.rollback()
            return 1


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
