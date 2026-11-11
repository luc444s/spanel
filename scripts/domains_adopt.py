#!/usr/bin/env python3
"""Adopt an already-published Traefik domain (plan by default; never edits YAML)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import or_, select

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import systutor.kernel.models  # noqa: F401
from domains_sync import build_operations, build_registry
from plugins.domains.backend.sync import apply_sync, prepare_sync
from systutor.contracts.domain_namespace import claim_domain, domain_owner
from systutor.core.config import get_settings
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant


class AdoptionError(Exception):
    pass


def adopt_domain(
    db,
    app,
    operations,
    *,
    domain: str,
    tenant_value: str,
    site_id: str | None = None,
    apply: bool = False,
) -> dict[str, object]:
    needle = domain.strip().lower().removesuffix(".")
    tenant = db.scalar(
        select(Tenant).where(or_(Tenant.id == tenant_value, Tenant.slug == tenant_value))
    )
    if tenant is None:
        raise AdoptionError(f"unknown tenant {tenant_value!r}")
    overrides: dict[str, str] = {}
    initial = prepare_sync(db, app, operations)
    matches = [row for row in initial.observations if row.domain == needle]
    if site_id:
        containers = {row.container for row in matches}
        if not containers:
            # Ambiguous containers are withheld from observations; identify the
            # requested site through a second preparation override per orphan.
            from systutor.contracts.domain_ownership import list_sites

            selected = [
                site
                for site in list_sites(app, db, None)  # type: ignore[arg-type]
                if site.site_id == site_id
            ]
            if len(selected) != 1:
                raise AdoptionError(f"unknown or ambiguous site {site_id!r}")
            overrides[selected[0].container] = site_id
        else:
            overrides.update({container: site_id for container in containers})
        initial = prepare_sync(db, app, operations, site_overrides=overrides)
        matches = [row for row in initial.observations if row.domain == needle]
    if len(matches) != 1:
        raise AdoptionError(f"{needle}: not published once in a coherent managed YAML")
    observed = matches[0]
    if observed.tenant_id != tenant.id:
        raise AdoptionError(
            f"{needle}: site belongs to tenant {observed.tenant_id}, not {tenant.id}"
        )
    if site_id and observed.site_id != site_id:
        raise AdoptionError(f"{needle}: published site does not match {site_id}")
    owner = domain_owner(app, db, needle)
    if owner is not None and owner != tenant.id:
        raise AdoptionError(f"{needle}: owned by another tenant")
    if not initial.ok:
        raise AdoptionError("the complete proxy snapshot contains conflicts, orphans or invalid YAML")
    result = {
        "domain": needle,
        "tenant_id": tenant.id,
        "tenant": tenant.slug,
        "site_id": observed.site_id,
        "container": observed.container,
        "already_owned": owner == tenant.id,
        "applied": apply,
    }
    if not apply:
        return result
    if owner is None:
        claim_domain(app, db, tenant.id, needle)
    apply_sync(db, initial)
    record_audit(
        db,
        tenant_id=tenant.id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="domains",
        action="domains.domain.adopted",
        entity_type="domain",
        entity_id=needle,
        result="success",
        correlation_id=None,
        request_id=None,
        details={"site_id": observed.site_id, "container": observed.container},
    )
    db.commit()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--tenant", required=True, help="tenant slug or id")
    parser.add_argument("--site", dest="site_id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        factory = build_session_factory(get_settings())
        with factory() as db:
            result = adopt_domain(
                db,
                build_registry(),
                build_operations(),
                domain=args.domain,
                tenant_value=args.tenant,
                site_id=args.site_id,
                apply=args.apply,
            )
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"domain: {result['domain']}")
            print(f"tenant: {result['tenant']}")
            print(f"site: {result['site_id']} ({result['container']})")
            print(f"mode: {'applied' if args.apply else 'plan'}")
        return 0
    except Exception as exc:  # noqa: BLE001 - operator-facing failure
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"domains_adopt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
