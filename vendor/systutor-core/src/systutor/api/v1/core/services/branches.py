from __future__ import annotations

from sqlalchemy.orm import Session

from systutor.api.v1.core.common import CoreActionContext, audit_core_action, emit_core_event
from systutor.kernel.tenants.models import Branch
from systutor.kernel.tenants.service import (
    create_branch_for_tenant,
    get_branch_for_tenant,
    list_branches_for_tenant,
    update_branch_for_tenant,
)


def serialize_core_branch(branch: Branch) -> dict[str, object]:
    return {
        "id": branch.id,
        "tenant_id": branch.tenant_id,
        "name": branch.name,
        "code": branch.code,
        "active": branch.is_active,
        "created_at": branch.created_at,
        "updated_at": branch.updated_at,
    }


def list_core_branches(db: Session, *, tenant_id: str) -> list[dict[str, object]]:
    branches = list_branches_for_tenant(db, tenant_id=tenant_id)
    return [serialize_core_branch(branch) for branch in branches]


def get_core_branch(db: Session, *, tenant_id: str, branch_id: str) -> dict[str, object] | None:
    branch = get_branch_for_tenant(db, tenant_id, branch_id)
    if branch is None:
        return None
    return serialize_core_branch(branch)


def create_core_branch(
    db: Session,
    *,
    tenant_id: str,
    name: str,
    code: str,
    action_context: CoreActionContext,
) -> dict[str, object]:
    branch = create_branch_for_tenant(
        db,
        tenant_id=tenant_id,
        name=name,
        code=code,
        is_active=True,
    )
    audit_core_action(
        db,
        context=action_context,
        action="branch.create",
        entity_type="branch",
        entity_id=branch.id,
        details={"code": branch.code},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.branch.created",
        entity_type="branch",
        entity_id=branch.id,
        payload={"tenant_id": tenant_id, "code": branch.code},
    )
    return serialize_core_branch(branch)


def update_core_branch(
    db: Session,
    *,
    tenant_id: str,
    branch_id: str,
    name: str | None,
    code: str | None,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    branch = get_branch_for_tenant(db, tenant_id, branch_id)
    if branch is None:
        return None

    branch = update_branch_for_tenant(db, branch=branch, name=name, code=code)
    audit_core_action(
        db,
        context=action_context,
        action="branch.update",
        entity_type="branch",
        entity_id=branch.id,
        details={"code": branch.code},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name="core.branch.updated",
        entity_type="branch",
        entity_id=branch.id,
        payload={"tenant_id": tenant_id, "code": branch.code},
    )
    return serialize_core_branch(branch)


def set_core_branch_active(
    db: Session,
    *,
    tenant_id: str,
    branch_id: str,
    is_active: bool,
    action_context: CoreActionContext,
) -> dict[str, object] | None:
    branch = get_branch_for_tenant(db, tenant_id, branch_id)
    if branch is None:
        return None

    branch = update_branch_for_tenant(db, branch=branch, is_active=is_active)
    action = "branch.enable" if is_active else "branch.disable"
    event_name = "core.branch.enabled" if is_active else "core.branch.disabled"
    audit_core_action(
        db,
        context=action_context,
        action=action,
        entity_type="branch",
        entity_id=branch.id,
        details={"code": branch.code, "active": is_active},
    )
    emit_core_event(
        db,
        context=action_context,
        event_name=event_name,
        entity_type="branch",
        entity_id=branch.id,
        payload={"tenant_id": tenant_id, "code": branch.code, "active": is_active},
    )
    return serialize_core_branch(branch)
