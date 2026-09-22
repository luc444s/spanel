from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.kernel.audit.models import AuditLog
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.events.models import EventLog
from systutor.kernel.permissions.models import Role, RolePermission
from systutor.kernel.permissions.service import assign_role_to_user, ensure_permission
from systutor.kernel.tenants.models import Branch, Tenant
from systutor.kernel.tenants.service import create_branch_for_tenant


def login(client, email: str = "admin@example.com", password: str = "ChangeMe123!"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def auth_headers(
    client,
    email: str = "admin@example.com",
    password: str = "ChangeMe123!",
) -> dict[str, str]:
    response = login(client, email=email, password=password)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_tenant(db: Session, *, name: str, slug: str) -> Tenant:
    tenant = Tenant(name=name, slug=slug)
    db.add(tenant)
    db.flush()
    return tenant


def create_branch(db: Session, *, tenant: Tenant, name: str, code: str) -> Branch:
    return create_branch_for_tenant(db, tenant_id=tenant.id, name=name, code=code, is_active=True)


def create_role(db: Session, *, tenant: Tenant, name: str, permission_names: list[str]) -> Role:
    role = Role(tenant_id=tenant.id, name=name, description=None, is_active=True)
    db.add(role)
    db.flush()
    for permission_name in permission_names:
        permission = ensure_permission(db, permission_name=permission_name)
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()
    return role


def create_user(
    db: Session,
    *,
    tenant: Tenant,
    branch: Branch | None,
    email: str,
    password: str,
    full_name: str,
    role: Role | None = None,
) -> User:
    user = User(
        tenant_id=tenant.id,
        branch_id=branch.id if branch is not None else None,
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    db.flush()
    if role is not None:
        assign_role_to_user(db, user=user, role=role)
    return user


def test_core_users_crud_and_disable_blocks_auth(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant = db_session.get(Tenant, seeded_demo["tenant_id"])
    assert tenant is not None
    role = create_role(
        db_session,
        tenant=tenant,
        name="operator-users",
        permission_names=["core.users.read"],
    )
    db_session.commit()

    headers = auth_headers(client)
    create_response = client.post(
        "/api/v1/core/users",
        headers=headers,
        json={
            "name": "User One",
            "email": "user-one@example.com",
            "password": "UserOne123!",
            "branch_id": seeded_demo["branch_id"],
            "role_ids": [role.id],
            "warehouse_ids": ["warehouse-a", "warehouse-b"],
        },
    )
    assert create_response.status_code == 201
    payload = create_response.json()
    assert payload["name"] == "User One"
    assert payload["roles"] == ["operator-users"]
    assert payload["warehouse_ids"] == ["warehouse-a", "warehouse-b"]
    assert "password_hash" not in payload

    user_id = payload["id"]
    update_response = client.patch(
        f"/api/v1/core/users/{user_id}",
        headers=headers,
        json={"name": "User One Updated", "email": "user-one-updated@example.com"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "User One Updated"

    disable_response = client.post(f"/api/v1/core/users/{user_id}/disable", headers=headers)
    assert disable_response.status_code == 200
    assert disable_response.json()["active"] is False
    login_after_disable = login(
        client,
        email="user-one-updated@example.com",
        password="UserOne123!",
    )
    assert login_after_disable.status_code == 401

    enable_response = client.post(f"/api/v1/core/users/{user_id}/enable", headers=headers)
    assert enable_response.status_code == 200
    assert enable_response.json()["active"] is True
    login_after_enable = login(
        client,
        email="user-one-updated@example.com",
        password="UserOne123!",
    )
    assert login_after_enable.status_code == 200
    assert login_after_enable.json()["user"]["warehouse_ids"] == ["warehouse-a", "warehouse-b"]

    me_response = client.get(
        "/api/v1/auth/me",
        headers=auth_headers(
            client,
            email="user-one-updated@example.com",
            password="UserOne123!",
        ),
    )
    assert me_response.status_code == 200
    assert me_response.json()["warehouse_ids"] == ["warehouse-a", "warehouse-b"]

    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_id == user_id)
            .order_by(AuditLog.occurred_at.asc())
        )
    )
    assert any(audit.action == "user.create" for audit in audits)
    assert any(audit.action == "user.disable" for audit in audits)

    events = list(db_session.scalars(select(EventLog).where(EventLog.entity_id == user_id)))
    assert any(event.event_name == "core.user.created" for event in events)
    assert any(event.event_name == "core.user.enabled" for event in events)


def test_core_users_tenant_isolation_and_cross_tenant_validation(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant_b = create_tenant(db_session, name="Tenant B", slug="tenant-b-mgmt")
    branch_b = create_branch(db_session, tenant=tenant_b, name="B Branch", code="B01")
    role_b = create_role(
        db_session,
        tenant=tenant_b,
        name="external-role",
        permission_names=["core.users.read"],
    )
    user_b = create_user(
        db_session,
        tenant=tenant_b,
        branch=branch_b,
        email="tenant-b-user@example.com",
        password="TenantB123!",
        full_name="Tenant B User",
    )
    db_session.commit()

    headers = auth_headers(client)
    list_response = client.get("/api/v1/core/users", headers=headers)
    assert all(item["id"] != user_b.id for item in list_response.json())

    detail_response = client.get(f"/api/v1/core/users/{user_b.id}", headers=headers)
    assert detail_response.status_code == 404

    branch_fail = client.post(
        "/api/v1/core/users",
        headers=headers,
        json={
            "name": "Cross Branch",
            "email": "cross-branch@example.com",
            "password": "CrossBranch123!",
            "branch_id": branch_b.id,
            "role_ids": [],
        },
    )
    assert branch_fail.status_code == 400

    role_fail = client.post(
        "/api/v1/core/users",
        headers=headers,
        json={
            "name": "Cross Role",
            "email": "cross-role@example.com",
            "password": "CrossRole123!",
            "role_ids": [role_b.id],
        },
    )
    assert role_fail.status_code == 400


def test_core_roles_and_branches_management(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    headers = auth_headers(client)

    role_response = client.post(
        "/api/v1/core/roles",
        headers=headers,
        json={"name": "dispatcher", "permission_names": ["core.users.read", "core.plugin.read"]},
    )
    assert role_response.status_code == 201
    role_id = role_response.json()["id"]
    assert sorted(role_response.json()["permissions"]) == ["core.plugin.read", "core.users.read"]

    role_update = client.patch(
        f"/api/v1/core/roles/{role_id}",
        headers=headers,
        json={"permission_names": ["core.users.read", "core.plugin.runtime.read"]},
    )
    assert role_update.status_code == 200
    assert "core.plugin.runtime.read" in role_update.json()["permissions"]

    role_disable = client.post(f"/api/v1/core/roles/{role_id}/disable", headers=headers)
    assert role_disable.status_code == 200
    assert role_disable.json()["active"] is False

    role_enable = client.post(f"/api/v1/core/roles/{role_id}/enable", headers=headers)
    assert role_enable.status_code == 200
    assert role_enable.json()["active"] is True

    branch_response = client.post(
        "/api/v1/core/branches",
        headers=headers,
        json={"name": "North", "code": "NORTH"},
    )
    assert branch_response.status_code == 201
    branch_id = branch_response.json()["id"]

    branch_update = client.patch(
        f"/api/v1/core/branches/{branch_id}",
        headers=headers,
        json={"name": "North Updated", "code": "NORTH2"},
    )
    assert branch_update.status_code == 200
    assert branch_update.json()["name"] == "North Updated"

    branch_disable = client.post(f"/api/v1/core/branches/{branch_id}/disable", headers=headers)
    assert branch_disable.status_code == 200
    assert branch_disable.json()["active"] is False

    branch_enable = client.post(f"/api/v1/core/branches/{branch_id}/enable", headers=headers)
    assert branch_enable.status_code == 200
    assert branch_enable.json()["active"] is True

    tenant_b = create_tenant(db_session, name="Tenant Extra", slug="tenant-extra-mgmt")
    role_b = create_role(db_session, tenant=tenant_b, name="tenant-b-role", permission_names=[])
    branch_b = create_branch(db_session, tenant=tenant_b, name="Tenant B Branch", code="TB1")
    db_session.commit()

    assert client.get(f"/api/v1/core/roles/{role_b.id}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/core/branches/{branch_b.id}", headers=headers).status_code == 404

    audit_actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.entity_id.in_([role_id, branch_id]))
        )
    )
    assert "role.update" in audit_actions
    assert "branch.disable" in audit_actions

    event_names = list(
        db_session.scalars(
            select(EventLog.event_name).where(EventLog.entity_id.in_([role_id, branch_id]))
        )
    )
    assert "core.role.created" in event_names
    assert "core.branch.created" in event_names


def test_core_plugins_management_permissions_and_lifecycle(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant = db_session.get(Tenant, seeded_demo["tenant_id"])
    branch = db_session.get(Branch, seeded_demo["branch_id"])
    assert tenant is not None
    assert branch is not None

    runtime_reader_role = create_role(
        db_session,
        tenant=tenant,
        name="runtime-reader",
        permission_names=["core.plugin.runtime.read"],
    )
    runtime_reader = create_user(
        db_session,
        tenant=tenant,
        branch=branch,
        email="runtime-reader@example.com",
        password="Runtime123!",
        full_name="Runtime Reader",
        role=runtime_reader_role,
    )
    db_session.commit()
    assert runtime_reader.id

    admin_headers = auth_headers(client)
    reader_headers = auth_headers(
        client,
        email="runtime-reader@example.com",
        password="Runtime123!",
    )

    list_response = client.get("/api/v1/core/plugins", headers=reader_headers)
    assert list_response.status_code == 200
    assert any(item["plugin_id"] == "alpha" for item in list_response.json())

    forbidden_enable = client.post(
        "/api/v1/core/plugins/alpha/enable",
        headers=reader_headers,
    )
    assert forbidden_enable.status_code == 403

    crm_install_response = client.post("/api/v1/core/plugins/beta/install", headers=admin_headers)
    assert crm_install_response.status_code == 200
    crm_enable_response = client.post("/api/v1/core/plugins/beta/enable", headers=admin_headers)
    assert crm_enable_response.status_code == 200

    install_response = client.post("/api/v1/core/plugins/alpha/install", headers=admin_headers)
    assert install_response.status_code == 200

    enable_response = client.post("/api/v1/core/plugins/alpha/enable", headers=admin_headers)
    assert enable_response.status_code == 200
    assert enable_response.json()["state"] == "enabled"

    disable_response = client.post("/api/v1/core/plugins/alpha/disable", headers=admin_headers)
    assert disable_response.status_code == 200
    assert disable_response.json()["state"] == "disabled"

    migrate_response = client.post(
        "/api/v1/core/plugins/alpha/migrate",
        headers=admin_headers,
        json={},
    )
    assert migrate_response.status_code == 200

    uninstall_response = client.post(
        "/api/v1/core/plugins/alpha/uninstall",
        headers=admin_headers,
    )
    assert uninstall_response.status_code == 200
    assert uninstall_response.json()["state"] == "uninstalled"

    plugin_events = list(
        db_session.scalars(
            select(EventLog.event_name).where(
                EventLog.entity_id == "alpha",
                EventLog.event_name.in_(["core.plugin.enabled", "core.plugin.installed"]),
            )
        )
    )
    assert "core.plugin.enabled" in plugin_events

    plugin_audits = list(
        db_session.scalars(
            select(AuditLog.action).where(
                AuditLog.entity_id == "alpha",
                AuditLog.action == "plugin.enable",
            )
        )
    )
    assert "plugin.enable" in plugin_audits
