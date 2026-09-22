from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from systutor.kernel.audit.models import AuditLog
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.events.models import EventLog
from systutor.kernel.events.service import record_event
from systutor.kernel.permissions.models import Role, UserRole
from systutor.kernel.plugins.runtime import PluginManifestRegistry


def login(client, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_health_check(client) -> None:
    response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "api"


def test_database_connection(client, seeded_demo: dict[str, str]) -> None:
    response = client.get("/api/v1/system/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database_connected"] is True
    assert payload["plugins_loaded"] >= 1


def test_db_session_can_execute_query(db_session: Session) -> None:
    value = db_session.execute(text("SELECT 1")).scalar_one()
    assert value == 1


def test_login_correct(client, seeded_demo: dict[str, str]) -> None:
    response = login(client, "admin@example.com", "ChangeMe123!")

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == "admin@example.com"
    assert "core.plugin.read" in payload["user"]["permissions"]


def test_login_incorrect(client, seeded_demo: dict[str, str]) -> None:
    response = login(client, "admin@example.com", "wrong-password")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_current_user(client, seeded_demo: dict[str, str]) -> None:
    login_response = login(client, "admin@example.com", "ChangeMe123!")
    token = login_response.json()["access_token"]

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "admin@example.com"
    assert payload["tenant_id"] == seeded_demo["tenant_id"]


def test_permission_validation(client, db_session: Session, seeded_demo: dict[str, str]) -> None:
    limited_role = Role(
        tenant_id=seeded_demo["tenant_id"],
        name="viewer",
        description="Limited role",
    )
    db_session.add(limited_role)
    db_session.flush()

    limited_user = User(
        tenant_id=seeded_demo["tenant_id"],
        branch_id=seeded_demo["branch_id"],
        email="viewer@example.com",
        full_name="Viewer",
        password_hash=hash_password("Viewer123!"),
        is_active=True,
        is_superadmin=False,
    )
    db_session.add(limited_user)
    db_session.flush()
    db_session.add(UserRole(user_id=limited_user.id, role_id=limited_role.id))
    db_session.commit()

    login_response = login(client, "viewer@example.com", "Viewer123!")
    token = login_response.json()["access_token"]

    response = client.get("/api/v1/system/plugins", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


def test_audit_log_written(db_session: Session, seeded_demo: dict[str, str]) -> None:
    record_audit(
        db_session,
        tenant_id=seeded_demo["tenant_id"],
        branch_id=seeded_demo["branch_id"],
        actor_user_id=seeded_demo["user_id"],
        actor_type="user",
        module="core",
        action="test.audit",
        entity_type="test",
        entity_id="audit-1",
        result="success",
        correlation_id="corr-1",
        request_id="req-1",
        details={"source": "pytest"},
    )
    db_session.commit()

    log = db_session.query(AuditLog).filter(AuditLog.action == "test.audit").one()
    assert log.result == "success"
    assert log.correlation_id == "corr-1"


def test_event_log_written(db_session: Session, seeded_demo: dict[str, str]) -> None:
    record_event(
        db_session,
        event_name="core.test.executed",
        module="core",
        tenant_id=seeded_demo["tenant_id"],
        branch_id=seeded_demo["branch_id"],
        actor_user_id=seeded_demo["user_id"],
        actor_type="user",
        entity_type="test",
        entity_id="event-1",
        correlation_id="corr-2",
        causation_id=None,
        payload={"source": "pytest"},
        metadata_json={"request_id": "req-2"},
    )
    db_session.commit()

    log = db_session.query(EventLog).filter(EventLog.event_name == "core.test.executed").one()
    assert log.correlation_id == "corr-2"
    assert log.payload["source"] == "pytest"


def test_plugin_manifest_validation(app) -> None:
    registry = PluginManifestRegistry(app.state.settings.plugins_dir)
    registry.discover()

    manifests = registry.list()
    assert len(manifests) >= 1
    manifest_ids = {manifest.id for manifest in manifests}
    assert "alpha" in manifest_ids
    assert "beta" in manifest_ids
    assert all(manifest.description for manifest in manifests)
