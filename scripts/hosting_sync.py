#!/usr/bin/env python3
"""Reconcile every site running on the Docker host with Hosting, by discovery.

No hardcoded container list: the script reads `docker ps`, extracts the public
domains from the Traefik labels of each container, decides the kind, resolves
the owning tenant and adopts or backfills the `hosting_sites` row. The only
hardcoded part is `IGNORED`, for infrastructure that is not a site (panel,
proxy, databases, mail, phpMyAdmin and test containers).

Rules:

- `traefik.enable=false` is respected: the container is not published, so it is
  not adopted (its stale row can be removed with `--retire-old`).
- A container that already has a row is always processed, even if it matches
  `IGNORED`: that is how old rows get their missing domain.
- A container with several different domains is ambiguous (Hosting supports one
  primary). It is skipped unless you pass `--domain <container>=<domain>`.
- `www.` aliases are not stored: the primary is the bare domain.
- The tenant is resolved by `site_suffix`/`domain` of the tenants. Zero or
  several matches skip the item; use `--assign <container>=<slug>`.
- Every write is audited. Containers are never started, stopped or removed.

Usage:
    python3 scripts/hosting_sync.py                       # dry-run everything
    python3 scripts/hosting_sync.py --apply
    python3 scripts/hosting_sync.py --only systutor --only portafolio
    python3 scripts/hosting_sync.py --domain mail-landing=mail.systutor.com --apply
    python3 scripts/hosting_sync.py --assign ardiffx=ardiffx --kind ardiffx=static --apply
    python3 scripts/hosting_sync.py --apply --retire-old

Exit codes: 0 ok, 1 at least one item failed, 2 nothing to do.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
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
from plugins.hosting.backend.discovery import (  # pyright: ignore[reportMissingImports]
    parse_labels,
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
from plugins.hosting.backend.transport import (  # pyright: ignore[reportMissingImports]
    build_docker_argv,
    run,
)
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant, new_uuid

# Infrastructure that is never a site. A row that already exists still gets
# processed (that is how backfills work), the list only blocks new adoptions.
IGNORED = {
    "spanel-app-1",
    "spanel-postgres-1",
    "spanel-redis-1",
    "spanel-phpmyadmin",
    "traefik",
    "acme-webroot",
    "mailserver",
    "webmail",
    "whoami-mail",
    "mail-landing",
    "mariadb",
}

# Extra skip rules by suffix, applied only to containers without a row.
IGNORED_SUFFIXES = ("-db", "-postgres", "-redis", "-mariadb")

# Rows that should disappear once their replacement is adopted. The Docker
# containers stay untouched: only the DB row is released.
RETIRE: dict[str, str] = {
    "legalattorney": "legalattorney-clean",
}

_HOST_RE = re.compile(r"Host\s*\(\s*[`\"']([^`\"']+)[`\"']\s*\)", re.IGNORECASE)
_WP_IMAGES = ("wordpress", "wp-cli")


class SyncError(Exception):
    """A failure explained to the operator, not a traceback."""


@dataclass
class Detected:
    container: str
    domain: str
    kind: str | None
    row: HostingSite | None
    notes: list[str] = field(default_factory=list)


def _is_ignored(name: str) -> bool:
    if name in IGNORED:
        return True
    return name.endswith(IGNORED_SUFFIXES)


def _site_by_container(db: Session, container_name: str) -> HostingSite | None:
    return db.scalar(
        select(HostingSite).where(HostingSite.container_name == container_name)
    )


def _tenant_by_slug(db: Session, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise SyncError(f"no tenant exists with slug {slug!r}")
    return tenant


def _tenant_by_id(db: Session, tenant_id: str) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise SyncError(f"tenant {tenant_id} does not exist")
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
            f"{domain}: no tenant owns that domain or has a site_suffix that "
            "covers it; create the tenant or pass --assign <container>=<slug>"
        )
    names = ", ".join(sorted(t.slug for t in matches.values()))
    raise SyncError(
        f"{domain}: several tenants cover it ({names}); choose with "
        "--assign <container>=<slug>"
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
        raise SyncError(f"{domain}: already belongs to container {other.container_name}")

    extra_owner = _domain_is_extra(domain)
    if extra_owner is not None:
        raise SyncError(f"{domain}: already published as an extra by {extra_owner}")

    suffix = _tenant_suffix(db, tenant.id)
    if suffix and not _inside_suffix(domain, suffix):
        message = f"{domain}: outside the tenant suffix {suffix} of tenant {tenant.slug}"
        if not allow_outside:
            raise SyncError(message + " (use --allow-outside to force it)")
        print(f"warning: {message}")


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


def _docker_containers() -> list[dict]:
    env = docker_env()
    result = run(
        build_docker_argv("docker ps --format '{{json .}}'", env=env),
        env=env,
        timeout=90,
    )
    if result.returncode != 0:
        raise SyncError(
            f"docker ps failed: {result.stderr.strip() or f'exit {result.returncode}'}"
        )
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _container_name(payload: dict) -> str:
    names = str(payload.get("Names") or "")
    return names.split(",")[0].strip()


def _hosts_from_labels(labels: dict[str, str]) -> list[str]:
    """Every `Host(...)` in every Traefik router rule, normalized and deduped."""
    found: list[str] = []
    for key, value in labels.items():
        if not key.startswith("traefik.http.routers.") or not key.endswith(".rule"):
            continue
        for match in _HOST_RE.findall(str(value)):
            host = match.strip().lower().removesuffix(".")
            if host and host not in found:
                found.append(host)
    return found


def _primary(hosts: list[str]) -> tuple[str | None, list[str]]:
    """Pick one primary domain: the bare host, not its www alias."""
    bare: list[str] = []
    for host in hosts:
        base = host.removeprefix("www.")
        if base not in bare:
            bare.append(base)
    if len(bare) <= 1:
        return (bare[0] if bare else None), []
    return None, bare


def _kind_for(container_name: str, image: str, labels: dict[str, str]) -> str:
    """WordPress needs a real signal; everything else is treated as static."""
    if any(token in image.lower() for token in _WP_IMAGES):
        return "wordpress"
    if "spanel.site" in labels:
        return "wordpress"
    info = inspect_container(docker_env(), container_name, timeout=60)
    if info is None:
        raise SyncError(f"{container_name}: could not inspect it to determine the kind")
    candidato = candidato_de(info)
    return "wordpress" if candidato.senal in {"etiqueta", "entorno"} else "static"


def _detect(
    db: Session,
    *,
    only: set[str],
    ignore: set[str],
    domain_overrides: dict[str, str],
    kind_overrides: dict[str, str],
) -> tuple[list[Detected], list[str]]:
    detected: list[Detected] = []
    notes: list[str] = []

    for payload in _docker_containers():
        name = _container_name(payload)
        if not name:
            continue
        if only and name not in only:
            continue
        row = _site_by_container(db, name)
        if name in ignore:
            notes.append(f"{name}: ignored by --ignore")
            continue
        if row is None and _is_ignored(name):
            notes.append(f"{name}: infraestructura ignorada")
            continue

        labels = parse_labels(payload.get("Labels"))
        if labels.get("traefik.enable", "").strip().lower() == "false":
            notes.append(f"{name}: traefik.enable=false, not published")
            continue

        hosts = _hosts_from_labels(labels)
        override = domain_overrides.get(name)
        if override:
            domain = normalize_domain(override)
            if domain not in hosts and hosts:
                notes.append(
                    f"{name}: --domain {domain} is not in the labels ({', '.join(hosts)})"
                )
        else:
            domain, ambiguous = _primary(hosts)
            if domain is None:
                if ambiguous:
                    notes.append(
                        f"{name}: sirve varios dominios ({', '.join(ambiguous)}); "
                        f"choose one with --domain {name}=<domain>"
                    )
                elif row is not None:
                    notes.append(f"{name}: no Host in labels; pass --domain {name}=<domain>")
                else:
                    notes.append(f"{name}: no Host in labels, not adopting")
                continue

        kind = kind_overrides.get(name)
        if kind is None and row is None:
            kind = _kind_for(name, str(payload.get("Image") or ""), labels)

        detected.append(Detected(container=name, domain=domain, kind=kind, row=row))

    return detected, notes


def _sync_one(
    db: Session,
    item: Detected,
    *,
    assign: dict[str, str],
    apply: bool,
    allow_outside: bool,
) -> str:
    site = item.row
    changes: list[str] = []

    if site is not None:
        if site.domain != item.domain:
            _check_domain(
                db,
                item.domain,
                site_id=site.id,
                tenant=_tenant_by_id(db, site.tenant_id),
                allow_outside=allow_outside,
            )
            changes.append(
                f"domain {site.domain or '(no domain)'} -> {item.domain}"
            )
        if item.kind and site.kind != item.kind:
            changes.append(f"type {site.kind} -> {item.kind}")
        if not changes:
            return f"no changes: {item.container} ({item.domain}, {site.kind})"
        if apply:
            previous = site.domain
            site.domain = item.domain
            if item.kind:
                site.kind = item.kind
            _audit(
                db,
                tenant_id=site.tenant_id,
                action="hosting.site.domain_set",
                entity_id=site.id,
                details={
                    "container_name": item.container,
                    "domain": item.domain,
                    "previous_domain": previous,
                    "kind": site.kind,
                    "source": "hosting_sync",
                },
            )
            db.commit()
    else:
        tenant = _resolve_tenant(db, item.domain, assign.get(item.container))
        _check_domain(
            db,
            item.domain,
            site_id=None,
            tenant=tenant,
            allow_outside=allow_outside,
        )
        changes.append(f"adopt into tenant {tenant.slug} with domain {item.domain}")
        if item.kind:
            changes.append(f"type {item.kind}")
        if apply:
            site = HostingSite(
                id=new_uuid(),
                tenant_id=tenant.id,
                container_name=item.container,
                domain=item.domain,
                kind=item.kind or "static",
            )
            db.add(site)
            try:
                db.flush()
            except IntegrityError as exc:
                db.rollback()
                raise SyncError(
                    f"{item.container}: could not be adopted (duplicate domain or container)"
                ) from exc
            _audit(
                db,
                tenant_id=tenant.id,
                action="hosting.site.adopted",
                entity_id=site.id,
                details={
                    "container_name": item.container,
                    "domain": item.domain,
                    "kind": site.kind,
                    "source": "hosting_sync",
                },
            )
            db.commit()

    prefix = "aplicado" if apply else "dry-run"
    return f"{prefix}: {item.container}: " + "; ".join(changes)


def _retire_old(db: Session, *, apply: bool) -> list[str]:
    lines: list[str] = []
    for old, replacement in RETIRE.items():
        old_row = _site_by_container(db, old)
        if old_row is None:
            continue
        if _site_by_container(db, replacement) is None:
            lines.append(f"not retiring {old}: {replacement} is not yet adopted")
            continue
        if not apply:
            lines.append(f"dry-run: would retire row {old} (container untouched)")
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
        lines.append(f"removed row {old}; the container is untouched")
    return lines


def _pairs(values: list[str], flag: str, parser: argparse.ArgumentParser) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            parser.error(f"{flag} espera CONTENEDOR=VALOR")
        container, value = item.split("=", 1)
        pairs[container.strip()] = value.strip()
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--only", action="append", default=[], metavar="CONTAINER")
    parser.add_argument(
        "--ignore", action="append", default=[], metavar="CONTAINER",
        help="do not adopt this container (repeatable)",
    )
    parser.add_argument(
        "--assign", action="append", default=[], metavar="CONTAINER=SLUG",
        help="force a container's tenant (repeatable)",
    )
    parser.add_argument(
        "--domain", action="append", default=[], metavar="CONTAINER=DOMAIN",
        help="force the primary domain (useful for containers with several)",
    )
    parser.add_argument(
        "--kind", action="append", default=[], metavar="CONTAINER=KIND",
        help="force the kind: wordpress or static (repeatable)",
    )
    parser.add_argument(
        "--allow-outside", action="store_true",
        help="allow domains outside the tenant suffix",
    )
    parser.add_argument(
        "--retire-old", action="store_true",
        help="delete old rows when the good container is already adopted",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write changes (without this it is a dry-run)",
    )
    args = parser.parse_args(argv)

    kind_overrides = _pairs(args.kind, "--kind", parser)
    for container, kind in kind_overrides.items():
        if kind not in {"wordpress", "static"}:
            parser.error(f"--kind {container}={kind}: use wordpress or static")

    assign = _pairs(args.assign, "--assign", parser)
    domains = _pairs(args.domain, "--domain", parser)

    settings = Settings(**env_settings_kwargs())
    factory = build_session_factory(settings)
    failures = 0
    with factory() as db:
        try:
            detected, notes = _detect(
                db,
                only=set(args.only),
                ignore=set(args.ignore),
                domain_overrides=domains,
                kind_overrides=kind_overrides,
            )
        except SyncError as exc:
            print(f"FAILED: {exc}")
            return 1

        for note in notes:
            print(f"skipped: {note}")
        if not detected:
            print("there are no sites to reconcile")
            return 2

        for item in detected:
            try:
                print(
                    _sync_one(
                        db,
                        item,
                        assign=assign,
                        apply=args.apply,
                        allow_outside=args.allow_outside,
                    )
                )
            except (SyncError, SetDomainError) as exc:
                db.rollback()
                failures += 1
                print(f"FAILED: {exc}")

        if args.retire_old:
            for line in _retire_old(db, apply=args.apply):
                print(line)

    if not args.apply:
        print("\ndry-run: nothing was written. Repeat with --apply to confirm.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
