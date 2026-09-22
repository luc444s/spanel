from __future__ import annotations

from sqlalchemy import Select, delete, distinct, select
from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import Permission, Role, RolePermission, UserRole
from systutor.kernel.tenants.service import TenantScopeError


def list_user_permissions(db: Session, *, user_id: str, tenant_id: str) -> list[str]:
    stmt: Select[tuple[str]] = (
        select(distinct(Permission.name))
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            Role.tenant_id == tenant_id,
            Role.is_active.is_(True),
        )
        .order_by(Permission.name.asc())
    )
    return list(db.scalars(stmt))


def user_has_permission(db: Session, *, user_id: str, tenant_id: str, permission_name: str) -> bool:
    stmt = (
        select(Permission.id)
        .select_from(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            Role.tenant_id == tenant_id,
            Role.is_active.is_(True),
            Permission.name == permission_name,
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def list_roles_for_tenant(db: Session, *, tenant_id: str) -> list[Role]:
    stmt: Select[tuple[Role]] = (
        select(Role).where(Role.tenant_id == tenant_id).order_by(Role.name.asc())
    )
    return list(db.scalars(stmt))


def list_roles_by_ids_for_tenant(db: Session, *, tenant_id: str, role_ids: list[str]) -> list[Role]:
    if not role_ids:
        return []

    stmt: Select[tuple[Role]] = (
        select(Role)
        .where(Role.tenant_id == tenant_id, Role.id.in_(role_ids))
        .order_by(Role.name.asc())
    )
    return list(db.scalars(stmt))


def list_roles_by_names_for_tenant(
    db: Session, *, tenant_id: str, role_names: list[str]
) -> list[Role]:
    if not role_names:
        return []

    stmt: Select[tuple[Role]] = (
        select(Role)
        .where(Role.tenant_id == tenant_id, Role.name.in_(role_names), Role.is_active.is_(True))
        .order_by(Role.name.asc())
    )
    return list(db.scalars(stmt))


def get_role_for_tenant(db: Session, *, tenant_id: str, role_id: str) -> Role | None:
    stmt: Select[tuple[Role]] = select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id)
    return db.scalar(stmt)


def create_role_for_tenant(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    description: str | None,
) -> Role:
    role = Role(tenant_id=tenant_id, name=name, description=description, is_active=True)
    db.add(role)
    db.flush()
    return role


def update_role_for_tenant(
    db: Session,
    *,
    role: Role,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> Role:
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    if is_active is not None:
        role.is_active = is_active
    db.add(role)
    db.flush()
    return role


def delete_role_for_tenant(db: Session, *, role: Role) -> None:
    db.delete(role)
    db.flush()


def assign_role_to_user(db: Session, *, user: User, role: Role) -> UserRole:
    if user.tenant_id != role.tenant_id:
        raise TenantScopeError("Role does not belong to the user's tenant")

    stmt: Select[tuple[UserRole]] = select(UserRole).where(
        UserRole.user_id == user.id,
        UserRole.role_id == role.id,
    )
    existing = db.scalar(stmt)
    if existing is not None:
        return existing

    user_role = UserRole(user_id=user.id, role_id=role.id)
    db.add(user_role)
    db.flush()
    return user_role


def remove_role_from_user(db: Session, *, user: User, role: Role) -> bool:
    if user.tenant_id != role.tenant_id:
        raise TenantScopeError("Role does not belong to the user's tenant")

    stmt: Select[tuple[UserRole]] = select(UserRole).where(
        UserRole.user_id == user.id,
        UserRole.role_id == role.id,
    )
    user_role = db.scalar(stmt)
    if user_role is None:
        return False

    db.delete(user_role)
    db.flush()
    return True


def replace_user_roles(db: Session, *, user: User, roles: list[Role]) -> None:
    if any(role.tenant_id != user.tenant_id for role in roles):
        raise TenantScopeError("Role does not belong to the user's tenant")

    db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db.flush()

    seen_role_ids: set[str] = set()
    for role in roles:
        if role.id in seen_role_ids:
            continue
        seen_role_ids.add(role.id)
        db.add(UserRole(user_id=user.id, role_id=role.id))

    db.flush()


def list_permissions(db: Session) -> list[Permission]:
    stmt: Select[tuple[Permission]] = select(Permission).order_by(Permission.name.asc())
    return list(db.scalars(stmt))


def list_permissions_by_names(db: Session, *, permission_names: list[str]) -> list[Permission]:
    if not permission_names:
        return []

    stmt: Select[tuple[Permission]] = (
        select(Permission)
        .where(Permission.name.in_(permission_names))
        .order_by(Permission.name.asc())
    )
    return list(db.scalars(stmt))


def get_permission_by_id(db: Session, *, permission_id: str) -> Permission | None:
    stmt: Select[tuple[Permission]] = select(Permission).where(Permission.id == permission_id)
    return db.scalar(stmt)


def get_permission_by_name(db: Session, *, permission_name: str) -> Permission | None:
    stmt: Select[tuple[Permission]] = select(Permission).where(Permission.name == permission_name)
    return db.scalar(stmt)


def ensure_permission(
    db: Session,
    *,
    permission_name: str,
    description: str | None = None,
) -> Permission:
    permission = get_permission_by_name(db, permission_name=permission_name)
    if permission is not None:
        return permission

    permission = Permission(name=permission_name, description=description)
    db.add(permission)
    db.flush()
    return permission


def list_role_permissions(db: Session, *, role_id: str) -> list[Permission]:
    stmt: Select[tuple[Permission]] = (
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.name.asc())
    )
    return list(db.scalars(stmt))


def list_role_permission_names(db: Session, *, role_id: str) -> list[str]:
    return [permission.name for permission in list_role_permissions(db, role_id=role_id)]


def list_user_role_names(db: Session, *, tenant_id: str, user_id: str) -> list[str]:
    stmt: Select[tuple[str]] = (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            Role.tenant_id == tenant_id,
            Role.is_active.is_(True),
        )
        .order_by(Role.name.asc())
    )
    return list(db.scalars(stmt))


def replace_role_permissions(
    db: Session,
    *,
    role: Role,
    permissions: list[Permission],
) -> None:
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    db.flush()

    existing_permission_ids: set[str] = set()
    for permission in permissions:
        if permission.id in existing_permission_ids:
            continue
        existing_permission_ids.add(permission.id)
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    db.flush()
