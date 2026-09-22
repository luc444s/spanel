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
    CoreUserCategoryRead,
    CoreUserCreateRequest,
    CoreUserRead,
    CoreUserUpdateRequest,
)
from systutor.api.v1.core.services.users import (
    create_core_user,
    get_core_user,
    get_user_categories,
    list_core_users,
    set_core_user_active,
    update_core_user,
)
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext
from systutor.kernel.tenants.service import TenantScopeError, get_branch_for_tenant

router = APIRouter(prefix="/core/users", tags=["core"])
DB_SESSION = Depends(get_db_session)
TENANT_CONTEXT = Depends(get_current_tenant_context)
REQUIRE_USERS_READ = Depends(require_permission("core.users.read"))
REQUIRE_USERS_CREATE = Depends(require_permission("core.users.create"))
REQUIRE_USERS_UPDATE = Depends(require_permission("core.users.update"))
REQUIRE_USERS_DISABLE = Depends(require_permission("core.users.disable"))


def _resolve_branch(db: Session, *, tenant_id: str, branch_id: str | None):
    if branch_id is None:
        return None
    branch = get_branch_for_tenant(db, tenant_id, branch_id)
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid branch for tenant",
        )
    return branch


@router.get("/categories", response_model=list[CoreUserCategoryRead])
def list_user_categories() -> list[CoreUserCategoryRead]:
    return [
        CoreUserCategoryRead(value=key, label=label)
        for key, (label, _role_names) in get_user_categories().items()
    ]


@router.get("", response_model=list[CoreUserRead])
def list_users(
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_READ,
) -> list[CoreUserRead]:
    return [
        CoreUserRead.model_validate(item)
        for item in list_core_users(db, tenant_id=tenant_context.current_tenant_id)
    ]


@router.get("/{user_id}", response_model=CoreUserRead)
def get_user(
    user_id: str,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_READ,
) -> CoreUserRead:
    item = get_core_user(db, tenant_id=tenant_context.current_tenant_id, user_id=user_id)
    if item is None:
        raise tenant_not_found("User")
    return CoreUserRead.model_validate(item)


@router.post("", response_model=CoreUserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CoreUserCreateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_CREATE,
) -> CoreUserRead:
    try:
        item = create_core_user(
            db,
            tenant_id=tenant_context.current_tenant_id,
            name=payload.name,
            email=str(payload.email),
            password=payload.password,
            branch=_resolve_branch(
                db,
                tenant_id=tenant_context.current_tenant_id,
                branch_id=payload.branch_id,
            ),
            category=payload.category,
            role_ids=payload.role_ids,
            warehouse_ids=payload.warehouse_ids,
            action_context=build_action_context(request, tenant_context),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CoreUserRead.model_validate(item)


@router.patch("/{user_id}", response_model=CoreUserRead)
def update_user(
    user_id: str,
    payload: CoreUserUpdateRequest,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_UPDATE,
) -> CoreUserRead:
    branch_was_provided = "branch_id" in payload.model_fields_set
    try:
        item = update_core_user(
            db,
            tenant_id=tenant_context.current_tenant_id,
            user_id=user_id,
            name=payload.name,
            email=str(payload.email) if payload.email is not None else None,
            password=payload.password,
            branch=_resolve_branch(
                db,
                tenant_id=tenant_context.current_tenant_id,
                branch_id=payload.branch_id,
            )
            if branch_was_provided
            else None,
            branch_was_provided=branch_was_provided,
            category=payload.category,
            role_ids=payload.role_ids,
            warehouse_ids=payload.warehouse_ids,
            action_context=build_action_context(request, tenant_context),
        )
        if item is None:
            raise tenant_not_found("User")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise handle_integrity_error(exc) from exc
    except (TenantScopeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CoreUserRead.model_validate(item)


@router.post("/{user_id}/disable", response_model=CoreUserRead)
def disable_user(
    user_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_DISABLE,
) -> CoreUserRead:
    item = set_core_user_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        user_id=user_id,
        is_active=False,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("User")
    db.commit()
    return CoreUserRead.model_validate(item)


@router.post("/{user_id}/enable", response_model=CoreUserRead)
def enable_user(
    user_id: str,
    request: Request,
    db: Session = DB_SESSION,
    tenant_context: TenantContext = TENANT_CONTEXT,
    _: User = REQUIRE_USERS_DISABLE,
) -> CoreUserRead:
    item = set_core_user_active(
        db,
        tenant_id=tenant_context.current_tenant_id,
        user_id=user_id,
        is_active=True,
        action_context=build_action_context(request, tenant_context),
    )
    if item is None:
        raise tenant_not_found("User")
    db.commit()
    return CoreUserRead.model_validate(item)
