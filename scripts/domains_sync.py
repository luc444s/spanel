#!/usr/bin/env python3
"""Synchronize the read-only Domains observation from Traefik (plan by default)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.apps.backend.register import resolve_apps_sites
from plugins.domains.backend.proxy import ProxyOperations, build_runner
from plugins.domains.backend.settings import (
    get_domains_settings,
    register_domains_settings,
)
from plugins.domains.backend.sync import (
    SyncPlan,
    apply_sync,
    mark_sync_error,
    prepare_sync,
)
from plugins.hosting.backend.register import resolve_hosting_sites
from plugins.tenant.backend.register import (
    domain_claim_provider,
    domain_owner_provider,
    managed_domains_provider,
)
from systutor.contracts.domain_namespace import register_domain_providers
from systutor.contracts.domain_ownership import register_site_provider
from systutor.core.config import get_settings
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit


def build_registry() -> FastAPI:
    app = FastAPI()
    register_site_provider(app, "hosting", resolve_hosting_sites)
    register_site_provider(app, "apps", resolve_apps_sites)
    register_domain_providers(
        app,
        "tenant",
        list_domains=managed_domains_provider,
        owner=domain_owner_provider,
        claim=domain_claim_provider,
    )
    return app


def build_operations() -> ProxyOperations:
    register_domains_settings()
    settings = get_domains_settings().proxy_settings()
    return ProxyOperations(settings, build_runner(settings))


def audit_sync(db, plan: SyncPlan) -> None:
    record_audit(
        db,
        tenant_id=None,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="domains",
        action="domains.inventory.synced",
        entity_type="domain_inventory",
        entity_id=plan.generation,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "documents": plan.documents,
            "domains": len(plan.observations),
            "additions": len(plan.additions),
            "changes": len(plan.changes),
            "removals": len(plan.removals),
        },
    )


def render(plan: SyncPlan, *, applied: bool, json_output: bool) -> None:
    payload = plan.to_dict()
    payload["applied"] = applied
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return
    print("Domains observed inventory")
    print(f"mode: {'applied' if applied else 'plan'}")
    print(f"documents: {plan.documents}; domains: {len(plan.observations)}")
    for label, values in (
        ("add", plan.additions),
        ("change", plan.changes),
        ("remove", plan.removals),
        ("conflict", plan.conflicts),
        ("orphan", plan.orphans),
        ("invalid", plan.invalid),
    ):
        for value in values:
            print(f"{label}: {value}")


def run_sync(db, app: FastAPI, operations: ProxyOperations, *, apply: bool) -> SyncPlan:
    plan = prepare_sync(db, app, operations)
    if apply:
        if not plan.ok:
            mark_sync_error(db, "snapshot rejected: conflicts, orphans or invalid documents", plan)
            db.commit()
            return plan
        apply_sync(db, plan)
        audit_sync(db, plan)
        db.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="activate the prepared generation")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    try:
        register_domains_settings()
        factory = build_session_factory(get_settings())
        with factory() as db:
            try:
                plan = run_sync(db, build_registry(), build_operations(), apply=args.apply)
            except Exception as exc:
                db.rollback()
                if args.apply:
                    mark_sync_error(db, f"sync failed: {exc}")
                    db.commit()
                raise
        render(plan, applied=args.apply and plan.ok, json_output=args.json)
        return 0 if plan.ok else 1
    except Exception as exc:  # noqa: BLE001 - bounded operator message
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"domains_sync: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
