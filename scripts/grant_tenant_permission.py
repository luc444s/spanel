#!/usr/bin/env python3
"""Grant explicit RBAC permissions to one tenant through a dedicated role.

The role is intentionally NOT `tenant_admin`: the core boot sync replaces the
`tenant_admin` permission set on every start. A separate role survives restarts
and can be assigned to every active user of the tenant or to selected emails.
Because tenant users have exactly one role, assigning this role replaces each
selected user's previous role; use `--dry-run` before applying.

Usage:
    python3 scripts/grant_tenant_permission.py --tenant milton --dry-run
    python3 scripts/grant_tenant_permission.py --tenant milton --email ops@example.com
    python3 scripts/grant_tenant_permission.py --tenant milton \
        --permission apps.applications.read \
        --permission apps.applications.manage \
        --permission apps.deployments.create

Remove platform admin from one user while keeping the rest of the admin role:

    python3 scripts/grant_tenant_permission.py --tenant milton \
        --email ops@example.com \
        --role tenant_operator \
        --clone-platform-role \
        --remove-platform-roles

Exit codes: 0 ok, 1 failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

import systutor.kernel.models  # noqa: F401
from systutor.core.config import Settings, env_settings_kwargs
from systutor.core.database import build_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import (
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from systutor.kernel.permissions.service import (
    assign_role_to_user,
    ensure_permission,
    remove_role_from_user,
)
from systutor.kernel.tenants.models import Tenant

DEFAULT_PERMISSION = "apps.deployments.create"
DEFAULT_ROLE = "apps_deployer"
MANAGED_ROLE = "tenant_admin"
# Endpoints behind these permissions still require core.platform.admin. Keeping
# them in a cloned tenant role would only render dead UI and 403 responses.
PLATFORM_ONLY_PERMISSIONS = {
    "core.platform.admin",
    "apps.applications.delete",
    "hosting.sites.delete",
    "hosting.profiles.manage",
    "tenant.tenants.read",
    "tenant.tenants.manage",
}


class GrantError(Exception):
    """A failure explained to the operator, not a traceback."""


def _resolve_tenant(db: Session, value: str) -> Tenant:
    tenant = db.scalar(
        select(Tenant).where(or_(Tenant.slug == value, Tenant.id == value))
    )
    if tenant is None:
        raise GrantError(f"unknown tenant {value!r}")
    return tenant


def _resolve_users(db: Session, tenant: Tenant, emails: list[str]) -> list[User]:
    stmt = select(User).where(User.tenant_id == tenant.id, User.is_active.is_(True))
    users = list(db.scalars(stmt.order_by(User.email.asc())).all())
    if not emails:
        return users

    wanted = {email.strip().lower() for email in emails if email.strip()}
    selected = [user for user in users if user.email.lower() in wanted]
    missing = wanted - {user.email.lower() for user in selected}
    if missing:
        raise GrantError(
            f"users not found in tenant {tenant.slug!r}: {', '.join(sorted(missing))}"
        )
    return selected


def _get_or_create_role(db: Session, tenant_id: str, name: str) -> tuple[Role, bool]:
    role = db.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == name)
    )
    if role is not None:
        if not role.is_active:
            role.is_active = True
            db.add(role)
            db.flush()
        return role, False
    role = Role(
        tenant_id=tenant_id,
        name=name,
        description="Explicit RBAC grants for this tenant",
        is_active=True,
    )
    db.add(role)
    db.flush()
    return role, True


def _grant_permission(
    db: Session, role: Role, permission: Permission
) -> bool:
    existing = db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
    )
    if existing is not None:
        return False
    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()
    return True


def _assign_role(db: Session, user: User, role: Role) -> bool:
    existing = db.scalar(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.role_id == role.id,
        )
    )
    current = list(db.scalars(select(UserRole).where(UserRole.user_id == user.id)))
    if existing is not None and len(current) == 1:
        return False
    assign_role_to_user(db, user=user, role=role)
    return True


def _roles_with_permission(
    db: Session, tenant_id: str, permission_name: str
) -> list[Role]:
    stmt = (
        select(Role)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            Role.tenant_id == tenant_id,
            Permission.name == permission_name,
        )
        .order_by(Role.name.asc())
    )
    return list(db.scalars(stmt).all())


def _role_permissions(db: Session, role_id: str) -> list[Permission]:
    stmt = (
        select(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.name.asc())
    )
    return list(db.scalars(stmt).all())


def _clone_permissions(
    db: Session,
    tenant_id: str,
    target: Role,
    source_name: str,
    exclude: set[str],
) -> list[str]:
    source = db.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == source_name)
    )
    if source is None:
        raise GrantError(f"unknown source role {source_name!r}")
    added: list[str] = []
    for permission in _role_permissions(db, source.id):
        if permission.name in exclude:
            continue
        if _grant_permission(db, target, permission):
            added.append(permission.name)
    return added


def _remove_role_from_users(db: Session, users: list[User], role: Role) -> list[str]:
    removed: list[str] = []
    for user in users:
        if remove_role_from_user(db, user=user, role=role):
            removed.append(user.email)
    db.flush()
    return removed


def grant(
    db: Session,
    *,
    tenant_value: str,
    permissions: list[str],
    role_name: str,
    emails: list[str],
    dry_run: bool,
    clone_role: str | None = None,
    clone_platform_role: bool = False,
    exclude_permissions: list[str] | None = None,
    remove_roles: list[str] | None = None,
    remove_platform_roles: bool = False,
) -> dict[str, object]:
    tenant = _resolve_tenant(db, tenant_value)
    if role_name == MANAGED_ROLE:
        raise GrantError(
            "tenant_admin is rebuilt on every boot; use a separate role name"
        )
    users = _resolve_users(db, tenant, emails)
    if not users:
        raise GrantError(f"tenant {tenant.slug!r} has no active users to assign")

    if clone_platform_role:
        platform_roles = _roles_with_permission(
            db, tenant.id, "core.platform.admin"
        )
        if not platform_roles:
            raise GrantError("no role in this tenant grants core.platform.admin")
        if len(platform_roles) > 1:
            names = ", ".join(role.name for role in platform_roles)
            raise GrantError(
                f"multiple roles grant core.platform.admin ({names}); "
                "use --clone-role to choose one"
            )
        clone_role = platform_roles[0].name

    role, role_created = _get_or_create_role(db, tenant.id, role_name)
    resolved: list[Permission] = []
    permissions_added: list[str] = []
    cloned: list[str] = []
    if clone_role:
        excluded = PLATFORM_ONLY_PERMISSIONS | set(exclude_permissions or [])
        cloned = _clone_permissions(db, tenant.id, role, clone_role, excluded)

    for permission_name in permissions:
        permission = ensure_permission(
            db,
            permission_name=permission_name,
            description=f"Granted explicitly to tenant {tenant.slug}",
        )
        resolved.append(permission)
        if _grant_permission(db, role, permission):
            permissions_added.append(permission_name)

    users_assigned: list[str] = []
    for user in users:
        if _assign_role(db, user, role):
            users_assigned.append(user.email)

    roles_to_remove: dict[str, Role] = {}
    if remove_platform_roles:
        for platform_role in _roles_with_permission(
            db, tenant.id, "core.platform.admin"
        ):
            roles_to_remove[platform_role.name] = platform_role
    for remove_name in remove_roles or []:
        role_to_remove = db.scalar(
            select(Role).where(
                Role.tenant_id == tenant.id,
                Role.name == remove_name,
            )
        )
        if role_to_remove is None:
            raise GrantError(f"unknown role to remove {remove_name!r}")
        roles_to_remove[remove_name] = role_to_remove

    roles_removed: dict[str, list[str]] = {}
    for remove_name, role_to_remove in roles_to_remove.items():
        if remove_name == role_name:
            raise GrantError("cannot remove the role being granted")
        roles_removed[remove_name] = _remove_role_from_users(
            db, users, role_to_remove
        )

    result: dict[str, object] = {
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        "role": {
            "id": role.id,
            "name": role.name,
            "created": role_created,
        },
        "cloned_from": clone_role,
        "permissions_cloned": cloned,
        "permissions": [permission.name for permission in resolved],
        "permissions_added": permissions_added,
        "users": [user.email for user in users],
        "users_assigned": users_assigned,
        "roles_removed": roles_removed,
        "dry_run": dry_run,
    }

    record_audit(
        db,
        tenant_id=tenant.id,
        branch_id=None,
        actor_user_id=None,
        actor_type="system",
        module="core",
        action="rbac.permissions.granted",
        entity_type="role",
        entity_id=role.id,
        result="success",
        correlation_id=None,
        request_id=None,
        details={
            "role": role.name,
            "permissions": result["permissions"],
            "users": result["users"],
            "cloned_from": clone_role,
            "roles_removed": sorted(roles_removed),
            "dry_run": dry_run,
        },
    )

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tenant", required=True, help="tenant slug or id")
    parser.add_argument(
        "--permission",
        action="append",
        dest="permissions",
        default=None,
        help=f"permission to grant (repeatable; default: {DEFAULT_PERMISSION})",
    )
    parser.add_argument(
        "--role",
        default=DEFAULT_ROLE,
        help=f"role that holds the grants (default: {DEFAULT_ROLE})",
    )
    parser.add_argument(
        "--email",
        action="append",
        default=[],
        help="assign the role only to this email (repeatable; default: all active users)",
    )
    parser.add_argument(
        "--clone-role",
        default=None,
        help="copy permissions from an existing role in the same tenant",
    )
    parser.add_argument(
        "--clone-platform-role",
        action="store_true",
        help="clone the only role in the tenant that grants core.platform.admin",
    )
    parser.add_argument(
        "--exclude-permission",
        action="append",
        dest="exclude_permissions",
        default=None,
        help="extra permission skipped when cloning (platform-only ones are always skipped)",
    )
    parser.add_argument(
        "--remove-role",
        action="append",
        dest="remove_roles",
        default=[],
        help="remove this role from the selected users (repeatable)",
    )
    parser.add_argument(
        "--remove-platform-roles",
        action="store_true",
        help="remove every role granting core.platform.admin from the selected users",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the result but roll back every write",
    )
    args = parser.parse_args(argv)

    settings = Settings(**env_settings_kwargs())
    session_factory = build_session_factory(settings)
    with session_factory() as db:
        try:
            result = grant(
                db,
                tenant_value=args.tenant,
                permissions=args.permissions or [DEFAULT_PERMISSION],
                role_name=args.role,
                emails=args.email,
                dry_run=args.dry_run,
                clone_role=args.clone_role,
                clone_platform_role=args.clone_platform_role,
                exclude_permissions=args.exclude_permissions,
                remove_roles=args.remove_roles,
                remove_platform_roles=args.remove_platform_roles,
            )
        except GrantError as exc:
            print(f"FAILED: {exc}")
            return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
