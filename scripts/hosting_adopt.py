#!/usr/bin/env python3
"""Adopt an existing WordPress container into one tenant. Operator CLI.

Adoption is explicit because a container that already exists has no owner:
guessing "the only tenant there is" is how a staging site ends up in a customer
list. This script verifies that the container exists and is a WordPress using
the extracted detection of A.SPEC 0012, resolves the tenant by slug through the
core model, and writes ONE `hosting_sites` row plus one audit row.

It never starts, stops or writes to the container.

Usage:
    python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo
    python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --domain demo.example
    python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run

Exit codes: 0 ok (or dry-run), 1 verification/DB failure, 2 already owned.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
# `plugins` lives in the core root; `systutor` lives in its `src`. Both go
# first so a checkout does not silently import an editable install that points
# at another clone.
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.inspection import (  # pyright: ignore[reportMissingImports]
    candidato_de,
    inspect_container,
)
from plugins.hosting.backend.models import (
    HostingSite,  # pyright: ignore[reportMissingImports]
)
from plugins.hosting.backend.settings import (  # pyright: ignore[reportMissingImports]
    docker_env,
    get_hosting_settings,
)
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant, new_uuid


class AdoptionError(Exception):
    """A failure explained to the operator, not a traceback."""


class AlreadyOwnedError(AdoptionError):
    """The container is already in `hosting_sites`."""


@dataclass(slots=True)
class AdoptionResult:
    container_name: str
    tenant_id: str
    tenant_slug: str
    domain: str | None
    site_id: str | None
    dry_run: bool


def _assert_wordpress(container_name: str) -> None:
    hosting_settings = get_hosting_settings()
    info = inspect_container(
        docker_env(hosting_settings),
        container_name,
        timeout=hosting_settings.command_timeout,
    )
    if info is None:
        raise AdoptionError(f"el contenedor {container_name} no existe o no responde")
    candidato = candidato_de(info)
    if not candidato.es_sitio:
        raise AdoptionError(
            f"el contenedor {container_name} no es un sitio WordPress ({candidato.por_que})"
        )


def _tenant_by_slug(db: Session, slug: str) -> Tenant:
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise AdoptionError(f"no existe ningun tenant con slug {slug!r}")
    return tenant


def _owner_of(db: Session, container_name: str) -> HostingSite | None:
    stmt: Select[tuple[HostingSite]] = select(HostingSite).where(
        HostingSite.container_name == container_name
    )
    return db.scalar(stmt)


def adopt_container(
    db: Session,
    *,
    container_name: str,
    tenant_slug: str,
    domain: str | None = None,
    dry_run: bool = False,
) -> AdoptionResult:
    """Adopt one container, or explain why it cannot be adopted.

    Verification happens before touching the database. A dry run stops right
    after it: no row and no audit are written.
    """
    _assert_wordpress(container_name)
    tenant = _tenant_by_slug(db, tenant_slug)

    existing = _owner_of(db, container_name)
    if existing is not None:
        owner = db.get(Tenant, existing.tenant_id)
        owner_label = f"{owner.slug} ({owner.id})" if owner is not None else existing.tenant_id
        raise AlreadyOwnedError(
            f"el contenedor {container_name} ya pertenece al tenant {owner_label}"
        )

    if dry_run:
        return AdoptionResult(
            container_name=container_name,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            domain=domain,
            site_id=None,
            dry_run=True,
        )

    site = HostingSite(
        id=new_uuid(),
        tenant_id=tenant.id,
        container_name=container_name,
        domain=domain,
    )
    db.add(site)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AlreadyOwnedError(
            f"el contenedor {container_name} ya esta adoptado (restriccion unica)"
        ) from exc

    record_audit(
        db,
        tenant_id=tenant.id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="hosting",
        action="hosting.site.adopted",
        entity_type="hosting_site",
        entity_id=site.id,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "container_name": container_name,
            "domain": domain,
        },
    )
    db.flush()
    return AdoptionResult(
        container_name=container_name,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        domain=domain,
        site_id=site.id,
        dry_run=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--container", required=True, help="nombre del contenedor WordPress")
    parser.add_argument("--tenant", required=True, help="slug del tenant que sera el dueno")
    parser.add_argument("--domain", default=None, help="dominio publico del sitio (opcional)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verifica y resuelve, pero no escribe ninguna fila",
    )
    args = parser.parse_args(argv)

    settings = Settings(**env_settings_kwargs())
    session_factory = build_session_factory(settings)
    with session_factory() as db:
        try:
            result = adopt_container(
                db,
                container_name=args.container,
                tenant_slug=args.tenant,
                domain=args.domain,
                dry_run=args.dry_run,
            )
        except AlreadyOwnedError as exc:
            print(f"FALLO: {exc}")
            return 2
        except AdoptionError as exc:
            print(f"FALLO: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001 - explained to the operator
            print(f"FALLO: no se pudo adoptar: {exc}")
            return 1

        if result.dry_run:
            print(
                f"dry-run: {result.container_name} -> tenant {result.tenant_slug} "
                f"({result.tenant_id}); no se escribio nada"
            )
            return 0

        db.commit()

    print(
        f"adoptado: {result.container_name} -> tenant {result.tenant_slug} "
        f"({result.tenant_id}), sitio {result.site_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
