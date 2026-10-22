"""Request and response contracts for the hosting routes.

The requests use `extra="forbid"` on purpose: a client cannot smuggle a
`tenant_id`, a container name or any other Docker selector into the body. The
container is always resolved from the owned `hosting_sites` row.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HostingSiteSummary(BaseModel):
    id: str
    container_name: str
    domain: str | None
    status: str
    managed: bool


class HostingSiteListResponse(BaseModel):
    sites: list[HostingSiteSummary]


class WordPressUserSummary(BaseModel):
    id: int
    login: str
    email: str
    status: int
    role: str


class WordPressUserListResponse(BaseModel):
    site_id: str
    container_name: str
    users: list[WordPressUserSummary]


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=8)


class ResetPasswordResponse(BaseModel):
    site_id: str
    user_id: int
    login: str
    generated: bool
    password: str | None = None
