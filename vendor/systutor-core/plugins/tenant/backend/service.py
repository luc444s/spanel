from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.permissions.models import Role
from systutor.kernel.permissions.service import assign_role_to_user, sync_tenant_admin_role
from systutor.kernel.tenants.models import Tenant

TENANT_ADMIN_ROLE = "tenant_admin"


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


def create_tenant(db: Session, *, name: str, slug: str, domain: str | None = None) -> Tenant:
    tenant = Tenant(name=name, slug=slug, domain=domain)
    db.add(tenant)
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
    tenant.domain = domain
    db.add(tenant)
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
