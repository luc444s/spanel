from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.core.config import Settings
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.permissions.models import Permission, Role, RolePermission
from systutor.kernel.permissions.service import assign_role_to_user
from systutor.kernel.plugins.runtime import LoadedPlugin
from systutor.kernel.plugins.service import sync_plugin_registry
from systutor.kernel.tenants.models import Branch, Tenant
from systutor.kernel.tenants.service import assign_branch_to_user

BASE_PERMISSIONS = [
    "core.auth.me",
    "core.plugin.read",
    "core.plugin.runtime.read",
    "core.plugin.manage",
    "core.audit.read",
    "core.event.read",
    "core.user.manage",
    "core.users.read",
    "core.users.create",
    "core.users.update",
    "core.users.disable",
    "core.users.delete",
    "core.role.manage",
    "core.roles.read",
    "core.roles.manage",
    "core.permission.manage",
    "core.branches.read",
    "core.branches.manage",
    "core.documents.read",
    "core.documents.manage",
    "core.signatures.read",
    "core.signatures.manage",
]


def _get_or_create_tenant(db: Session, settings: Settings) -> Tenant:
    stmt: Select[tuple[Tenant]] = select(Tenant).where(
        Tenant.slug == settings.seed_demo_tenant_slug
    )
    tenant = db.scalar(stmt)
    if tenant is not None:
        return tenant

    tenant = Tenant(name=settings.seed_demo_tenant_name, slug=settings.seed_demo_tenant_slug)
    db.add(tenant)
    db.flush()
    return tenant


def _get_or_create_branch(db: Session, tenant: Tenant, settings: Settings) -> Branch:
    stmt: Select[tuple[Branch]] = select(Branch).where(
        Branch.tenant_id == tenant.id,
        Branch.code == settings.seed_demo_branch_code,
    )
    branch = db.scalar(stmt)
    if branch is not None:
        return branch

    branch = Branch(
        tenant_id=tenant.id,
        name=settings.seed_demo_branch_name,
        code=settings.seed_demo_branch_code,
    )
    db.add(branch)
    db.flush()
    return branch


def _get_or_create_role(db: Session, tenant: Tenant, name: str, description: str) -> Role:
    stmt: Select[tuple[Role]] = select(Role).where(
        Role.tenant_id == tenant.id,
        Role.name == name,
    )
    role = db.scalar(stmt)
    if role is not None:
        return role

    role = Role(
        tenant_id=tenant.id,
        name=name,
        description=description,
    )
    db.add(role)
    db.flush()
    return role


def _get_or_create_permission(db: Session, permission_name: str) -> Permission:
    stmt: Select[tuple[Permission]] = select(Permission).where(Permission.name == permission_name)
    permission = db.scalar(stmt)
    if permission is not None:
        return permission

    permission = Permission(name=permission_name, description=f"Base permission {permission_name}")
    db.add(permission)
    db.flush()
    return permission


def _ensure_role_permission(db: Session, role_id: str, permission_id: str) -> None:
    stmt: Select[tuple[RolePermission]] = select(RolePermission).where(
        RolePermission.role_id == role_id,
        RolePermission.permission_id == permission_id,
    )
    if db.scalar(stmt) is not None:
        return

    db.add(RolePermission(role_id=role_id, permission_id=permission_id))
    db.flush()


def _get_or_create_admin_user(
    db: Session,
    tenant: Tenant,
    branch: Branch,
    settings: Settings,
) -> User:
    stmt: Select[tuple[User]] = select(User).where(User.email == settings.seed_admin_email)
    user = db.scalar(stmt)
    if user is not None:
        user.tenant_id = tenant.id
        user.full_name = settings.seed_admin_full_name
        user.is_active = True
        user.is_superadmin = True
        if not user.password_hash:
            user.password_hash = hash_password(settings.seed_admin_password)
        db.add(user)
        db.flush()
        assign_branch_to_user(db, user, branch)
        return user

    user = User(
        tenant_id=tenant.id,
        branch_id=branch.id,
        email=settings.seed_admin_email,
        full_name=settings.seed_admin_full_name,
        password_hash=hash_password(settings.seed_admin_password),
        is_active=True,
        is_superadmin=True,
    )
    db.add(user)
    db.flush()
    return user


def seed_demo_data(
    db: Session,
    settings: Settings,
    plugins: Sequence[LoadedPlugin],
) -> dict[str, str]:
    """Create demo tenant, branch, admin role, and admin user.

    Idempotent: existing records are reused. The admin role receives all
    base kernel permissions plus the permissions declared by the loaded
    plugins.
    """
    tenant = _get_or_create_tenant(db, settings)
    branch = _get_or_create_branch(db, tenant, settings)
    admin_role = _get_or_create_role(
        db,
        tenant,
        name="admin",
        description="Administrative role for demo tenant",
    )

    plugin_permissions = [
        plugin.manifest.permissions for plugin in plugins if plugin.manifest is not None
    ]
    all_permission_names = sorted(set(BASE_PERMISSIONS).union(*plugin_permissions))
    for permission_name in all_permission_names:
        permission = _get_or_create_permission(db, permission_name)
        _ensure_role_permission(db, admin_role.id, permission.id)

    user = _get_or_create_admin_user(db, tenant, branch, settings)
    assign_role_to_user(db, user=user, role=admin_role)
    sync_plugin_registry(db, list(plugins))
    db.commit()

    return {
        "tenant_id": tenant.id,
        "branch_id": branch.id,
        "role_id": admin_role.id,
        "user_id": user.id,
        "user_email": user.email,
    }
