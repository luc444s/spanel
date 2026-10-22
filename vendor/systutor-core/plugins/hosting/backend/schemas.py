"""Request and response contracts for the hosting routes.

The requests use `extra="forbid"` on purpose: a client cannot smuggle a
`tenant_id`, a container name or any other Docker selector into the body. The
container is always resolved from the owned `hosting_sites` row.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=100)
    domain: str = Field(min_length=4, max_length=253)
    admin_email: str = Field(min_length=3, max_length=254)

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, value: str) -> str:
        import re

        value = value.strip().lower()
        if not re.fullmatch(
            r"(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
            value,
        ):
            raise ValueError("Invalid domain")
        return value

    @field_validator("admin_email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value


class CreateSiteResponse(BaseModel):
    site: HostingSiteSummary
    admin_password: str


class SiteLogsResponse(BaseModel):
    logs: str


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
