from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.api.v1.core.common import CoreActionContext, audit_core_action, emit_core_event
from systutor.kernel.auth.models import User
from systutor.kernel.auth.security import hash_password
from systutor.kernel.auth.service import (
    create_user_for_tenant,
    get_user_for_tenant,
    list_users_for_tenant,
    update_user_for_tenant,
)
from systutor.kernel.permissions.service import (
    list_roles_by_ids_for_tenant,
    list_roles_by_names_for_tenant,
    list_user_role_names,
    replace_user_roles,
)
from systutor.kernel.tenants.models import Branch
from systutor.kernel.tenants.service import list_user_warehouse_ids, replace_user_warehouse_ids

# User categories are host-defined. The host application registers each
# category with its display label and the roles auto-assigned on creation.
# key -> (display label, roles auto-assigned when creating a user with that category)
_USER_CATEGORIES: dict[str, tuple[str, list[str]]] = {}


def register_user_category(key: str, label: str, role_names: list[str]) -> None:
    """Register a user category for the host application."""
    _USER_CATEGORIES[key] = (label, list(role_names))


def get_user_categories() -> dict[str, tuple[str, list[str]]]:
    """Return the registered user categories."""
    return dict(_USER_CATEGORIES)


def get_user_category_map() -> dict[str, list[str]]:
    """Return category keys mapped to their auto-assigned role names."""
    return {key: role_names for key, (_label, role_names) in _USER_CATEGORIES.items()}


def get_category_labels() -> dict[str, str]:
    """Return category keys mapped to their display labels."""
    return {key: label for key, (label, _role_names) in _USER_CATEGORIES.items()}


def serialize_core_user(db: Session, user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "branch_id": user.branch_id,
        "name": user.full_name,
        "email": user.email,
        "active": user.is_active,
        "category": user.category,
        "roles": list_user_role_names(db, tenant_id=user.tenant_id, user_id=user.id),
        "warehouse_ids": list_user_warehouse_ids(db, tenant_id=user.tenant_id, user_id=user.id),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def list_core_users(db: Session, *, tenant_id: str) -> list[dict[str, object]]:
    users = list_users_for_tenant(db, tenant_id=tenant_id)
    return [serialize_core_user(db, user) for user in users]


def get_core_user(db: Session, *, tenant_id: str, user_id: str) -> dict[str, object] | None:
    user = get_user_for_tenant(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        return None
    return serialize_core_user(db, user)


def create_core_user(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    email: str,
    password: str,
    branch: Branch | None,
    category: str | None,
    role_ids: list[str],
    warehouse_ids: list[str],
    action_context: CoreActionContext,
) -> dict[str, object]:
    resolved_role_ids = list(role_ids)
    category_role_names = get_user_category_map().get(category) if category else None
    if category_role_names:
        category_roles = list_roles_by_names_for_tenant(
            db, tenant_id=tenant_id, role_names=category_role_names
        )
        for cr in category_roles:
            if cr.id not in resolved_role_ids:
                resolved_role_ids.append(cr.id)

    roles = list_roles_by_ids_for_tenant(db, tenant_id=tenant_id, role_ids=resolved_role_ids)
    if len(roles) != len(set(resolved_role_ids)):
        raise ValueError("Invalid role for tenant")
    if any(not role.is_active for role in roles):
        raise ValueError("Disabled roles cannot be assigned")

    user = create_user_for_tenant(
        db,
        tenant_id=tenant_id,
        email=email,
        full_name=name,
        password_hash=hash_password(password),
        branch=branch,
        is_active=True,
    )
    if category:
        user.category = category
        db.add(user)
        db.flush()
    replace_user_roles(db, user=user, roles=roles)
    replace_user_warehouse_ids(db, user=user, warehouse_ids=warehouse_ids)
    audit_core_action(
        db,
        context=action_context,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        details={
            "email": user.email,
            "role_ids": list(role_ids),
            "warehouse_ids": list(warehouse_ids),
        },
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.user.created",
        entity_type="user",
        entity_id=user.id,
        payload={"email": user.email, "tenant_id": tenant_id},
    )
    return serialize_core_user(db, user)


def update_core_user(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    name: str | None,
    email: str | None,
    password: str | None,
    branch: Branch | None,
    branch_was_provided: bool,
    category: str | None,
    role_ids: list[str] | None,
    warehouse_ids: list[str] | None,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    user = get_user_for_tenant(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        return None

    roles = None
    if role_ids is not None:
        roles = list_roles_by_ids_for_tenant(db, tenant_id=tenant_id, role_ids=role_ids)
        if len(roles) != len(set(role_ids)):
            raise ValueError("Invalid role for tenant")
        if any(not role.is_active for role in roles):
            raise ValueError("Disabled roles cannot be assigned")

    user = update_user_for_tenant(
        db,
        user=user,
        full_name=name,
        password_hash=hash_password(password) if password is not None else None,
        branch=branch,
        branch_was_provided=branch_was_provided,
    )
    if email is not None:
        user.email = email
        db.add(user)
        db.flush()
    if category is not None:
        user.category = category if category else None
        db.add(user)
        db.flush()
    if roles is not None:
        replace_user_roles(db, user=user, roles=roles)
    if warehouse_ids is not None:
        replace_user_warehouse_ids(db, user=user, warehouse_ids=warehouse_ids)

    audit_core_action(
        db,
        context=action_context,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "warehouse_ids": warehouse_ids},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.user.updated",
        entity_type="user",
        entity_id=user.id,
        payload={"email": user.email, "tenant_id": tenant_id},
    )
    return serialize_core_user(db, user)


def set_core_user_active(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    is_active: bool,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    user = get_user_for_tenant(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        return None

    user = update_user_for_tenant(db, user=user, is_active=is_active)
    action = "user.enable" if is_active else "user.disable"
    event_name = "core.user.enabled" if is_active else "core.user.disabled"
    audit_core_action(
        db,
        context=action_context,
        action=action,
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "active": is_active},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name=event_name,
        entity_type="user",
        entity_id=user.id,
        payload={"email": user.email, "tenant_id": tenant_id, "active": is_active},
    )
    return serialize_core_user(db, user)
