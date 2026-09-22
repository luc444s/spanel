from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class UserProfile(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str
    branch_id: str | None
    branch_name: str | None
    email: str
    full_name: str
    is_active: bool
    is_superadmin: bool
    category: str | None
    permissions: list[str]
    warehouse_ids: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: UserProfile
