from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.api.v1.core.common import (
    build_action_context,
    handle_integrity_error,
    tenant_not_found,
)
from systutor.api.v1.core.schemas import (
    CoreRoleCreateRequest,
    CoreRoleRead,
    CoreRoleUpdateRequest,
)
from systutor.api.v1.core.services.roles import (
    create_core_role,
    get_core_role,
    list_core_roles,
    set_core_role_active,
    update_core_role,
)
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    require_any_permission,
    require_permission,
)
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/core/roles", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_ROLES_READ = Depends(require_permission("core.roles.read"))
REQUIRE_ROLES_MANAGE = Depends(require_permission("core.roles.manage"))
REQUIRE_ROLES_READ_OR_MANAGE = Depends(
    require_any_permission("core.roles.read", "core.roles.manage")
)


@router.get("", response_model=list[CoreRoleRead])
def list_roles(
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_READ_OR_MANAGE,
) -> list[CoreRoleRead]:
    return [
        CoreRoleRead.model_validate(item)
        for item in list_core_roles(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.get("/{role_id}", response_model=CoreRoleRead)
def get_role(
    role_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_READ_OR_MANAGE,
) -> CoreRoleRead:
    item = get_core_role(db, tenant_id=tenant_context.current_tenant_id, role_id=role_id)
    if item is None:
        raise tenant_not_found("Role")
    return CoreRoleRead.model_validate(item)


@router.post("", response_model=CoreRoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: CoreRoleCreateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_MANAGE,
) -> CoreRoleRead:
    try:
        item = create_core_role(
            db,
            tenant_id=tenant_context.current_tenant_id,
            name=payload.name,
            permission_names=payload.permission_names,
            action_context=build_action_context(request, tenant_context),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CoreRoleRead.model_validate(item)


@router.patch("/{role_id}", response_model=CoreRoleRead)
def update_role(
    role_id: str,
    payload: CoreRoleUpdateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_MANAGE,
) -> CoreRoleRead:
    try:
        item = update_core_role(
            db,
            tenant_id=tenant_context.current_tenant_id,
            role_id=role_id,
            name=payload.name,
            permission_names=payload.permission_names,
            action_context=build_action_context(request, tenant_context),
        )
        if item is None:
            raise tenant_not_found("Role")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CoreRoleRead.model_validate(item)


@router.post("/{role_id}/disable", response_model=CoreRoleRead)
def disable_role(
    role_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_MANAGE,
) -> CoreRoleRead:
    item = set_core_role_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        role_id=role_id,
        is_active=False,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("Role")
    db.commit()
    return CoreRoleRead.model_validate(item)


@router.post("/{role_id}/enable", response_model=CoreRoleRead)
def enable_role(
    role_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_ROLES_MANAGE,
) -> CoreRoleRead:
    item = set_core_role_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        role_id=role_id,
        is_active=True,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("Role")
    db.commit()
    return CoreRoleRead.model_validate(item)
