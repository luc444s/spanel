from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.dependencies import require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import create_access_token, hash_password
from systutor.kernel.auth.service import get_user_by_id, get_user_for_tenant
from systutor.kernel.events.service import emit_event, record_event
from systutor.kernel.permissions.models import Permission, Role, RolePermission, UserRole
from systutor.kernel.permissions.service import assign_role_to_user, list_user_permissions
from systutor.kernel.tenants.models import Branch, Tenant
from systutor.kernel.tenants.service import (
    TenantScopeError,
    assign_branch_to_user,
    list_audit_logs_for_tenant,
    list_event_logs_for_tenant,
    list_outbox_events_for_tenant,
)

MANAGE_USERS_PERMISSION = "core.user.manage"
MANAGE_USERS_REQUIREMENT = Depends(require_permission(MANAGE_USERS_PERMISSION))


def login(client, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def create_tenant(db: Session, *, name: str, slug: str) -> Tenant:
    tenant = Tenant(name=name, slug=slug)
    db.add(tenant)
    db.flush()
    return tenant


def create_branch(db: Session, *, tenant: Tenant, name: str, code: str) -> Branch:
    branch = Branch(tenant_id=tenant.id, name=name, code=code)
    db.add(branch)
    db.flush()
    return branch


def create_user(
    db: Session,
    *,
    tenant: Tenant,
    branch: Branch | None,
    email: str,
    password: str,
    full_name: str,
    is_active: bool = True,
) -> User:
    user = User(
        tenant_id=tenant.id,
        branch_id=branch.id if branch is not None else None,
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=is_active,
        is_superadmin=False,
    )
    db.add(user)
    db.flush()
    return user


def create_role(db: Session, *, tenant: Tenant, name: str) -> Role:
    role = Role(tenant_id=tenant.id, name=name, description=f"Role {name}")
    db.add(role)
    db.flush()
    return role


def get_permission(db: Session, name: str) -> Permission:
    stmt: Select[tuple[Permission]] = select(Permission).where(Permission.name == name)
    permission = db.scalar(stmt)
    assert permission is not None
    return permission


def assign_permission_to_role(db: Session, *, role: Role, permission: Permission) -> None:
    stmt: Select[tuple[RolePermission]] = select(RolePermission).where(
        RolePermission.role_id == role.id,
        RolePermission.permission_id == permission.id,
    )
    if db.scalar(stmt) is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        db.flush()


def build_token(
    app,
    user: User,
    *,
    tenant_id: str | None = None,
    branch_id: str | None = None,
) -> str:
    return create_access_token(
        settings=app.state.settings,
        subject=user.id,
        email=user.email,
        tenant_id=tenant_id or user.tenant_id,
        branch_id=user.branch_id if branch_id is None else branch_id,
        is_superadmin=user.is_superadmin,
    )


def add_manage_users_probe(app) -> None:
    if any(
        getattr(route, "path", None) == "/api/v1/test/tenant-permissions"
        for route in app.router.routes
    ):
        return

    @app.get("/api/v1/test/tenant-permissions")
    def tenant_permissions_probe(_: User = MANAGE_USERS_REQUIREMENT):
        return {"status": "ok"}


def test_user_from_tenant_a_cannot_resolve_user_from_tenant_b(
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant_b = create_tenant(db_session, name="Tenant B", slug="tenant-b")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="B01")
    user_b = create_user(
        db_session,
        tenant=tenant_b,
        branch=branch_b,
        email="tenant-b-user@example.com",
        password="TenantB123!",
        full_name="Tenant B User",
    )
    db_session.commit()

    resolved = get_user_for_tenant(
        db_session,
        tenant_id=seeded_demo["tenant_id"],
        user_id=user_b.id,
    )

    assert resolved is None


def test_permissions_do_not_cross_tenants(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    add_manage_users_probe(app)
    tenant_a = db_session.get(Tenant, seeded_demo["tenant_id"])
    assert tenant_a is not None
    branch_a = db_session.get(Branch, seeded_demo["branch_id"])
    assert branch_a is not None

    user_a = create_user(
        db_session,
        tenant=tenant_a,
        branch=branch_a,
        email="tenant-a-viewer@example.com",
        password="Viewer123!",
        full_name="Tenant A Viewer",
    )
    tenant_b = create_tenant(db_session, name="Tenant B", slug="tenant-b-permissions")
    role_b = create_role(db_session, tenant=tenant_b, name="admin-b")
    permission = get_permission(db_session, MANAGE_USERS_PERMISSION)
    assign_permission_to_role(db_session, role=role_b, permission=permission)
    db_session.add(UserRole(user_id=user_a.id, role_id=role_b.id))
    db_session.commit()

    assert list_user_permissions(db_session, user_id=user_a.id, tenant_id=tenant_a.id) == []

    token = build_token(app, user_a)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/test/tenant-permissions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_permissions_work_inside_same_tenant(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    add_manage_users_probe(app)
    tenant_a = db_session.get(Tenant, seeded_demo["tenant_id"])
    assert tenant_a is not None
    branch_a = db_session.get(Branch, seeded_demo["branch_id"])
    assert branch_a is not None

    user_a = create_user(
        db_session,
        tenant=tenant_a,
        branch=branch_a,
        email="tenant-a-manager@example.com",
        password="Manager123!",
        full_name="Tenant A Manager",
    )
    role_a = create_role(db_session, tenant=tenant_a, name="manager-a")
    permission = get_permission(db_session, MANAGE_USERS_PERMISSION)
    assign_permission_to_role(db_session, role=role_a, permission=permission)
    assign_role_to_user(db_session, user=user_a, role=role_a)
    db_session.commit()

    assert MANAGE_USERS_PERMISSION in list_user_permissions(
        db_session,
        user_id=user_a.id,
        tenant_id=tenant_a.id,
    )

    token = build_token(app, user_a)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/test/tenant-permissions",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_branch_from_other_tenant_cannot_be_assigned(
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    user_a = get_user_by_id(db_session, seeded_demo["user_id"])
    assert user_a is not None
    tenant_b = create_tenant(db_session, name="Tenant B", slug="tenant-b-branch")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="BRB")

    with pytest.raises(TenantScopeError):
        assign_branch_to_user(db_session, user_a, branch_b)


def test_audit_log_records_actor_tenant(db_session: Session, seeded_demo: dict[str, str]) -> None:
    user_a = get_user_by_id(db_session, seeded_demo["user_id"])
    assert user_a is not None

    audit = record_audit(
        db_session,
        tenant_id=None,
        branch_id=None,
        actor_user_id=user_a.id,
        actor_type="user",
        module="core",
        action="tenant.audit",
        entity_type="test",
        entity_id="audit-tenant-a",
        result="success",
        correlation_id=None,
        request_id="req-tenant-audit",
        details={"source": "pytest"},
    )
    db_session.commit()

    assert audit.tenant_id == seeded_demo["tenant_id"]
    logs = list_audit_logs_for_tenant(db_session, seeded_demo["tenant_id"])
    assert any(item.id == audit.id for item in logs)


def test_event_log_records_actor_tenant(db_session: Session, seeded_demo: dict[str, str]) -> None:
    user_a = get_user_by_id(db_session, seeded_demo["user_id"])
    assert user_a is not None

    event = record_event(
        db_session,
        event_name="core.test.tenant_recorded",
        module="core",
        tenant_id=None,
        branch_id=None,
        actor_user_id=user_a.id,
        actor_type="user",
        entity_type="test",
        entity_id="event-tenant-a",
        correlation_id=None,
        causation_id=None,
        payload={"source": "pytest"},
        metadata_json={"request_id": "req-tenant-event"},
    )
    db_session.commit()

    assert event.tenant_id == seeded_demo["tenant_id"]
    assert event.correlation_id is not None
    logs = list_event_logs_for_tenant(db_session, seeded_demo["tenant_id"])
    assert any(item.id == event.id for item in logs)


def test_event_outbox_records_actor_tenant(
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    user_a = get_user_by_id(db_session, seeded_demo["user_id"])
    assert user_a is not None
    tenant_b = create_tenant(db_session, name="Tenant B", slug="tenant-b-outbox")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="OBB")
    user_b = create_user(
        db_session,
        tenant=tenant_b,
        branch=branch_b,
        email="tenant-b-outbox@example.com",
        password="TenantB123!",
        full_name="Tenant B Outbox",
    )

    result_a = emit_event(
        db_session,
        event=EventContract(
            event_name="core.test.outbox_a",
            module="core",
            actor_user_id=user_a.id,
            actor_type="user",
            entity_type="test",
            entity_id="outbox-a",
            payload={"tenant": "a"},
        ),
    )
    result_b = emit_event(
        db_session,
        event=EventContract(
            event_name="core.test.outbox_b",
            module="core",
            actor_user_id=user_b.id,
            actor_type="user",
            entity_type="test",
            entity_id="outbox-b",
            payload={"tenant": "b"},
        ),
    )
    db_session.commit()

    assert result_a.outbox.tenant_id == seeded_demo["tenant_id"]
    assert result_a.outbox.status == "pending"
    outbox_a = list_outbox_events_for_tenant(db_session, seeded_demo["tenant_id"])
    assert any(item.id == result_a.outbox.id for item in outbox_a)
    assert all(item.id != result_b.outbox.id for item in outbox_a)


def test_global_endpoints_work_without_tenant(client, seeded_demo: dict[str, str]) -> None:
    health_response = client.get("/api/v1/system/health")
    ready_response = client.get("/api/v1/system/ready")

    assert health_response.status_code == 200
    assert ready_response.status_code == 200


def test_auth_me_returns_tenant_and_branch(client, seeded_demo: dict[str, str]) -> None:
    login_response = login(client, "admin@example.com", "ChangeMe123!")
    token = login_response.json()["access_token"]

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == seeded_demo["tenant_id"]
    assert payload["branch_id"] == seeded_demo["branch_id"]


def test_manipulated_token_with_invalid_tenant_fails(
    app,
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    user = get_user_by_id(db_session, seeded_demo["user_id"])
    assert user is not None
    token = build_token(app, user, tenant_id="tenant-does-not-exist")

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_inactive_user_cannot_access_protected_endpoint(app, client, db_session: Session) -> None:
    tenant = create_tenant(db_session, name="Tenant Inactive", slug="tenant-inactive")
    branch = create_branch(db_session, tenant=tenant, name="Inactive Branch", code="INA")
    user = create_user(
        db_session,
        tenant=tenant,
        branch=branch,
        email="inactive@example.com",
        password="Inactive123!",
        full_name="Inactive User",
        is_active=False,
    )
    db_session.commit()

    token = build_token(app, user)
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_plugin_registry_remains_global_technical(client, seeded_demo: dict[str, str]) -> None:
    login_response = login(client, "admin@example.com", "ChangeMe123!")
    token = login_response.json()["access_token"]

    response = client.get("/api/v1/system/plugins", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert all("tenant_id" not in item for item in payload)
    assert all("branch_id" not in item for item in payload)
