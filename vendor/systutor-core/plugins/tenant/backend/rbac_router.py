from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from plugins.tenant.backend.schemas import (
    ReplaceTenantUserRoleRequest,
    TenantPermissionResponse,
    TenantPermissionsResponse,
    TenantRoleResponse,
    TenantRolesResponse,
    TenantUserResponse,
    UpdateTenantRoleRequest,
)
from plugins.tenant.backend.service import get_tenant_by_id
from systutor.api.deps import get_db_session
from systutor.api.v1.core.common import CoreActionContext
from systutor.api.v1.core.services.roles import list_core_roles, update_core_role
from systutor.api.v1.core.services.users import replace_core_user_role
from systutor.kernel.auth.dependencies import require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.permissions.service import (
    get_role_for_tenant,
    list_permissions,
)

router = APIRouter(prefix="/tenants", tags=["tenants"])
PLATFORM_TENANT_MANAGE = Depends(
    require_permission("tenant.tenants.manage", platform_only=True)
)


def _require_tenant(db: Session, tenant_id: str) -> None:
    if get_tenant_by_id(db, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")


def _action_context(request: Request, actor: User) -> CoreActionContext:
    return CoreActionContext(
        tenant_id=actor.tenant_id,
        branch_id=actor.branch_id,
        actor_user_id=actor.id,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
    )


@router.get("/{tenant_id}/roles", response_model=TenantRolesResponse)
def list_tenant_roles(
    tenant_id: str,
    db: Session = Depends(get_db_session),
    _actor: User = PLATFORM_TENANT_MANAGE,
) -> TenantRolesResponse:
    _require_tenant(db, tenant_id)
    return TenantRolesResponse(
        roles=[
            TenantRoleResponse.model_validate(role)
            for role in list_core_roles(db, tenant_id=tenant_id)
        ]
    )


@router.get("/{tenant_id}/permissions", response_model=TenantPermissionsResponse)
def list_tenant_permissions(
    tenant_id: str,
    db: Session = Depends(get_db_session),
    _actor: User = PLATFORM_TENANT_MANAGE,
) -> TenantPermissionsResponse:
    _require_tenant(db, tenant_id)
    return TenantPermissionsResponse(
        permissions=[
            TenantPermissionResponse.model_validate(permission, from_attributes=True)
            for permission in list_permissions(db)
        ]
    )


@router.put("/{tenant_id}/users/{user_id}/role", response_model=TenantUserResponse)
def replace_tenant_user_role(
    tenant_id: str,
    user_id: str,
    body: ReplaceTenantUserRoleRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    actor: User = PLATFORM_TENANT_MANAGE,
) -> TenantUserResponse:
    _require_tenant(db, tenant_id)
    role = get_role_for_tenant(db, tenant_id=tenant_id, role_id=body.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    try:
        user = replace_core_user_role(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            action_context=_action_context(request, actor),
        )
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TenantUserResponse(
        id=str(user["id"]),
        email=str(user["email"]),
        full_name=str(user["name"]),
        is_active=bool(user["active"]),
        branch_id=user["branch_id"] if isinstance(user["branch_id"], str) else None,
        roles=list(user["roles"]),  # type: ignore[arg-type]
    )


@router.patch("/{tenant_id}/roles/{role_id}", response_model=TenantRoleResponse)
def update_tenant_role(
    tenant_id: str,
    role_id: str,
    body: UpdateTenantRoleRequest,
    request: Request,
    db: Session = Depends(get_db_session),
    actor: User = PLATFORM_TENANT_MANAGE,
) -> TenantRoleResponse:
    _require_tenant(db, tenant_id)
    try:
        role = update_core_role(
            db,
            tenant_id=tenant_id,
            role_id=role_id,
            name=None,
            permission_names=body.permission_names,
            action_context=_action_context(request, actor),
        )
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TenantRoleResponse.model_validate(role)
