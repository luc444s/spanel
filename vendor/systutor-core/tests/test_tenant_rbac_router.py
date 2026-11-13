from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import systutor.kernel.models  # noqa: F401
from plugins.tenant.backend.rbac_router import router
from systutor.api.deps import get_db_session
from systutor.core.database import Base
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.models import Permission, Role, UserRole
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.models import Tenant


@pytest.fixture()
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as db:
        platform = Tenant(id="platform", name="Platform", slug="platform")
        target = Tenant(id="target", name="Target", slug="target")
        actor = User(
            id="actor",
            tenant_id=platform.id,
            email="actor@example.com",
            full_name="Actor",
            password_hash="hash",
        )
        member = User(
            id="member",
            tenant_id=target.id,
            email="member@example.com",
            full_name="Member",
            password_hash="hash",
        )
        operator = Role(id="operator", tenant_id=target.id, name="operator")
        administrator = Role(id="administrator", tenant_id=target.id, name="administrator")
        permission = Permission(id="read-users", name="core.users.read")
        db.add_all([platform, target, actor, member, operator, administrator, permission])
        db.flush()
        db.add(UserRole(user_id=member.id, role_id=operator.id))
        db.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1/plugins/tenant")
    context = TenantContext(
        current_tenant_id="platform",
        current_branch_id=None,
        current_user_id="actor",
        current_permissions=("tenant.tenants.manage", "core.platform.admin"),
        current_warehouse_ids=None,
    )

    def session_override() -> Iterator[Session]:
        with factory() as db:
            yield db

    with factory() as db:
        actor = db.get(User, "actor")
        assert actor is not None
        app.dependency_overrides[get_current_user] = lambda: actor
        app.dependency_overrides[get_current_tenant_context] = lambda: context
        app.dependency_overrides[get_db_session] = session_override
        with TestClient(app) as test_client:
            yield test_client
    engine.dispose()


def test_platform_admin_lists_and_replaces_selected_tenant_role(client: TestClient) -> None:
    roles = client.get("/api/v1/plugins/tenant/tenants/target/roles")
    assert roles.status_code == 200
    assert {item["name"] for item in roles.json()["roles"]} == {
        "administrator",
        "operator",
    }

    response = client.put(
        "/api/v1/plugins/tenant/tenants/target/users/member/role",
        json={"role_id": "administrator"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["administrator"]


def test_platform_admin_edits_role_permissions(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/plugins/tenant/tenants/target/roles/operator",
        json={"permission_names": ["core.users.read"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["permissions"] == ["core.users.read"]


def test_role_assignment_rejects_cross_tenant_user(client: TestClient) -> None:
    response = client.put(
        "/api/v1/plugins/tenant/tenants/target/users/actor/role",
        json={"role_id": "administrator"},
    )
    assert response.status_code == 404
