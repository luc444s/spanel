from __future__ import annotations

from pydantic import BaseModel


class CreateTenantRequest(BaseModel):
    name: str
    slug: str
    domain: str | None = None
    user_email: str
    user_password: str
    user_full_name: str = ""


class UpdateTenantDomainRequest(BaseModel):
    domain: str | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    domain: str | None
    is_active: bool


class TenantListResponse(BaseModel):
    tenants: list[TenantResponse]


class AssignUserRequest(BaseModel):
    user_id: str


class CreateUserInTenantRequest(BaseModel):
    email: str
    password: str
    full_name: str = ""


class ReassignUserRequest(BaseModel):
    target_tenant_id: str


class TenantUserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    branch_id: str | None


class TenantUsersResponse(BaseModel):
    users: list[TenantUserResponse]
