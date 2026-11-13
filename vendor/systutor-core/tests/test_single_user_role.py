from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from systutor.kernel.audit.models import AuditLog
from systutor.kernel.events.models import EventLog
from systutor.kernel.permissions.models import Role, UserRole
from systutor.kernel.tenants.models import Tenant


def _headers(client) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "ChangeMe123!"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _create_role(client, headers: dict[str, str], name: str) -> dict:
    response = client.post(
        "/api/v1/core/roles",
        headers=headers,
        json={"name": name, "permission_names": ["core.users.read"]},
    )
    assert response.status_code == 201
    return response.json()


def _create_user(client, headers: dict[str, str], role_id: str) -> dict:
    response = client.post(
        "/api/v1/core/users",
        headers=headers,
        json={
            "name": "Single Role User",
            "email": "single-role@example.com",
            "password": "SingleRole123!",
            "role_ids": [role_id],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_role_replacement_is_atomic_audited_and_singular(
    client, db_session: Session, seeded_demo
) -> None:
    headers = _headers(client)
    first = _create_role(client, headers, "first-role")
    second = _create_role(client, headers, "second-role")
    user = _create_user(client, headers, first["id"])

    response = client.put(
        f"/api/v1/core/users/{user['id']}/role",
        headers=headers,
        json={"role_id": second["id"]},
    )

    assert response.status_code == 200
    assert response.json()["roles"] == ["second-role"]
    assignment_count = db_session.scalar(
        select(func.count(UserRole.id)).where(UserRole.user_id == user["id"])
    )
    assert assignment_count == 1
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == user["id"],
            AuditLog.action == "user.role.replace",
        )
    )
    assert audit is not None
    assert audit.details["previous_roles"] == ["first-role"]
    event = db_session.scalar(
        select(EventLog).where(
            EventLog.entity_id == user["id"],
            EventLog.event_name == "core.user.role_replaced",
        )
    )
    assert event is not None


def test_role_replacement_rejects_inactive_and_cross_tenant_roles(
    client, db_session: Session, seeded_demo
) -> None:
    headers = _headers(client)
    current = _create_role(client, headers, "current-role")
    inactive = _create_role(client, headers, "inactive-role")
    user = _create_user(client, headers, current["id"])
    assert client.post(
        f"/api/v1/core/roles/{inactive['id']}/disable", headers=headers
    ).status_code == 200

    inactive_response = client.put(
        f"/api/v1/core/users/{user['id']}/role",
        headers=headers,
        json={"role_id": inactive["id"]},
    )
    assert inactive_response.status_code == 400

    other_tenant = Tenant(name="Other", slug="other-single-role")
    db_session.add(other_tenant)
    db_session.flush()
    foreign_role = Role(tenant_id=other_tenant.id, name="foreign", is_active=True)
    db_session.add(foreign_role)
    db_session.commit()
    cross_response = client.put(
        f"/api/v1/core/users/{user['id']}/role",
        headers=headers,
        json={"role_id": foreign_role.id},
    )
    assert cross_response.status_code == 404

    detail = client.get(f"/api/v1/core/users/{user['id']}", headers=headers)
    assert detail.json()["roles"] == ["current-role"]


def test_user_payload_rejects_more_than_one_role(client, seeded_demo) -> None:
    headers = _headers(client)
    first = _create_role(client, headers, "payload-first")
    second = _create_role(client, headers, "payload-second")
    response = client.post(
        "/api/v1/core/users",
        headers=headers,
        json={
            "name": "Invalid Roles",
            "email": "invalid-roles@example.com",
            "password": "InvalidRoles123!",
            "role_ids": [first["id"], second["id"]],
        },
    )
    assert response.status_code == 422
