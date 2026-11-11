#!/usr/bin/env python3
"""Save an existing Apps deployment environment encrypted in the database.

Run inside the Spanel app container after updating to a version that supports
reusable tenant environments. The script reads the active release's ``app.env``
(or an explicit file), removes the credentials managed by Apps, encrypts the
rest with the Apps key and stores it on the application row.

It never prints values. Use ``--dry-run`` to validate without writing.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from plugins.apps.backend.crypto import TokenCipher
from plugins.apps.backend.managed_environment import MANAGED_DATABASE_NAMES
from plugins.apps.backend.models import Application, Deployment
from plugins.apps.backend.settings import AppsSettings
from plugins.apps.backend.tenant_environment import save_tenant_environment
from sqlalchemy import select
from systutor.core.config import get_settings
from systutor.core.database import build_session_factory

ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the flat dotenv format Compose receives, without logging values."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def tenant_entries(values: dict[str, str]) -> list[tuple[str, str]]:
    """Only the tenant-owned variables; managed credentials stay server-side."""
    return [
        (name, value)
        for name, value in values.items()
        if name not in MANAGED_DATABASE_NAMES
    ]


def release_env_path(deployment: Deployment, state_root: Path) -> Path:
    manifest = deployment.manifest or {}
    release_value = str(manifest.get("release") or "").strip()
    if not release_value:
        raise RuntimeError("The active deployment does not register a release")
    release = Path(release_value)
    candidates = [release / "app.env"]
    if release.parent.name == "releases":
        candidates.append(release.parent.parent / "private" / release.name / "app.env")
    candidates.append(state_root / "private" / release.name / "app.env")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "app.env was not found for the active release; use --env-file with a local copy"
    )


def find_application(db, application_id: str | None, slug: str | None) -> Application:
    stmt = select(Application).where(Application.deleted_at.is_(None))
    if application_id:
        stmt = stmt.where(Application.id == application_id)
    elif slug:
        stmt = stmt.where(Application.slug == slug)
    else:
        raise RuntimeError("Specify --application-id or --slug")
    rows = list(db.scalars(stmt).all())
    if not rows:
        raise RuntimeError("Application not found")
    if len(rows) > 1:
        names = ", ".join(f"{row.slug} ({row.id})" for row in rows)
        raise RuntimeError(f"There is more than one application with that slug: {names}")
    return rows[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--application-id", "--app-id", dest="application_id")
    identity.add_argument("--slug")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--key-version", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    try:
        apps = AppsSettings.from_env()
        cipher = TokenCipher.from_text(
            AppsSettings.read_secret(args.key_file or apps.encryption_key_file),
            args.key_version or apps.key_version,
        )
        settings = get_settings()
        factory = build_session_factory(settings)
        with factory() as db:
            app = find_application(db, args.application_id, args.slug)
            if app.has_saved_environment and not args.force:
                print(
                    f"{app.slug} already has stored variables; use --force to replace them"
                )
                return 2
            deployment = (
                db.get(Deployment, app.active_deployment_id)
                if app.active_deployment_id else None
            )
            if args.env_file is not None:
                source = args.env_file.expanduser()
            elif deployment is not None:
                source = release_env_path(deployment, apps.state_root)
            else:
                print("The application has no active deployment or --env-file")
                return 2
            if not source.is_file():
                print(f"The specified file does not exist: {source}")
                return 2
            entries = tenant_entries(parse_env_text(source.read_text(encoding="utf-8")))
            if not entries:
                print("No tenant variables were found to save")
                return 2
            names = ", ".join(name for name, _value in entries)
            if args.dry_run:
                print(f"{app.slug}: would save {len(entries)} variables: {names}")
                return 0
            save_tenant_environment(db, app, cipher, entries)
            db.commit()
            print(f"{app.slug}: {len(entries)} variables saved encrypted: {names}")
            return 0
    except Exception as exc:  # noqa: BLE001 - mensaje operativo sin secretos
        print(f"migrate_env: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
