from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.api.seed import ensure_seed
from systutor.kernel.permissions.models import Role, RolePermission
from systutor.kernel.permissions.service import (
    TENANT_ADMIN_BASE_PERMISSIONS,
    ensure_permission,
    list_role_permission_names,
    sync_tenant_admin_role,
)
from systutor.kernel.tenants.models import Tenant


def test_explicit_sync_is_additive_and_idempotent(db_session: Session) -> None:
    tenant = Tenant(name="Persistent RBAC", slug="persistent-rbac")
    db_session.add(tenant)
    db_session.flush()

    sync_tenant_admin_role(
        db_session,
        tenant_id=tenant.id,
        permissions=["mail.accounts.read"],
    )
    role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "tenant_admin")
    )
    assert role is not None

    manual = ensure_permission(
        db_session,
        permission_name="core.documents.manage",
        description="Operator-managed grant",
    )
    db_session.add(RolePermission(role_id=role.id, permission_id=manual.id))
    db_session.flush()

    sync_tenant_admin_role(
        db_session,
        tenant_id=tenant.id,
        permissions=["mail.accounts.create"],
    )
    sync_tenant_admin_role(
        db_session,
        tenant_id=tenant.id,
        permissions=["mail.accounts.create"],
    )

    granted = set(list_role_permission_names(db_session, role_id=role.id))
    assert set(TENANT_ADMIN_BASE_PERMISSIONS) <= granted
    assert {
        "mail.accounts.read",
        "mail.accounts.create",
        "core.documents.manage",
    } <= granted
    rows = list(
        db_session.scalars(
            select(RolePermission).where(RolePermission.role_id == role.id)
        )
    )
    assert len(rows) == len({row.permission_id for row in rows})


def test_application_boot_does_not_rewrite_existing_tenant_role(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant = Tenant(name="Restart safe", slug="restart-safe")
    db_session.add(tenant)
    db_session.flush()
    sync_tenant_admin_role(
        db_session,
        tenant_id=tenant.id,
        permissions=["mail.accounts.read"],
    )
    role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "tenant_admin")
    )
    assert role is not None
    db_session.query(RolePermission).filter(RolePermission.role_id == role.id).delete()
    manual = ensure_permission(
        db_session,
        permission_name="core.documents.manage",
        description="Only operator-selected permission",
    )
    db_session.add(RolePermission(role_id=role.id, permission_id=manual.id))
    db_session.flush()

    ensure_seed(
        db_session,
        app.state.settings,
        app.state.plugin_runtime.list_results(),
    )

    assert list_role_permission_names(db_session, role_id=role.id) == [
        "core.documents.manage"
    ]
