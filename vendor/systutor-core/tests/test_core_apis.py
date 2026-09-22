from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.permissions.models import Role
from systutor.kernel.tenants.models import Branch, Tenant


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
    return user


def create_role(db: Session, *, tenant: Tenant, name: str) -> Role:
    role = Role(tenant_id=tenant.id, name=name, description=f"Role {name}")
    db.add(role)
    db.flush()
    return role


def auth_headers(
    client, email: str = "admin@example.com", password: str = "ChangeMe123!"
) -> dict[str, str]:
    response = login(client, email, password)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_users_api_is_tenant_scoped(
    client, db_session: Session, seeded_demo: dict[str, str]
) -> None:
    tenant_b = create_tenant(db_session, name="Tenant B", slug="api-tenant-b")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="B01")
    user_b = create_user(
        db_session,
        tenant=tenant_b,
        branch=branch_b,
        email="tenant-b-api@example.com",
        password="TenantB123!",
        full_name="Tenant B API",
    )
    db_session.commit()

    headers = auth_headers(client)
    list_response = client.get("/api/v1/users", headers=headers)
    detail_response = client.get(f"/api/v1/users/{user_b.id}", headers=headers)

    assert list_response.status_code == 200
    payload = list_response.json()
    assert all(item["tenant_id"] == seeded_demo["tenant_id"] for item in payload)
    assert all(item["id"] != user_b.id for item in payload)
    assert detail_response.status_code == 404


def test_cross_tenant_role_assignment_fails(
    client, db_session: Session, seeded_demo: dict[str, str]
) -> None:
    tenant_a = db_session.get(Tenant, seeded_demo["tenant_id"])
    assert tenant_a is not None
    branch_a = db_session.get(Branch, seeded_demo["branch_id"])
    assert branch_a is not None
    user_a = create_user(
        db_session,
        tenant=tenant_a,
        branch=branch_a,
        email="tenant-a-role-target@example.com",
        password="Target123!",
        full_name="Tenant A Target",
    )
    tenant_b = create_tenant(db_session, name="Tenant B", slug="api-role-b")
    role_b = create_role(db_session, tenant=tenant_b, name="external-role")
    db_session.commit()

    response = client.post(
        f"/api/v1/users/{user_a.id}/roles",
        headers=auth_headers(client),
        json={"role_id": role_b.id},
    )

    assert response.status_code == 404


def test_cross_tenant_branch_assignment_fails(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    tenant_b = create_tenant(db_session, name="Tenant B", slug="api-branch-b")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="BB1")
    db_session.commit()

    response = client.post(
        "/api/v1/users",
        headers=auth_headers(client),
        json={
            "email": "cross-branch@example.com",
            "full_name": "Cross Branch",
            "password": "CrossBranch123!",
            "branch_id": branch_b.id,
        },
    )

    assert response.status_code == 400


def test_audit_log_api_is_tenant_scoped(
    client, db_session: Session, seeded_demo: dict[str, str]
) -> None:
    tenant_b = create_tenant(db_session, name="Tenant B", slug="api-audit-b")
    branch_b = create_branch(db_session, tenant=tenant_b, name="Branch B", code="AB1")
    user_b = create_user(
        db_session,
        tenant=tenant_b,
        branch=branch_b,
        email="tenant-b-audit@example.com",
        password="Audit123!",
        full_name="Tenant B Audit",
    )
    audit_b = record_audit(
        db_session,
        tenant_id=tenant_b.id,
        branch_id=branch_b.id,
        actor_user_id=user_b.id,
        actor_type="user",
        module="core",
        action="tenant-b.audit",
        entity_type="test",
        entity_id="audit-b",
        result="success",
        correlation_id="corr-b",
        request_id="req-b",
        details={"source": "pytest"},
    )
    db_session.commit()

    list_response = client.get("/api/v1/audit-logs", headers=auth_headers(client))
    detail_response = client.get(f"/api/v1/audit-logs/{audit_b.id}", headers=auth_headers(client))

    assert list_response.status_code == 200
    payload = list_response.json()
    assert all(item["tenant_id"] == seeded_demo["tenant_id"] for item in payload)
    assert all(item["id"] != audit_b.id for item in payload)
    assert detail_response.status_code == 404


def test_permission_checks_apply_to_new_core_apis(
    client,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    limited_user = User(
        tenant_id=seeded_demo["tenant_id"],
        branch_id=seeded_demo["branch_id"],
        email="no-users-read@example.com",
        full_name="No Users Read",
        password_hash=hash_password("Viewer123!"),
        is_active=True,
        is_superadmin=False,
    )
    db_session.add(limited_user)
    db_session.commit()

    response = client.get(
        "/api/v1/users",
        headers=auth_headers(client, email="no-users-read@example.com", password="Viewer123!"),
    )

    assert response.status_code == 403


def test_core_api_crud_smoke(client, seeded_demo: dict[str, str]) -> None:
    headers = auth_headers(client)

    branch_response = client.post(
        "/api/v1/branches",
        headers=headers,
        json={"name": "North Branch", "code": "NORTH", "is_active": True},
    )
    assert branch_response.status_code == 201
    branch_id = branch_response.json()["id"]

    role_response = client.post(
        "/api/v1/roles",
        headers=headers,
        json={"name": "operator", "description": "Operator role"},
    )
    assert role_response.status_code == 201
    role_id = role_response.json()["id"]

    user_response = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "operator@example.com",
            "full_name": "Operator User",
            "password": "Operator123!",
            "branch_id": branch_id,
            "is_active": True,
        },
    )
    assert user_response.status_code == 201
    user_id = user_response.json()["id"]

    get_user_response = client.get(f"/api/v1/users/{user_id}", headers=headers)
    assert get_user_response.status_code == 200

    patch_user_response = client.patch(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"full_name": "Operator Updated"},
    )
    assert patch_user_response.status_code == 200
    assert patch_user_response.json()["full_name"] == "Operator Updated"

    assign_role_response = client.post(
        f"/api/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_id": role_id},
    )
    assert assign_role_response.status_code == 201

    permissions_response = client.get("/api/v1/permissions", headers=headers)
    assert permissions_response.status_code == 200
    assert any(item["name"] == "core.plugin.read" for item in permissions_response.json())

    plugin_registry_response = client.get("/api/v1/plugin-registry", headers=headers)
    assert plugin_registry_response.status_code == 200
    assert plugin_registry_response.json()

    remove_role_response = client.delete(
        f"/api/v1/users/{user_id}/roles/{role_id}",
        headers=headers,
    )
    assert remove_role_response.status_code == 204

    delete_user_response = client.delete(f"/api/v1/users/{user_id}", headers=headers)
    assert delete_user_response.status_code == 204

    delete_role_response = client.delete(f"/api/v1/roles/{role_id}", headers=headers)
    assert delete_role_response.status_code == 204

    delete_branch_response = client.delete(f"/api/v1/branches/{branch_id}", headers=headers)
    assert delete_branch_response.status_code == 204
