#!/usr/bin/env python3
"""Baja segura de un WordPress gestionado por SPanel.

El script elimina exclusivamente un sitio registrado en ``hosting_sites``, su
contenedor WordPress y su volumen. Antes de modificar nada verifica los labels
de propiedad. De MariaDB elimina solamente ``WORDPRESS_DB_NAME``: nunca borra
el contenedor, volumen o imagen, sea una instancia dedicada o compartida.
También elimina los secretos y libera la reserva de ``tenant_domains``.

Sin ``--execute`` solo muestra el plan. La ejecución exige escribir el nombre
exacto del contenedor con ``--confirm``.

Ejemplos:
    python3 scripts/hosting_delete_wordpress.py --container spanel-demo-wp
    python3 scripts/hosting_delete_wordpress.py --container spanel-demo-wp \
        --execute --confirm spanel-demo-wp
    python3 scripts/hosting_delete_wordpress.py --container spanel-demo-wp \
        --keep-domain --execute --confirm spanel-demo-wp

Exit codes: 0 correcto/plan, 1 fallo de validación o ejecución, 2 confirmación
ausente o incorrecta.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from plugins.hosting.backend.config import env_of, site_dir
from plugins.hosting.backend.inspection import inspect_container
from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.provisioner import (
    Provisioner,
    SiteNames,
)
from plugins.hosting.backend.settings import get_hosting_settings
from plugins.hosting.backend.transport import (
    Fallo,
    build_docker_argv,
    run,
)
from plugins.tenant.backend.models import TenantDomain
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.models import Tenant


class DeleteError(Exception):
    """Fallo explicado al operador, sin traceback."""


@dataclass(frozen=True, slots=True)
class DeletePlan:
    site_id: str
    tenant_id: str
    tenant_slug: str
    container: str
    database_container: str
    database_mode: str
    database_name: str | None
    database_user: str | None
    volumes: tuple[str, str]
    domain: str | None
    release_domain: bool
    domain_reserved: bool
    missing: tuple[str, ...]
    stable_dir: str


def names_from_container(container: str) -> SiteNames:
    """Deriva nombres solo del formato creado por el provisioner."""
    prefix, suffix = "spanel-", "-wp"
    if not container.startswith(prefix) or not container.endswith(suffix):
        raise DeleteError(
            f"{container!r} no tiene el formato gestionado spanel-<slug>-wp"
        )
    slug = container[len(prefix) : -len(suffix)]
    names = SiteNames(slug)
    if not slug or names.wp != container:
        raise DeleteError(f"no se pudieron derivar recursos seguros desde {container!r}")
    return names


def _registered_site(db: Session, container: str) -> HostingSite:
    site = db.scalar(
        select(HostingSite).where(HostingSite.container_name == container)
    )
    if site is None:
        raise DeleteError(
            f"{container!r} no está registrado en Hosting; no se borrará por nombre solamente"
        )
    if site.kind != "wordpress":
        raise DeleteError(
            f"{container!r} es de tipo {site.kind!r}, no un sitio WordPress"
        )
    return site


def build_plan(
    db: Session,
    *,
    container: str,
    keep_domain: bool,
    provisioner: Provisioner,
) -> DeletePlan:
    """Valida DB, nombres y propiedad Docker sin modificar estado."""
    site = _registered_site(db, container)
    names = names_from_container(container)
    tenant = db.get(Tenant, site.tenant_id)
    if tenant is None:
        raise DeleteError(f"el tenant {site.tenant_id!r} del sitio ya no existe")

    wp_info = inspect_container(
        provisioner.env, names.wp, timeout=provisioner.settings.command_timeout
    )
    wp_env = env_of(wp_info or {})
    raw_db_host = wp_env.get("WORDPRESS_DB_HOST", "").strip()
    db_host = raw_db_host.rsplit(":", 1)[0] if raw_db_host else names.db
    database_name = wp_env.get("WORDPRESS_DB_NAME", "").strip() or None
    database_user = wp_env.get("WORDPRESS_DB_USER", "").strip() or None
    database_mode = "dedicated" if db_host == names.db else "shared"
    if wp_info is None:
        raise DeleteError(
            "el WordPress no existe y no se puede verificar qué base le pertenece"
        )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", db_host):
        raise DeleteError(f"host MariaDB inválido: {db_host!r}")
    if not database_name or not re.fullmatch(r"[A-Za-z0-9_$-]+", database_name):
        raise DeleteError("WORDPRESS_DB_NAME ausente o inseguro")
    if not database_user or not re.fullmatch(r"[A-Za-z0-9_$@.-]+", database_user):
        raise DeleteError("WORDPRESS_DB_USER ausente o inseguro")
    if not provisioner.exists("container", db_host):
        raise DeleteError(f"el contenedor MariaDB {db_host!r} no existe")
    # Regla absoluta: MariaDB y su volumen jamás forman parte del cleanup.
    resources = (
        ("container", names.wp),
        ("volume", names.wp),
    )
    missing: list[str] = []
    for kind, name in resources:
        try:
            exists = provisioner.exists(kind, name)
        except Fallo as exc:
            raise DeleteError(f"no se pudo inspeccionar {kind} {name}: {exc}") from exc
        if not exists:
            missing.append(f"{kind}:{name}")
            continue
        if not provisioner._owned(kind, name, names, site.tenant_id):
            raise DeleteError(
                f"{kind} {name!r} existe pero sus labels no pertenecen a este sitio/tenant"
            )

    domain = (site.domain or "").strip().lower().removesuffix(".") or None
    reservation = db.get(TenantDomain, domain) if domain else None
    if reservation is not None and reservation.tenant_id != site.tenant_id:
        raise DeleteError(
            f"el dominio {domain} está reservado por otro tenant; estado inconsistente"
        )

    release = bool(domain and not keep_domain and reservation is not None)
    tenant_primary = (tenant.domain or "").strip().lower().removesuffix(".")
    if release and tenant_primary == domain:
        raise DeleteError(
            f"{domain} es el dominio de correo primario del tenant; cambialo primero "
            "o repetí con --keep-domain"
        )

    return DeletePlan(
        site_id=site.id,
        tenant_id=site.tenant_id,
        tenant_slug=tenant.slug,
        container=names.wp,
        database_container=db_host,
        database_mode=database_mode,
        database_name=database_name,
        database_user=database_user,
        volumes=(names.wp,),
        domain=domain,
        release_domain=release,
        domain_reserved=reservation is not None,
        missing=tuple(missing),
        stable_dir=site_dir(provisioner.settings, container),
    )


def print_plan(plan: DeletePlan) -> None:
    print("PLAN DE BAJA (irreversible)")
    print(f"  sitio:       {plan.container} ({plan.site_id})")
    print(f"  tenant:      {plan.tenant_slug} ({plan.tenant_id})")
    print(
        f"  base datos:  {plan.database_name} dentro de MariaDB "
        f"{plan.database_container} ({plan.database_mode}; contenedor y volumen se conservan)"
    )
    print(f"  volúmenes:   {', '.join(plan.volumes)}")
    print(f"  secretos:    {plan.stable_dir}")
    print(f"  dominio:     {plan.domain or '-'}")
    if plan.release_domain:
        print("  dominio DB:  se liberará")
    elif plan.domain_reserved:
        print("  dominio DB:  se conservará reservado")
    else:
        print("  dominio DB:  no existe reserva")
    if plan.missing:
        print(f"  ya ausentes: {', '.join(plan.missing)}")


def execute_plan(db: Session, plan: DeletePlan, provisioner: Provisioner) -> None:
    """Borra infraestructura primero y confirma DB solo si quedó limpia."""
    names = names_from_container(plan.container)
    _drop_database(plan, provisioner)
    # Orden invertido por cleanup: primero contenedor WP, después su volumen.
    resources = [("volume", names.wp), ("container", names.wp)]
    failures = provisioner.cleanup(names, plan.tenant_id, resources)
    if failures:
        raise DeleteError("limpieza Docker incompleta: " + "; ".join(failures))

    result = run(
        build_docker_argv(
            f"rm -rf {shlex.quote(plan.stable_dir)}", env=provisioner.env
        ),
        env=provisioner.env,
        timeout=provisioner.settings.command_timeout,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[:400]
        raise DeleteError(f"no se pudo borrar {plan.stable_dir}: {detail}")

    site = db.get(HostingSite, plan.site_id)
    if site is None:
        raise DeleteError("el registro del sitio desapareció durante la operación")
    db.delete(site)

    if plan.release_domain and plan.domain:
        reservation = db.get(TenantDomain, plan.domain)
        if reservation is None or reservation.tenant_id != plan.tenant_id:
            raise DeleteError("la reserva del dominio cambió durante la operación")
        db.delete(reservation)

    record_audit(
        db,
        tenant_id=plan.tenant_id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="hosting",
        action="hosting.site.delete",
        entity_type="hosting_site",
        entity_id=plan.site_id,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "container_name": plan.container,
            "domain": plan.domain,
            "domain_released": plan.release_domain,
            "missing": list(plan.missing),
            "source": "hosting_delete_wordpress.py",
        },
    )
    db.flush()


def _drop_database(plan: DeletePlan, provisioner: Provisioner) -> None:
    """Elimina solo la DB usando su usuario WP; nunca MariaDB ni su volumen.

    La contraseña viaja por stdin y se exporta dentro del contenedor, no forma
    parte del argv ni del mensaje de error.
    """
    info = inspect_container(
        provisioner.env,
        plan.container,
        timeout=provisioner.settings.command_timeout,
    )
    values = env_of(info or {})
    password = values.get("WORDPRESS_DB_PASSWORD", "")
    if not password:
        raise DeleteError("WORDPRESS_DB_PASSWORD ausente; no se tocará MariaDB")
    if values.get("WORDPRESS_DB_NAME", "").strip() != plan.database_name:
        raise DeleteError("WORDPRESS_DB_NAME cambió desde el plan; operación cancelada")
    if values.get("WORDPRESS_DB_USER", "").strip() != plan.database_user:
        raise DeleteError("WORDPRESS_DB_USER cambió desde el plan; operación cancelada")

    sql = f"DROP DATABASE `{plan.database_name}`"
    shell = (
        'IFS= read -r MYSQL_PWD; export MYSQL_PWD; '
        'if command -v mariadb >/dev/null 2>&1; then client=mariadb; '
        'elif command -v mysql >/dev/null 2>&1; then client=mysql; '
        'else echo "cliente MariaDB ausente" >&2; exit 127; fi; '
        'exec "$client" -u "$1" -e "$2"'
    )
    try:
        provisioner.command(
            "exec",
            "-i",
            plan.database_container,
            "sh",
            "-c",
            shell,
            "sh",
            plan.database_user or "",
            sql,
            input=password + "\n",
        )
    except Fallo as exc:
        raise DeleteError(
            f"no se pudo eliminar únicamente la base {plan.database_name}: {exc}"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--container", required=True, help="contenedor spanel-<slug>-wp")
    parser.add_argument(
        "--keep-domain", action="store_true", help="conserva la reserva del dominio"
    )
    parser.add_argument(
        "--execute", action="store_true", help="ejecuta el plan irreversible"
    )
    parser.add_argument(
        "--confirm", default="", help="debe coincidir exactamente con --container"
    )
    args = parser.parse_args(argv)

    settings = Settings(**env_settings_kwargs())
    session_factory = build_session_factory(settings)
    provisioner = Provisioner(get_hosting_settings())
    with session_factory() as db:
        try:
            plan = build_plan(
                db,
                container=args.container,
                keep_domain=args.keep_domain,
                provisioner=provisioner,
            )
            print_plan(plan)
            if not args.execute:
                print("\nSolo plan. Para ejecutar agregá --execute --confirm " + plan.container)
                return 0
            if args.confirm != plan.container:
                print("FALLO: --confirm debe coincidir exactamente con el contenedor")
                return 2
            execute_plan(db, plan, provisioner)
            db.commit()
        except DeleteError as exc:
            db.rollback()
            print(f"FALLO: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001 - mensaje operativo sin secretos
            db.rollback()
            print(f"FALLO: no se pudo completar la baja: {exc}")
            return 1

    print(f"BAJA COMPLETA: {plan.container}")
    if plan.domain:
        state = "liberado" if plan.release_domain else "conservado"
        print(f"DOMINIO: {plan.domain} ({state})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
