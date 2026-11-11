#!/usr/bin/env python3
"""Baja lógica universal de un sitio registrado en Hosting.

Retira la ruta administrada por el file provider, conserva el dominio en el
namespace del tenant y elimina solamente la fila de ``hosting_sites``. Detecta
el tipo desde el registro: no deriva nombres ni presupone WordPress, nginx,
MariaDB o una convención de Docker.

No detiene ni modifica contenedores y no elimina imágenes, volúmenes, redes,
bases de datos, usuarios ni archivos. Si el dominio se publica mediante labels
Traefik del propio contenedor, bloquea la baja: quitar esa ruta requeriría
alterar o detener el contenedor. ``--keep-labeled-route`` habilita de forma
explícita el flujo de reemplazo: oculta el sitio y conserva temporalmente los
labels activos hasta que el nuevo contenedor publique la ruta sustituta.

Sin ``--execute`` es siempre dry-run.

Uso:
    python3 scripts/hosting_down.py --container tutorabusiness
    python3 scripts/hosting_down.py --container tutorabusiness \
        --keep-labeled-route --execute --confirm tutorabusiness

Exit codes: 0 correcto/plan, 1 validación o ejecución, 2 confirmación inválida.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.domains.backend.proxy import (
    ProxyError,
    ProxyOperations,
    build_runner,
)
from plugins.domains.backend.proxy import (
    Settings as ProxySettings,
)
from plugins.hosting.backend.inspection import inspect_container
from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.settings import (
    docker_env,
    get_hosting_settings,
)
from plugins.tenant.backend.models import TenantDomain
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant


class DownError(Exception):
    """Fallo seguro y explicable para el operador."""


@dataclass(frozen=True, slots=True)
class DownPlan:
    site_id: str
    tenant_id: str
    tenant_slug: str
    container: str
    kind: str
    domain: str
    container_present: bool
    route_file: str | None
    labeled_route_retained: bool
    domain_reserved: bool
    reserve_domain: bool
    blocked_reason: str | None = None


def _registered_site(db: Session, container: str) -> HostingSite:
    site = db.scalar(
        select(HostingSite).where(HostingSite.container_name == container)
    )
    if site is None:
        raise DownError(f"{container!r} is not registered in Hosting")
    if not site.domain:
        raise DownError("the site has no primary domain to release")
    return site


def _label_publishes(info: dict | None, domain: str) -> bool:
    """True si un router Traefik del contenedor publica el dominio."""
    config = (info or {}).get("Config") or {}
    labels = config.get("Labels") or {}
    if str(labels.get("traefik.enable", "")).lower() == "false":
        return False
    needles = (f"Host(`{domain}`)", f"Host(`www.{domain}`)")
    return any(
        key.startswith("traefik.http.routers.")
        and key.endswith(".rule")
        and any(needle in str(value) for needle in needles)
        for key, value in labels.items()
    )


def build_plan(
    db: Session,
    *,
    container: str,
    operations: ProxyOperations,
    keep_labeled_route: bool = False,
) -> DownPlan:
    """Inspecciona DB, Docker y proxy sin modificar ningún estado."""
    site = _registered_site(db, container)
    tenant = db.get(Tenant, site.tenant_id)
    if tenant is None:
        raise DownError(f"tenant {site.tenant_id!r} no longer exists")
    domain = site.domain.strip().lower().removesuffix(".")

    hosting = get_hosting_settings()
    info = inspect_container(
        docker_env(hosting), container, timeout=hosting.command_timeout
    )
    labels_route = _label_publishes(info, domain)

    try:
        claims = [row for row in operations.managed_domains() if row.domain == domain]
    except ProxyError as exc:
        raise DownError(f"could not inspect the proxy: {exc}") from exc
    if len(claims) > 1:
        raise DownError(f"domain {domain} appears in multiple managed routes")
    claim = claims[0] if claims else None
    blocked: str | None = None
    if labels_route and not keep_labeled_route:
        blocked = (
            "the container publishes the domain through Traefik labels; "
            "use --keep-labeled-route to hide it while temporarily keeping that route"
        )
    elif claim is not None and claim.container != container:
        blocked = f"the route belongs to another container: {claim.container}"

    reservation = db.get(TenantDomain, domain)
    if reservation is not None and reservation.tenant_id != site.tenant_id:
        raise DownError(f"domain {domain} is reserved by another tenant")
    primary_owner = db.scalar(
        select(Tenant).where(func.lower(Tenant.domain) == domain)
    )
    if primary_owner is not None and primary_owner.id != site.tenant_id:
        raise DownError(f"domain {domain} is another tenant's primary domain")
    reserved = reservation is not None or primary_owner is not None

    return DownPlan(
        site_id=site.id,
        tenant_id=site.tenant_id,
        tenant_slug=tenant.slug,
        container=container,
        kind=site.kind,
        domain=domain,
        container_present=info is not None,
        route_file=claim.file if claim else None,
        labeled_route_retained=labels_route and keep_labeled_route,
        domain_reserved=reserved,
        reserve_domain=not reserved,
        blocked_reason=blocked,
    )


def print_plan(plan: DownPlan) -> None:
    print("LOGICAL TEARDOWN PLAN")
    print(f"  site:        {plan.container} ({plan.kind})")
    print(f"  tenant:      {plan.tenant_slug} ({plan.tenant_id})")
    print(f"  domain:      {plan.domain}")
    print(f"  container:   {'present; will not be touched' if plan.container_present else 'absent'}")
    if plan.labeled_route_retained:
        print("  proxy route: container labels; will be kept temporarily")
    else:
        print(f"  proxy route: {plan.route_file or 'already absent'}")
    if plan.reserve_domain:
        print("  namespace:   will be reserved for the same tenant")
    else:
        print("  namespace:   will remain reserved for the same tenant")
    print("  Hosting:     will be hidden from the panel")
    print("  Docker/DB:   unchanged")
    if plan.blocked_reason:
        print(f"  BLOCKED:     {plan.blocked_reason}")


def execute_plan(db: Session, plan: DownPlan, operations: ProxyOperations) -> None:
    """Retira ruta, asegura reserva y desregistra; nunca ejecuta Docker mutante."""
    if plan.blocked_reason:
        raise DownError(plan.blocked_reason)

    # Revalidación de carrera inmediatamente antes de tocar el proxy.
    fresh = build_plan(
        db,
        container=plan.container,
        operations=operations,
        keep_labeled_route=plan.labeled_route_retained,
    )
    if fresh.blocked_reason:
        raise DownError(fresh.blocked_reason)
    if fresh.route_file:
        try:
            operations.remove(plan.domain)
        except ProxyError as exc:
            raise DownError(f"could not remove the route: {exc}") from exc

    site = db.get(HostingSite, plan.site_id)
    if site is None or site.container_name != plan.container:
        raise DownError("the Hosting record changed during the operation")

    if plan.reserve_domain:
        current = db.get(TenantDomain, plan.domain)
        if current is not None:
            if current.tenant_id != plan.tenant_id:
                raise DownError("domain ownership changed during the operation")
        else:
            db.add(TenantDomain(domain=plan.domain, tenant_id=plan.tenant_id))
            db.flush()

    db.delete(site)
    record_audit(
        db,
        tenant_id=plan.tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="hosting",
        action="hosting.site.down",
        entity_type="hosting_site",
        entity_id=plan.site_id,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "container_name": plan.container,
            "kind": plan.kind,
            "domain": plan.domain,
            "route_removed": bool(plan.route_file),
            "labeled_route_retained": plan.labeled_route_retained,
            "container_preserved": True,
            "domain_reserved": True,
            "source": "hosting_down.py",
        },
    )
    db.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--container", required=True, help="exact registered name")
    parser.add_argument(
        "--profile", choices=("internal", "public"), default="public"
    )
    parser.add_argument("--execute", action="store_true", help="apply the logical teardown")
    parser.add_argument("--confirm", default="", help="must match --container")
    parser.add_argument(
        "--keep-labeled-route",
        action="store_true",
        help="hide the site but temporarily keep its route via Traefik labels",
    )
    args = parser.parse_args(argv)

    # También carga el .env operativo; ProxySettings consume luego ese entorno.
    get_hosting_settings()
    try:
        proxy_settings = ProxySettings.from_env(args.profile)
        operations = ProxyOperations(proxy_settings, build_runner(proxy_settings))
    except (ValueError, ProxyError) as exc:
        print(f"FAILED: invalid proxy configuration: {exc}")
        return 1

    settings = Settings(**env_settings_kwargs())
    session_factory = build_session_factory(settings)
    with session_factory() as db:
        try:
            plan = build_plan(
                db,
                container=args.container,
                operations=operations,
                keep_labeled_route=args.keep_labeled_route,
            )
            print_plan(plan)
            if not args.execute:
                print("\nDry-run only. To execute add --execute --confirm " + plan.container)
                return 0
            if args.confirm != plan.container:
                print("FAILED: --confirm must exactly match the container")
                return 2
            execute_plan(db, plan, operations)
            db.commit()
        except DownError as exc:
            db.rollback()
            print(f"FAILED: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"FAILED: could not complete the logical teardown: {exc}")
            return 1

    print(f"LOGICAL TEARDOWN COMPLETE: {plan.container}")
    print(f"DOMAIN RESERVED FOR REPLACEMENT: {plan.domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
