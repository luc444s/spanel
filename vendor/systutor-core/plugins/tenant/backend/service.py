from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.permissions.models import Permission, Role, RolePermission
from systutor.kernel.permissions.service import assign_role_to_user
from systutor.kernel.tenants.models import Tenant

MAIL_PERMISSIONS = [
    "mail.accounts.read",
    "mail.accounts.create",
    "mail.account.password",
    "core.plugin.runtime.read",
]


class TenantNotFoundError(ValueError):
    """Raised when a tenant is not found."""


class UserAlreadyInTenantError(ValueError):
    """Raised when a user is already assigned to a tenant."""


def _get_or_create_permission(db: Session, name: str) -> Permission:
    perm = db.scalar(select(Permission).where(Permission.name == name))
    if perm is not None:
        return perm
    perm = Permission(name=name, description=f"Permission {name}")
    db.add(perm)
    db.flush()
    return perm


def _get_or_create_tenant_admin_role(db: Session, tenant_id: str) -> Role:
    role = db.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == "tenant_admin")
    )
    if role is not None:
        return role

    role = Role(
        tenant_id=tenant_id,
        name="tenant_admin",
        description="Tenant administrator with full mail access",
    )
    db.add(role)
    db.flush()

    for perm_name in MAIL_PERMISSIONS:
        perm = _get_or_create_permission(db, perm_name)
        exists = db.scalar(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == perm.id,
            )
        )
        if not exists:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
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
        is_superadmin=False,
    )
    db.add(user)
    db.flush()

    role = _get_or_create_tenant_admin_role(db, tenant_id)
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
