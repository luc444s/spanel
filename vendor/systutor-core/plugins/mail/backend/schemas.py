from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class CreateAccountRequest(BaseModel):
    username: str
    password: str
    domain: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9.+_-]{1,64}$", v):
            raise ValueError(
                "Username: only letters, digits, dots, plus, hyphens, underscores; max 64 chars"
            )
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class ChangePasswordRequest(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class MailAccountsResponse(BaseModel):
    domain: str
    domains: list[str] = Field(default_factory=list)
    accounts: list[str]


class MailMessageResponse(BaseModel):
    message: str
    email: str | None = None
