from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from plugins.tenant.backend.schemas import (
    AssignUserRequest,
    CreateTenantRequest,
    CreateUserInTenantRequest,
    ReassignUserRequest,
    TenantListResponse,
    TenantResponse,
    TenantUserResponse,
    TenantUsersResponse,
    UpdateTenantDomainRequest,
)
from plugins.tenant.backend.service import (
    assign_user_to_tenant,
    create_tenant,
    create_user_for_tenant,
    get_tenant_by_id,
    list_tenants,
    list_users_for_tenant,
    remove_user_from_tenant,
    update_tenant_domain,
)
from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_user
from systutor.kernel.auth.models import User
from systutor.kernel.auth.service import get_user_by_id

router = APIRouter(prefix="/tenants", tags=["tenants"])


def require_superadmin() -> Callable[..., User]:
    def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not current_user.is_superadmin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Superadmin access required",
            )
        return current_user

    return dependency


@router.get("", response_model=TenantListResponse)
def list_all_tenants(
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantListResponse:
    tenants = list_tenants(db)
    return TenantListResponse(
        tenants=[
            TenantResponse(
                id=t.id,
                name=t.name,
                slug=t.slug,
                domain=t.domain,
                is_active=t.is_active,
            )
            for t in tenants
        ]
    )


@router.post("", response_model=TenantResponse, status_code=201)
def create_new_tenant(
    body: CreateTenantRequest,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantResponse:
    tenant = create_tenant(db, name=body.name, slug=body.slug, domain=body.domain)
    create_user_for_tenant(
        db,
        tenant_id=tenant.id,
        email=body.user_email,
        password=body.user_password,
        full_name=body.user_full_name,
    )
    db.commit()
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        domain=tenant.domain,
        is_active=tenant.is_active,
    )


@router.put("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: str,
    body: UpdateTenantDomainRequest,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantResponse:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    updated = update_tenant_domain(db, tenant=tenant, domain=body.domain)
    db.commit()
    return TenantResponse(
        id=updated.id,
        name=updated.name,
        slug=updated.slug,
        domain=updated.domain,
        is_active=updated.is_active,
    )


@router.get("/{tenant_id}/users", response_model=TenantUsersResponse)
def list_tenant_users(
    tenant_id: str,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantUsersResponse:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    users = list_users_for_tenant(db, tenant_id)
    return TenantUsersResponse(
        users=[
            TenantUserResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                branch_id=u.branch_id,
            )
            for u in users
        ]
    )


@router.post("/{tenant_id}/users", response_model=TenantUserResponse, status_code=201)
def create_user_in_tenant(
    tenant_id: str,
    body: CreateUserInTenantRequest,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantUserResponse:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    user = create_user_for_tenant(
        db,
        tenant_id=tenant_id,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
    )
    db.commit()
    return TenantUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        branch_id=user.branch_id,
    )


@router.post("/{tenant_id}/users/assign", response_model=TenantUserResponse, status_code=201)
def assign_existing_user(
    tenant_id: str,
    body: AssignUserRequest,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantUserResponse:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    user = get_user_by_id(db, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        updated = assign_user_to_tenant(db, user=user, tenant_id=tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return TenantUserResponse(
        id=updated.id,
        email=updated.email,
        full_name=updated.full_name,
        is_active=updated.is_active,
        branch_id=updated.branch_id,
    )


@router.put("/{tenant_id}/users/{user_id}", response_model=TenantUserResponse)
def reassign_user(
    tenant_id: str,
    user_id: str,
    body: ReassignUserRequest,
    db: Session = Depends(get_db_session),
    _current_user: User = Depends(require_superadmin()),
) -> TenantUserResponse:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    target_tenant = get_tenant_by_id(db, body.target_tenant_id)
    if target_tenant is None:
        raise HTTPException(status_code=404, detail="Target tenant not found")
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not in this tenant")
    updated = remove_user_from_tenant(db, user=user, target_tenant_id=body.target_tenant_id)
    db.commit()
    return TenantUserResponse(
        id=updated.id,
        email=updated.email,
        full_name=updated.full_name,
        is_active=updated.is_active,
        branch_id=updated.branch_id,
    )
