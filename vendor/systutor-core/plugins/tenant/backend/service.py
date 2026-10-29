from __future__ import annotations

import re

from fastapi import FastAPI
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from plugins.tenant.backend.models import TenantDomain, TenantSiteSettings
from systutor.contracts.domain_namespace import (
    DomainConflictError,
    domain_usage,
    normalize_domain_name,
)
from systutor.contracts.domain_ownership import is_extra_claimed, list_sites
from systutor.contracts.site_namespace import (
    default_suffix_from_domain,
    normalize_site_suffix,
    site_suffixes_overlap,
)
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.permissions.models import Role
from systutor.kernel.permissions.service import assign_role_to_user, sync_tenant_admin_role
from systutor.kernel.tenants.models import Tenant

TENANT_ADMIN_ROLE = "tenant_admin"

_UNSET = object()


class TenantNotFoundError(ValueError):
    """Raised when a tenant is not found."""


class UserAlreadyInTenantError(ValueError):
    """Raised when a user is already assigned to a tenant."""


def _get_or_create_tenant_admin_role(db: Session, tenant_id: str) -> Role:
    role = db.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == TENANT_ADMIN_ROLE)
    )
    if role is not None:
        return role

    role = Role(
        tenant_id=tenant_id,
        name=TENANT_ADMIN_ROLE,
        description="Tenant administrator",
    )
    db.add(role)
    db.flush()
    return role


def list_tenants(db: Session) -> list[Tenant]:
    stmt: Select[tuple[Tenant]] = select(Tenant).order_by(Tenant.name.asc())
    return list(db.scalars(stmt))


def get_tenant_by_id(db: Session, tenant_id: str) -> Tenant | None:
    stmt: Select[tuple[Tenant]] = select(Tenant).where(Tenant.id == tenant_id)
    return db.scalar(stmt)


def normalize_tenant_domain(value: str | None) -> str | None:
    """Normaliza el dominio del tenant y convierte vacío en NULL.

    El dominio se usa para correo, así que se guarda en minúsculas y sin el
    punto final. Un string vacío no es un dominio: es la ausencia de dominio.
    """
    if value is None:
        return None
    text = value.strip().lower().removesuffix(".")
    if not text:
        return None
    if re.search(r"[\s/]", text):
        raise AppError(
            "El dominio del tenant no puede tener espacios ni barras",
            status_code=422,
            code="tenant_domain_invalid",
        )
    return text


def normalize_tenant_site_suffix(value: str | None) -> str | None:
    """Normaliza el sufijo de sitios y traduce el error puro a `AppError`."""
    try:
        return normalize_site_suffix(value)
    except ValueError as exc:
        raise AppError(
            str(exc),
            status_code=422,
            code="tenant_site_suffix_invalid",
        ) from exc


def get_tenant_site_settings(db: Session, tenant_id: str) -> TenantSiteSettings | None:
    stmt: Select[tuple[TenantSiteSettings]] = select(TenantSiteSettings).where(
        TenantSiteSettings.tenant_id == tenant_id
    )
    return db.scalar(stmt)


def get_site_suffix_for_tenant(db: Session, tenant_id: str) -> str | None:
    settings = get_tenant_site_settings(db, tenant_id)
    return settings.site_suffix if settings is not None else None


def _get_or_create_site_settings(db: Session, tenant_id: str) -> TenantSiteSettings:
    settings = get_tenant_site_settings(db, tenant_id)
    if settings is None:
        settings = TenantSiteSettings(tenant_id=tenant_id, site_suffix=None)
        db.add(settings)
        db.flush()
    return settings


def ensure_site_suffix_available(
    db: Session,
    *,
    suffix: str | None,
    exclude_tenant_id: str | None = None,
) -> None:
    """409 cuando otro tenant tiene un sufijo igual o solapado.

    El índice único solo cubre igualdad; el solapamiento jerárquico
    (`spanel` vs `spanel.site`) se decide aquí, antes de escribir.
    """
    if not suffix:
        return
    stmt = select(TenantSiteSettings, Tenant).join(
        Tenant, Tenant.id == TenantSiteSettings.tenant_id
    )
    if exclude_tenant_id is not None:
        stmt = stmt.where(TenantSiteSettings.tenant_id != exclude_tenant_id)
    for other_settings, other_tenant in db.execute(stmt).all():
        other_suffix = other_settings.site_suffix
        if not other_suffix:
            continue
        if site_suffixes_overlap(suffix, other_suffix):
            raise AppError(
                f"El sufijo {suffix} se solapa con el del tenant "
                f"{other_tenant.name} ({other_tenant.slug})",
                status_code=409,
                code="tenant_site_suffix_conflict",
            )


def set_site_suffix(db: Session, *, tenant_id: str, site_suffix: str | None) -> None:
    normalized = normalize_tenant_site_suffix(site_suffix)
    ensure_site_suffix_available(
        db,
        suffix=normalized,
        exclude_tenant_id=tenant_id,
    )
    settings = _get_or_create_site_settings(db, tenant_id)
    settings.site_suffix = normalized
    db.add(settings)
    db.flush()


def ensure_domain_available(
    db: Session,
    *,
    domain: str | None,
    exclude_tenant_id: str | None = None,
) -> None:
    """409 claro cuando otro tenant ya tiene ese dominio.

    La base tiene un índice único, pero sin esta comprobación un choque llega
    como `IntegrityError` y la API responde 500. El chequeo se hace con
    `lower()` para que `Empresa.com` y `empresa.com` cuenten como el mismo.
    """
    if not domain:
        return
    needle = domain.strip().lower().removesuffix(".")
    if not needle:
        return
    stmt = select(Tenant).where(func.lower(Tenant.domain) == needle)
    if exclude_tenant_id is not None:
        stmt = stmt.where(Tenant.id != exclude_tenant_id)
    other = db.scalar(stmt)
    if other is not None:
        raise AppError(
            f"El dominio {needle} ya está asignado al tenant {other.name} ({other.slug})",
            status_code=409,
            code="tenant_domain_conflict",
        )


def create_tenant(db: Session, *, name: str, slug: str, domain: str | None = None) -> Tenant:
    normalized = normalize_tenant_domain(domain)
    ensure_domain_available(db, domain=normalized)
    tenant = Tenant(name=name, slug=slug, domain=normalized)
    db.add(tenant)
    db.flush()
    # Postcondición 3: todo tenant nace con su fila de settings. El sufijo
    # inicial se deriva del dominio de correo, pero vive en otra tabla.
    suffix = default_suffix_from_domain(normalized)
    ensure_site_suffix_available(db, suffix=suffix, exclude_tenant_id=tenant.id)
    db.add(TenantSiteSettings(tenant_id=tenant.id, site_suffix=suffix))
    db.flush()
    # A.SPEC 0012: the tenant plugin is no longer the source of truth for
    # tenant_admin. The core sync grants exactly what installed plugins
    # declare for tenant administrators. An empty declaration is valid.
    sync_tenant_admin_role(db, tenant_id=tenant.id)
    return tenant


def create_user_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    email: str,
    password: str,
    full_name: str,
) -> User:
    user = User(
        tenant_id=tenant_id,
        email=email,
        full_name=full_name or email.split("@")[0],
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.flush()

    role = _get_or_create_tenant_admin_role(db, tenant_id)
    sync_tenant_admin_role(db, tenant_id=tenant_id)
    assign_role_to_user(db, user=user, role=role)

    return user


def update_tenant_domain(db: Session, *, tenant: Tenant, domain: str | None) -> Tenant:
    normalized = normalize_tenant_domain(domain)
    ensure_domain_available(db, domain=normalized, exclude_tenant_id=tenant.id)
    tenant.domain = normalized
    db.add(tenant)
    if normalized is not None:
        stale = db.get(TenantDomain, normalized)
        if stale is not None and stale.tenant_id == tenant.id:
            db.delete(stale)
    db.flush()
    return tenant


def update_tenant(
    db: Session,
    *,
    tenant: Tenant,
    domain: str | None | object = _UNSET,
    site_suffix: str | None | object = _UNSET,
) -> Tenant:
    """Actualiza solo los campos enviados, sin pisar el otro.

    La validación se completa antes de mutar el tenant: un conflicto de sufijo
    no deja el dominio de correo a medio escribir.
    """
    normalized_domain: str | None | object = _UNSET
    normalized_suffix: str | None | object = _UNSET

    if domain is not _UNSET:
        normalized_domain = normalize_tenant_domain(domain)  # type: ignore[arg-type]
        ensure_domain_available(
            db,
            domain=normalized_domain,  # type: ignore[arg-type]
            exclude_tenant_id=tenant.id,
        )
    if site_suffix is not _UNSET:
        normalized_suffix = normalize_tenant_site_suffix(site_suffix)  # type: ignore[arg-type]
        ensure_site_suffix_available(
            db,
            suffix=normalized_suffix,  # type: ignore[arg-type]
            exclude_tenant_id=tenant.id,
        )

    if normalized_domain is not _UNSET:
        tenant.domain = normalized_domain  # type: ignore[assignment]
        db.add(tenant)
        # Promoción: si el nuevo primario ya era un secundario del tenant, su
        # fila en `tenant_domains` deja de ser válida.
        if isinstance(normalized_domain, str):
            stale = db.get(TenantDomain, normalized_domain)
            if stale is not None and stale.tenant_id == tenant.id:
                db.delete(stale)
    if normalized_suffix is not _UNSET:
        settings = _get_or_create_site_settings(db, tenant.id)
        settings.site_suffix = normalized_suffix  # type: ignore[assignment]
        db.add(settings)
    db.flush()
    return tenant


def list_users_for_tenant(db: Session, tenant_id: str) -> list[User]:
    stmt: Select[tuple[User]] = (
        select(User).where(User.tenant_id == tenant_id).order_by(User.email.asc())
    )
    return list(db.scalars(stmt))


def assign_user_to_tenant(db: Session, *, user: User, tenant_id: str) -> User:
    if user.tenant_id == tenant_id:
        raise UserAlreadyInTenantError("User is already assigned to this tenant")
    user.tenant_id = tenant_id
    db.add(user)
    db.flush()
    return user


def remove_user_from_tenant(db: Session, *, user: User, target_tenant_id: str) -> User:
    user.tenant_id = target_tenant_id
    user.branch_id = None
    db.add(user)
    db.flush()
    return user


# ---- dominios administrados ------------------------------------------------


def _normalize_managed_domain(value: str) -> str:
    """Normaliza un dominio administrado y traduce el error puro a `AppError`."""
    try:
        return normalize_domain_name(value)
    except ValueError as exc:
        raise AppError(
            str(exc),
            status_code=422,
            code="tenant_domain_invalid",
        ) from exc


def list_managed_domains(db: Session, tenant_id: str) -> list[str]:
    """Dominios del tenant, incluido su dominio de correo primario.

    `tenants.domain` cuenta como propiedad aunque la fila explícita falte (una
    base anterior a la migración o un borrado manual): si no apareciera aquí,
    la UI ocultaría un dominio que luego bloquea el alta en otro tenant.
    """
    stmt: Select[tuple[TenantDomain]] = select(TenantDomain).where(
        TenantDomain.tenant_id == tenant_id
    )
    domains = {row.domain for row in db.scalars(stmt)}
    tenant = db.get(Tenant, tenant_id)
    primary = (tenant.domain or "").strip().lower().removesuffix(".") if tenant else ""
    if primary:
        domains.add(primary)
    return sorted(domains)


def get_managed_domain(db: Session, domain: str) -> TenantDomain | None:
    return db.get(TenantDomain, domain)


def get_managed_domain_owner(db: Session, domain: str) -> str | None:
    """Provider del contrato: tenant dueño del dominio, o `None` si está libre.

    La fila explícita manda; si no existe, `tenants.domain` sigue siendo el
    dominio de correo del tenant y también cuenta como propiedad. Así `check`
    no ofrece un dominio que después el alta rechazaría.
    """
    try:
        needle = normalize_domain_name(domain)
    except ValueError:
        return None
    row = db.get(TenantDomain, needle)
    if row is not None:
        return row.tenant_id
    tenant = db.scalar(
        select(Tenant).where(func.lower(Tenant.domain) == needle)
    )
    return tenant.id if tenant is not None else None


def claim_managed_domain(
    db: Session, *, tenant_id: str, domain: str
) -> tuple[TenantDomain, bool]:
    """Reclama el dominio para el tenant. Idempotente para el mismo dueño.

    Un dominio primario (`tenants.domain`) no puede vivir en `tenant_domains`:
    el primario se cambia en el tenant, no se añade como secundario.
    """
    needle = _normalize_managed_domain(domain)
    primary_owner = db.scalar(
        select(Tenant.id).where(func.lower(Tenant.domain) == needle)
    )
    if primary_owner is not None:
        raise AppError(
            f"El dominio {needle} es el dominio primario de un tenant y no "
            "puede añadirse como dominio adicional",
            status_code=409,
            code="tenant_domain_conflict",
        )
    existing = db.get(TenantDomain, needle)
    if existing is not None:
        if existing.tenant_id != tenant_id:
            raise AppError(
                f"El dominio {needle} ya está asignado a otro tenant",
                status_code=409,
                code="tenant_domain_conflict",
            )
        return existing, False

    row = TenantDomain(domain=needle, tenant_id=tenant_id)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            f"El dominio {needle} ya está asignado a otro tenant",
            status_code=409,
            code="tenant_domain_conflict",
        ) from exc
    return row, True


def _primary_of_other_tenant(
    app: FastAPI | None, db: Session, domain: str, tenant_id: str
):
    if app is None:
        return None
    for site in list_sites(app, db, None):
        if (site.primary_domain or "").strip().lower() == domain and site.tenant_id != tenant_id:
            return site
    return None


def add_managed_domain(
    db: Session,
    *,
    tenant: Tenant,
    domain: str,
    app: FastAPI | None = None,
) -> tuple[TenantDomain, bool]:
    """Añade un dominio administrado o devuelve el existente del mismo tenant."""
    needle = _normalize_managed_domain(domain)

    primary = (tenant.domain or "").strip().lower().removesuffix(".")
    if primary and needle == primary:
        raise AppError(
            f"El dominio {needle} es el dominio primario del tenant y no puede "
            "añadirse como dominio adicional; se cambia en «Dominio y sufijo»",
            status_code=409,
            code="tenant_domain_conflict",
        )

    existing = db.get(TenantDomain, needle)
    if existing is not None:
        if existing.tenant_id == tenant.id:
            return existing, False
        raise AppError(
            f"El dominio {needle} ya está asignado a otro tenant",
            status_code=409,
            code="tenant_domain_conflict",
        )

    # `tenants.domain` sigue siendo el dominio de correo primario: no se
    # reescribe, pero tampoco se permite que otro tenant lo reclame.
    other = db.scalar(
        select(Tenant).where(
            func.lower(Tenant.domain) == needle, Tenant.id != tenant.id
        )
    )
    if other is not None:
        raise AppError(
            f"El dominio {needle} es el dominio de correo del tenant "
            f"{other.name} ({other.slug}). Cámbialo en ese tenant o unifica "
            "la cuenta antes de reclamarlo.",
            status_code=409,
            code="tenant_domain_conflict",
        )

    site = _primary_of_other_tenant(app, db, needle, tenant.id)
    if site is not None:
        raise AppError(
            f"El dominio {needle} es el primario del contenedor {site.container} "
            "de otro tenant",
            status_code=409,
            code="tenant_domain_conflict",
        )

    return claim_managed_domain(db, tenant_id=tenant.id, domain=needle)


def remove_managed_domain(
    db: Session,
    *,
    tenant: Tenant,
    domain: str,
    app: FastAPI | None = None,
) -> None:
    """Elimina un dominio libre del conjunto del tenant.

    Un dominio primario de un sitio propio o publicado como extra por uno de
    sus contenedores está en uso y no se elimina.
    """
    needle = _normalize_managed_domain(domain)
    row = db.get(TenantDomain, needle)
    if row is not None and row.tenant_id != tenant.id:
        raise AppError(
            f"El dominio {needle} no pertenece al tenant",
            status_code=404,
            code="tenant_domain_not_found",
        )

    if app is not None:
        own_containers = {
            site.container for site in list_sites(app, db, tenant.id)
        }
        for site in list_sites(app, db, tenant.id):
            if (site.primary_domain or "").strip().lower() == needle:
                raise AppError(
                    f"El dominio {needle} es el primario del contenedor "
                    f"{site.container}",
                    status_code=409,
                    code="tenant_domain_in_use",
                )
        usage = domain_usage(app, db, needle)
        if usage is not None:
            if usage.tenant_id == tenant.id or usage.container in own_containers:
                raise AppError(
                    f"El dominio {needle} está publicado por el contenedor "
                    f"{usage.container}",
                    status_code=409,
                    code="tenant_domain_in_use",
                )
        elif is_extra_claimed(app, db, needle):
            raise AppError(
                f"El dominio {needle} está publicado como dominio extra",
                status_code=409,
                code="tenant_domain_in_use",
            )

    # El dominio de correo del tenant no se "quita": se cambia en el tenant.
    # Sin esta guarda la fila desaparece de la lista pero sigue bloqueando el
    # alta en otro tenant, que fue justo el estado confuso que la originó.
    primary = (tenant.domain or "").strip().lower().removesuffix(".")
    if primary and needle == primary:
        raise AppError(
            f"El dominio {needle} es el dominio de correo del tenant. "
            "Cámbialo en \"Dominio y sufijo\" antes de quitarlo.",
            status_code=409,
            code="tenant_domain_in_use",
        )

    if row is None:
        raise AppError(
            f"El dominio {needle} no pertenece al tenant",
            status_code=404,
            code="tenant_domain_not_found",
        )

    db.delete(row)
    db.flush()


def claim_domain_for_tenant(db: Session, tenant_id: str, domain: str) -> None:
    """Provider de escritura del contrato: reclama o falla si es de otro."""
    try:
        claim_managed_domain(db, tenant_id=tenant_id, domain=domain)
    except AppError as exc:
        owner = get_managed_domain_owner(db, domain) or ""
        raise DomainConflictError(domain, owner) from exc


def release_domain_for_tenant(
    db: Session, tenant_id: str, domain: str, app: FastAPI | None = None
) -> None:
    """Provider de liberación del contrato: suelta una reserva del tenant.

    Delega en `remove_managed_domain`, que ya guarda: primario de sitio y
    dominio en uso responden `409 tenant_domain_in_use`; dominio ajeno o
    desconocido responde 404.
    """
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError(
            "Tenant desconocido",
            status_code=404,
            code="tenant_domain_not_found",
        )
    remove_managed_domain(db, tenant=tenant, domain=domain, app=app)

