from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.api.v1.core.common import (
    build_action_context,
    handle_integrity_error,
    tenant_not_found,
)
from systutor.api.v1.core.schemas import (
    CoreBranchCreateRequest,
    CoreBranchRead,
    CoreBranchUpdateRequest,
)
from systutor.api.v1.core.services.branches import (
    create_core_branch,
    get_core_branch,
    list_core_branches,
    set_core_branch_active,
    update_core_branch,
)
from systutor.kernel.auth.dependencies import (
    get_current_tenant_context,
    require_any_permission,
    require_permission,
)
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

router = APIRouter(prefix="/core/branches", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_BRANCHES_READ = Depends(require_permission("core.branches.read"))
REQUIRE_BRANCHES_MANAGE = Depends(require_permission("core.branches.manage"))
REQUIRE_BRANCHES_READ_OR_MANAGE = Depends(
    require_any_permission("core.branches.read", "core.branches.manage")
)


@router.get("", response_model=list[CoreBranchRead])
def list_branches(
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_READ_OR_MANAGE,
) -> list[CoreBranchRead]:
    return [
        CoreBranchRead.model_validate(item)
        for item in list_core_branches(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.get("/{branch_id}", response_model=CoreBranchRead)
def get_branch(
    branch_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_READ_OR_MANAGE,
) -> CoreBranchRead:
    item = get_core_branch(db, tenant_id=tenant_context.current_tenant_id, branch_id=branch_id)
    if item is None:
        raise tenant_not_found("Branch")
    return CoreBranchRead.model_validate(item)


@router.post("", response_model=CoreBranchRead, status_code=status.HTTP_201_CREATED)
def create_branch(
    payload: CoreBranchCreateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> CoreBranchRead:
    try:
        item = create_core_branch(
            db,
            tenant_id=tenant_context.current_tenant_id,
            name=payload.name,
            code=payload.code,
            action_context=build_action_context(request, tenant_context),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    return CoreBranchRead.model_validate(item)


@router.patch("/{branch_id}", response_model=CoreBranchRead)
def update_branch(
    branch_id: str,
    payload: CoreBranchUpdateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> CoreBranchRead:
    try:
        item = update_core_branch(
            db,
            tenant_id=tenant_context.current_tenant_id,
            branch_id=branch_id,
            name=payload.name,
            code=payload.code,
            action_context=build_action_context(request, tenant_context),
        )
        if item is None:
            raise tenant_not_found("Branch")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    return CoreBranchRead.model_validate(item)


@router.post("/{branch_id}/disable", response_model=CoreBranchRead)
def disable_branch(
    branch_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> CoreBranchRead:
    item = set_core_branch_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        branch_id=branch_id,
        is_active=False,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("Branch")
    db.commit()
    return CoreBranchRead.model_validate(item)


@router.post("/{branch_id}/enable", response_model=CoreBranchRead)
def enable_branch(
    branch_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_BRANCHES_MANAGE,
) -> CoreBranchRead:
    item = set_core_branch_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        branch_id=branch_id,
        is_active=True,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("Branch")
    db.commit()
    return CoreBranchRead.model_validate(item)
