#!/usr/bin/env python3
"""Re-render Domains routes for Apps applications without the UI or a deploy.

This is the autonomous equivalent of opening each service's Dominios tab and
saving the route again. It reuses the Domains renderer, ownership checks and
container reconnection, then optionally restarts the proxy so failed ACME
orders are retried immediately.

Run inside the Spanel app container.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence

from fastapi import FastAPI
from plugins.apps.backend.models import Application
from plugins.apps.backend.register import resolve_apps_sites
from plugins.domains.backend.service import refresh_application_routes
from plugins.domains.backend.settings import register_domains_settings
from sqlalchemy import select
from systutor.contracts.domain_ownership import register_site_provider
from systutor.core.config import get_settings
from systutor.core.database import build_session_factory


def select_applications(db, application_id: str | None, slug: str | None) -> list[Application]:
    stmt = select(Application).where(Application.deleted_at.is_(None))
    if application_id:
        stmt = stmt.where(Application.id == application_id)
    elif slug:
        stmt = stmt.where(Application.slug == slug)
    return list(db.scalars(stmt.order_by(Application.created_at)).all())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--application-id", "--app-id", dest="application_id")
    identity.add_argument("--slug")
    parser.add_argument("--restart-proxy", action="store_true")
    parser.add_argument("--proxy-container", default="traefik")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        register_domains_settings()
        fastapi_app = FastAPI()
        register_site_provider(fastapi_app, "apps", resolve_apps_sites)
        settings = get_settings()
        factory = build_session_factory(settings)
        with factory() as db:
            applications = select_applications(db, args.application_id, args.slug)
            if not applications:
                print("No applications match")
                return 2
            for application in applications:
                if args.dry_run:
                    print(f"{application.slug}: its routes would be refreshed")
                    continue
                refresh_application_routes(
                    db, fastapi_app, application.tenant_id, application.id
                )
                print(f"{application.slug}: routes refreshed")
        if args.restart_proxy and not args.dry_run:
            subprocess.run(
                ["docker", "restart", args.proxy_container],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            print(f"{args.proxy_container}: restarted to retry ACME")
        return 0
    except Exception as exc:  # noqa: BLE001 - mensaje operativo sin secretos
        print(f"refresh_routes: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
