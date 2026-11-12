#!/usr/bin/env python3
"""Grant explicit RBAC permissions to one tenant through a dedicated role.

The role is intentionally NOT `tenant_admin`: the core boot sync replaces the
`tenant_admin` permission set on every start. A separate role survives restarts
and can be assigned to every active user of the tenant or to selected emails.

Usage:
    python3 scripts/grant_tenant_permission.py --tenant milton --dry-run
    python3 scripts/grant_tenant_permission.py --tenant milton --email ops@example.com
    python3 scripts/grant_tenant_permission.py --tenant milton \
        --permission apps.applications.read \
        --permission apps.applications.manage \
        --permission apps.deployments.create

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
from systutor.kernel.permissions.service import ensure_permission
from systutor.kernel.tenants.models import Tenant

DEFAULT_PERMISSION = "apps.deployments.create"
DEFAULT_ROLE = "apps_deployer"
MANAGED_ROLE = "tenant_admin"


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
    if existing is not None:
        return False
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    return True


def grant(
    db: Session,
    *,
    tenant_value: str,
    permissions: list[str],
    role_name: str,
    emails: list[str],
    dry_run: bool,
) -> dict[str, object]:
    tenant = _resolve_tenant(db, tenant_value)
    if role_name == MANAGED_ROLE:
        raise GrantError(
            "tenant_admin is rebuilt on every boot; use a separate role name"
        )
    users = _resolve_users(db, tenant, emails)
    if not users:
        raise GrantError(f"tenant {tenant.slug!r} has no active users to assign")

    role, role_created = _get_or_create_role(db, tenant.id, role_name)
    resolved: list[Permission] = []
    permissions_added: list[str] = []
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

    result: dict[str, object] = {
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        "role": {
            "id": role.id,
            "name": role.name,
            "created": role_created,
        },
        "permissions": [permission.name for permission in resolved],
        "permissions_added": permissions_added,
        "users": [user.email for user in users],
        "users_assigned": users_assigned,
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
            )
        except GrantError as exc:
            print(f"FAILED: {exc}")
            return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
