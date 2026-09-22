from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.api.v1.core.common import CoreActionContext, audit_core_action, emit_core_event
from systutor.kernel.permissions.models import Role
from systutor.kernel.permissions.service import (
    create_role_for_tenant,
    get_role_for_tenant,
    list_permissions_by_names,
    list_role_permission_names,
    list_roles_for_tenant,
    replace_role_permissions,
    update_role_for_tenant,
)


def serialize_core_role(db: Session, role: Role) -> dict[str, object]:
    return {
        "id": role.id,
        "tenant_id": role.tenant_id,
        "name": role.name,
        "permissions": list_role_permission_names(db, role_id=role.id),
        "active": role.is_active,
        "created_at": role.created_at,
        "updated_at": role.updated_at,
    }


def list_core_roles(db: Session, *, tenant_id: str) -> list[dict[str, object]]:
    roles = list_roles_for_tenant(db, tenant_id=tenant_id)
    return [serialize_core_role(db, role) for role in roles]


def get_core_role(db: Session, *, tenant_id: str, role_id: str) -> dict[str, object] | None:
    role = get_role_for_tenant(db, tenant_id=tenant_id, role_id=role_id)
    if role is None:
        return None
    return serialize_core_role(db, role)


def create_core_role(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    permission_names: list[str],
    action_context: CoreActionContext,
) -> dict[str, object]:
    permissions = list_permissions_by_names(db, permission_names=permission_names)
    if len(permissions) != len(set(permission_names)):
        raise ValueError("Invalid permission in payload")

    role = create_role_for_tenant(db, tenant_id=tenant_id, name=name, description=None)
    replace_role_permissions(db, role=role, permissions=permissions)
    audit_core_action(
        db,
        context=action_context,
        action="role.create",
        entity_type="role",
        entity_id=role.id,
        details={"name": role.name, "permissions": list(permission_names)},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.role.created",
        entity_type="role",
        entity_id=role.id,
        payload={"tenant_id": tenant_id, "name": role.name},
    )
    return serialize_core_role(db, role)


def update_core_role(
    db: Session,
    *,
    tenant_id: str,
    role_id: str,
    name: str | None,
    permission_names: list[str] | None,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    role = get_role_for_tenant(db, tenant_id=tenant_id, role_id=role_id)
    if role is None:
        return None

    permissions = None
    if permission_names is not None:
        permissions = list_permissions_by_names(db, permission_names=permission_names)
        if len(permissions) != len(set(permission_names)):
            raise ValueError("Invalid permission in payload")

    role = update_role_for_tenant(db, role=role, name=name)
    if permissions is not None:
        replace_role_permissions(db, role=role, permissions=permissions)
    audit_core_action(
        db,
        context=action_context,
        action="role.update",
        entity_type="role",
        entity_id=role.id,
        details={"name": role.name},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.role.updated",
        entity_type="role",
        entity_id=role.id,
        payload={"tenant_id": tenant_id, "name": role.name},
    )
    return serialize_core_role(db, role)


def set_core_role_active(
    db: Session,
    *,
    tenant_id: str,
    role_id: str,
    is_active: bool,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    role = get_role_for_tenant(db, tenant_id=tenant_id, role_id=role_id)
    if role is None:
        return None

    role = update_role_for_tenant(db, role=role, is_active=is_active)
    action = "role.enable" if is_active else "role.disable"
    event_name = "core.role.enabled" if is_active else "core.role.disabled"
    audit_core_action(
        db,
        context=action_context,
        action=action,
        entity_type="role",
        entity_id=role.id,
        details={"name": role.name, "active": is_active},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name=event_name,
        entity_type="role",
        entity_id=role.id,
        payload={"tenant_id": tenant_id, "name": role.name, "active": is_active},
    )
    return serialize_core_role(db, role)
