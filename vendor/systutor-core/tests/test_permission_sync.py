"""A.SPEC 0003: the admin role must hold every kernel and plugin permission
after ANY boot, not only the first one.

The falsifiable truth: add a permission name, boot again, and the admin role
has it. Before A.SPEC 0003, entrypoint.sh skipped the seed entirely once the
admin user existed, so a new permission never reached the role.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.api import seed as seed_module
from systutor.api.seed import BASE_PERMISSIONS, ensure_seed, sync_permissions
from systutor.core.errors import AppError
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import Permission, Role, RolePermission
from systutor.kernel.tenants.context import build_tenant_context
from systutor.kernel.tenants.models import Tenant

NEW_PERMISSION = "core.testonly.permission"


def _role_permission_names(db: Session, role: Role) -> set[str]:
    stmt = (
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    return set(db.scalars(stmt).all())


def _admin_role(db: Session, user: User) -> Role:
    role = db.scalar(
        select(Role).where(Role.tenant_id == user.tenant_id, Role.name == "admin")
    )
    assert role is not None, "admin role should exist after the first boot"
    return role


def _loaded_permissions(db: Session, role: Role) -> set[str]:
    return _role_permission_names(db, role)


# --- I3: first boot behaves exactly as before -------------------------------


def test_ensure_seed_creates_on_first_boot(app, db_session: Session):
    result = ensure_seed(db_session, app.state.settings, app.state.plugin_runtime.list_results())

    assert result["status"] == "seeded"
    for key in ("tenant_id", "branch_id", "role_id", "user_id", "user_email"):
        assert key in result, f"seed_demo_data contract changed: missing {key}"

    user = db_session.scalar(select(User))
    assert user is not None
    # A.SPEC 0004: the admin's power is the role's, not a flag's.
    assert not hasattr(user, "is_superadmin"), "the flag column is back in the model"


# --- THE TRUTH: a new permission reaches the role on the second boot --------


def test_kernel_permission_reaches_admin_role_on_second_boot(app, db_session: Session):
    settings = app.state.settings
    plugins = app.state.plugin_runtime.list_results()

    first = ensure_seed(db_session, settings, plugins)
    assert first["status"] == "seeded"
    role = _admin_role(db_session, db_session.scalar(select(User)))
    assert NEW_PERMISSION not in _loaded_permissions(db_session, role), (
        "precondition: the permission must not exist before the second boot"
    )

    # A permission lands in the catalog the way a real deploy would add one.
    seed_module.BASE_PERMISSIONS.append(NEW_PERMISSION)
    try:
        second = ensure_seed(db_session, settings, plugins)
    finally:
        seed_module.BASE_PERMISSIONS.remove(NEW_PERMISSION)

    assert second["status"] == "permissions_synced"
    assert NEW_PERMISSION in _loaded_permissions(db_session, role), (
        "A.SPEC 0003 FAIL: a permission added before a boot never reached the admin role"
    )


def test_plugin_permission_reaches_admin_role_on_second_boot(app, db_session: Session):
    settings = app.state.settings
    plugins = app.state.plugin_runtime.list_results()
    ensure_seed(db_session, settings, plugins)

    user = db_session.scalar(select(User))
    role = _admin_role(db_session, user)

    plugin_permission = "mail.brand.new.thing"
    for loaded in plugins:
        if loaded.manifest is not None and plugin_permission not in loaded.manifest.permissions:
            loaded.manifest.permissions = [*loaded.manifest.permissions, plugin_permission]
            break
    else:
        pytest.skip("no plugin manifest available in this environment")

    try:
        ensure_seed(db_session, settings, plugins)
    finally:
        for loaded in plugins:
            if loaded.manifest is not None and plugin_permission in loaded.manifest.permissions:
                loaded.manifest.permissions = [
                    p for p in loaded.manifest.permissions if p != plugin_permission
                ]

    assert plugin_permission in _loaded_permissions(db_session, role), (
        "A.SPEC 0003 FAIL: a plugin-declared permission never reached the admin role"
    )


# --- I2: idempotence --------------------------------------------------------


def test_ensure_seed_is_idempotent(app, db_session: Session):
    settings = app.state.settings
    plugins = app.state.plugin_runtime.list_results()

    ensure_seed(db_session, settings, plugins)
    user = db_session.scalar(select(User))
    role = _admin_role(db_session, user)
    after_first = _loaded_permissions(db_session, role)

    rows_first = len(db_session.query(RolePermission).all())
    ensure_seed(db_session, settings, plugins)
    ensure_seed(db_session, settings, plugins)

    assert _loaded_permissions(db_session, role) == after_first
    rows_after = len(db_session.query(RolePermission).all())
    assert rows_after == rows_first, "repeated sync must not duplicate role_permission rows"


# --- I1 / I7: A.SPEC 0004 inverted the bypass test --------------------------


def test_superadmin_no_longer_bypasses_permissions(app, db_session: Session):
    """THE TRUTH of A.SPEC 0004: no flag can shortcut authorization.

    Before this A.SPEC a superadmin with zero role permissions passed
    has_permission for anything. Now permissions are the only mechanism.
    The old test was named test_superadmin_bypass_still_present; this is
    the same measurement with the opposite expected result.
    """
    from systutor.kernel.tenants.context import TenantContext

    context = TenantContext(
        current_tenant_id="t1",
        current_branch_id="b1",
        current_user_id="u1",
        current_permissions=(),
        current_warehouse_ids=None,
    )
    assert context.has_permission("a.permission.they.do.not.have") is False, (
        "A.SPEC 0004 FAIL: a flag is still short-circuiting authorization"
    )


def test_has_permission_is_pure_membership(app, db_session: Session):
    from systutor.kernel.tenants.context import TenantContext

    context = TenantContext(
        current_tenant_id="t1",
        current_branch_id="b1",
        current_user_id="u1",
        current_permissions=("core.users.read", "core.tenants.read"),
        current_warehouse_ids=None,
    )
    assert context.has_permission("core.users.read") is True
    assert context.has_permission("core.tenants.read") is True
    assert context.has_permission("core.users.delete") is False


def test_seeded_admin_keeps_full_access(app, db_session: Session):
    """I1: the guarantee that 0004 is not a lockout.

    The seeded admin holds every permission by role, so removing the flag
    costs it nothing. If this fails, the deployment must be rolled back.
    """

    settings = app.state.settings
    plugins = app.state.plugin_runtime.list_results()
    ensure_seed(db_session, settings, plugins)
    user = db_session.scalar(select(User))

    context = build_tenant_context(db_session, user)
    granted = set(BASE_PERMISSIONS)
    missing = [p for p in granted if not context.has_permission(p)]
    assert not missing, f"seeded admin lost access to: {missing}"
    # The two tenant permissions that replaced require_superadmin (A.SPEC 0004).
    assert context.has_permission("core.tenants.read") is True
    assert context.has_permission("core.tenants.manage") is True


def test_mail_cross_domain_permission_is_declared(app, db_session: Session):
    """I3: the cross-domain mail permission replaces the removed flag.

    This test environment loads fake plugins, never the real mail plugin, so
    the permission cannot be granted here. What must hold is that it is
    declared, because sync_permissions reads plugin manifests.
    """
    import json
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[1] / "plugins/mail/permissions/mail.json"
    declared = {p["name"] for p in json.loads(manifest.read_text())["permissions"]}
    assert "mail.accounts.read.all" in declared
    assert "mail.accounts.read" in declared


def test_ensure_seed_rejects_orphan_admin(app, db_session: Session):
    """An admin user pointing at a missing tenant must fail loudly, not silently.

    Without this guard the sync would create a brand new tenant and hand the
    admin a second, unrelated role, leaving the real deployment untouched.
    """
    settings = app.state.settings
    ghost = User(
        tenant_id="00000000-0000-0000-0000-000000000000",
        email=settings.seed_admin_email,
        full_name="ghost",
        password_hash="x",
        is_active=True,
    )
    db_session.add(ghost)
    db_session.flush()

    with pytest.raises(AppError) as excinfo:
        ensure_seed(db_session, settings, [])

    assert excinfo.value.code == "seed_orphan_admin"


def test_sync_permissions_returns_full_catalog(app, db_session: Session):
    ensure_seed(db_session, app.state.settings, app.state.plugin_runtime.list_results())
    user = db_session.scalar(select(User))
    tenant = db_session.get(Tenant, user.tenant_id)

    granted = sync_permissions(db_session, tenant, app.state.plugin_runtime.list_results())

    assert set(granted) == set(_loaded_permissions(db_session, _admin_role(db_session, user)))
    assert "core.users.read" in granted
