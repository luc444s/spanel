from __future__ import annotations

import re

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

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

from plugins.tenant.backend.models import TenantSiteSettings

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
