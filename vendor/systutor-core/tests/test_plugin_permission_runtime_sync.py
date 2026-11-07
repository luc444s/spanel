"""A.SPEC 0045: a permission declared in a plugin manifest must reach the
permissions catalog and the platform-admin roles at the next boot, without
re-running install/enable and without manual SQL.

The falsifiable truth: `sync_plugin_registry` (the call at seed.py:285) is
extended to (a) ensure every manifest permission exists in the catalog and
(b) grant `manifest.platform_permissions` additively to every role already
holding `core.platform.admin`. Additive-only, idempotent, never revokes.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from systutor.api.seed import ensure_seed
from systutor.contracts.plugins import PluginManifestContract
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import Permission, Role, RolePermission
from systutor.kernel.plugins.models import PluginRegistry
from systutor.kernel.plugins.service import sync_plugin_registry

PLATFORM_PERMISSION = "alpha.sample.extra"


def _admin_role(db: Session, tenant_id: str) -> Role:
    role = db.scalar(select(Role).where(Role.tenant_id == tenant_id, Role.name == "admin"))
    assert role is not None, "admin role should exist after the first boot"
    return role


def _role_permission_names(db: Session, role: Role) -> set[str]:
    stmt = (
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id)
    )
    return set(db.scalars(stmt).all())


def _permission_names(db: Session) -> set[str]:
    return set(db.scalars(select(Permission.name)).all())


def _sync_audits(db: Session) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.action == "plugin.permissions.synced")
        ).all()
    )


def _seeded_plugins(app, db_session: Session):
    settings = app.state.settings
    plugins = app.state.plugin_runtime.list_results()
    ensure_seed(db_session, settings, plugins)
    db_session.commit()
    return plugins


def _boot(db: Session, plugins) -> None:
    sync_plugin_registry(db, list(plugins))
    db.commit()


def _second_role_without_platform_admin(db: Session, tenant_id: str) -> Role:
    """A role that mimics a tenant administrator: no core.platform.admin."""
    helper = Role(
        tenant_id=tenant_id,
        name="tenant_helper",
        description="No platform capability",
        is_active=True,
    )
    db.add(helper)
    db.flush()
    return helper


# --- I3: legacy manifests keep parsing, with the field defaulting to [] ----


def test_manifest_without_platform_permissions_defaults_to_empty():
    manifest = PluginManifestContract(
        id="demo",
        name="Demo",
        version="0.1.0",
        api_version="1",
        backend_entrypoint="backend.plugin:register",
        frontend_entrypoint="frontend/register.ts",
        permissions=["demo.sample.read"],
        tenant_admin_permissions=["demo.sample.read"],
        events=[],
        description="legacy manifest",
    )
    assert manifest.platform_permissions == []


def test_real_hosting_manifest_declares_platform_permissions():
    import json
    from pathlib import Path

    plugin_json = (
        Path(__file__).resolve().parents[1] / "plugins/hosting/plugin.json"
    )
    manifest = json.loads(plugin_json.read_text())
    assert "hosting.sites.delete" in manifest["platform_permissions"]
    assert "hosting.profiles.manage" in manifest["platform_permissions"]
    # INVARIANT I6: these must NOT become tenant-admin grants.
    assert not set(manifest["platform_permissions"]) & set(
        manifest["tenant_admin_permissions"]
    )


# --- THE TRUTH: a new permission reaches catalog + platform roles at boot --


def test_boot_syncs_new_permission_into_catalog(app, db_session: Session):
    plugins = _seeded_plugins(app, db_session)
    assert PLATFORM_PERMISSION not in _permission_names(db_session)

    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.permissions = [*target.manifest.permissions, PLATFORM_PERMISSION]
    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.permissions = [
            p for p in target.manifest.permissions if p != PLATFORM_PERMISSION
        ]

    assert db_session.scalar(
        select(Permission).where(Permission.name == PLATFORM_PERMISSION)
    ) is not None, "boot must migrate a newly declared manifest permission into the catalog"


def test_platform_permission_granted_to_platform_admin_roles_only(
    app, db_session: Session
):
    plugins = _seeded_plugins(app, db_session)
    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.platform_permissions = [PLATFORM_PERMISSION]

    user = db_session.scalar(select(User))
    assert user is not None
    admin_role = _admin_role(db_session, user.tenant_id)
    helper_role = _second_role_without_platform_admin(db_session, user.tenant_id)
    db_session.commit()

    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.platform_permissions = []

    assert PLATFORM_PERMISSION in _role_permission_names(db_session, admin_role), (
        "A role holding core.platform.admin must receive the platform permission"
    )
    assert PLATFORM_PERMISSION not in _role_permission_names(db_session, helper_role), (
        "A role without core.platform.admin must not receive the platform permission"
    )


def test_tenant_admin_role_does_not_gain_platform_permissions(
    app, db_session: Session
):
    plugins = _seeded_plugins(app, db_session)
    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.platform_permissions = [PLATFORM_PERMISSION]

    user = db_session.scalar(select(User))
    assert user is not None
    tenant_admin = Role(
        tenant_id=user.tenant_id,
        name="tenant_admin",
        description="Tenant administrator",
        is_active=True,
    )
    db_session.add(tenant_admin)
    db_session.flush()

    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.platform_permissions = []

    assert tenant_admin.id != _admin_role(db_session, user.tenant_id).id
    assert PLATFORM_PERMISSION not in _role_permission_names(db_session, tenant_admin), (
        "tenant admins must not gain plugin platform permissions"
    )
    assert "alpha.sample.read" not in _role_permission_names(db_session, tenant_admin)


# --- I4: no-op boot writes nothing; a real change audits exactly once ------


def test_noop_boot_writes_nothing_and_no_audit(app, db_session: Session):
    plugins = _seeded_plugins(app, db_session)
    first = _sync_audits(db_session)  # first boot may legitimately add grants
    first_permissions = _permission_names(db_session)
    first_rows = db_session.scalar(select(func.count()).select_from(RolePermission))

    _boot(db_session, plugins)

    assert len(_sync_audits(db_session)) == len(first), (
        "A repeated boot without changes must not audit again"
    )
    assert _permission_names(db_session) == first_permissions
    assert (
        db_session.scalar(select(func.count()).select_from(RolePermission)) == first_rows
    ), "a no-op boot must not add role_permission rows"


def test_sync_added_audits_once_with_details(app, db_session: Session):
    plugins = _seeded_plugins(app, db_session)
    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.platform_permissions = [PLATFORM_PERMISSION]
    db_session.commit()

    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.platform_permissions = []

    audits = [
        audit
        for audit in _sync_audits(db_session)
        if audit.details.get("plugin_id") == "alpha"
        and PLATFORM_PERMISSION in audit.details.get("added", [])
    ]
    assert len(audits) == 1, f"expected exactly one sync audit, found {len(audits)}"
    details = audits[0].details
    assert sorted(details["added"]) == [PLATFORM_PERMISSION]
    assert audits[0].actor_type == "system"


# --- I1: idempotency — a second boot never duplicates rows -----------------


def test_second_boot_does_not_duplicate_rows(app, db_session: Session):
    plugins = _seeded_plugins(app, db_session)
    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.platform_permissions = [PLATFORM_PERMISSION]
    db_session.commit()

    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.platform_permissions = []

    user = db_session.scalar(select(User))
    assert user is not None
    admin_role = _admin_role(db_session, user.tenant_id)
    granted = _role_permission_names(db_session, admin_role)
    rows = db_session.scalar(select(func.count()).select_from(RolePermission))

    _boot(db_session, plugins)

    assert _role_permission_names(db_session, admin_role) == granted
    assert (
        db_session.scalar(select(func.count()).select_from(RolePermission)) == rows
    ), "a second boot must not duplicate role_permission rows"


# --- I5: the sync does not touch plugin state or lifecycle ------------------


def test_sync_keeps_plugin_enabled_state(app, db_session: Session):
    plugins = _seeded_plugins(app, db_session)
    target = next(p for p in plugins if p.manifest is not None and p.manifest.id == "alpha")
    target.manifest.platform_permissions = [PLATFORM_PERMISSION]
    db_session.commit()

    try:
        _boot(db_session, plugins)
    finally:
        target.manifest.platform_permissions = []

    record = db_session.scalar(
        select(PluginRegistry).where(PluginRegistry.plugin_id == "alpha")
    )
    assert record is not None
    state_before = (record.state, record.is_enabled)
    _boot(db_session, plugins)
    assert (record.state, record.is_enabled) == state_before, (
        "the permission sync must not change plugin state"
    )
