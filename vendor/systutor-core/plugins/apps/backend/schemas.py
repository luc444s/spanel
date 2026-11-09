"""Public API schemas; secrets and server-owned runtime identifiers stay private."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ManifestStartRequest(BaseModel):
    owner_type: Literal["personal", "organization"] = "personal"
    organization: str | None = Field(default=None, max_length=39)

    @model_validator(mode="after")
    def organization_required_for_org(self):
        if self.owner_type == "organization" and not self.organization:
            raise ValueError("organization is required for an organization app")
        if self.owner_type == "personal" and self.organization:
            raise ValueError("organization must be empty for a personal app")
        return self


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    installation_id: str = Field(min_length=1, max_length=36)
    repository_id: str = Field(min_length=1, max_length=36)
    branch: str | None = Field(default=None, min_length=1, max_length=255)
    compose_file: str | None = Field(default=None, max_length=510)
    domain: str | None = Field(default=None, max_length=255)
    ingress_service: str | None = Field(default=None, max_length=255)
    ingress_port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("compose_file")
    @classmethod
    def safe_compose(cls, value: str | None) -> str | None:
        if value and (value.startswith("/") or ".." in value.split("/")):
            raise ValueError("compose_file must stay inside the repository")
        return value

    @field_validator("branch")
    @classmethod
    def safe_branch(cls, value: str | None) -> str | None:
        if value and (value.startswith("-") or any(ch in value for ch in "\x00\r\n")):
            raise ValueError("branch is invalid")
        return value

    @model_validator(mode="after")
    def complete_ingress(self):
        values = (self.domain, self.ingress_service, self.ingress_port)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("domain, ingress_service and ingress_port must be provided together")
        return self


class SourceUpdate(BaseModel):
    installation_id: str = Field(min_length=1, max_length=36)
    repository_id: str = Field(min_length=1, max_length=36)
    branch: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("branch")
    @classmethod
    def safe_branch(cls, value: str | None) -> str | None:
        if value and (value.startswith("-") or any(ch in value for ch in "\x00\r\n")):
            raise ValueError("branch is invalid")
        return value


class DeployRequest(BaseModel):
    environment: list[tuple[str, str]] = Field(default_factory=list, max_length=100)


class DeleteRequest(BaseModel):
    confirm: str
    purge_data: bool = False


class ApplicationView(BaseModel):
    id: str
    name: str
    slug: str
    repository: str
    branch: str
    compose_file: str | None
    domain: str | None
    ingress_service: str | None
    ingress_port: int | None
    status: str
    source_connection_user_id: str
    source_installation_id: str | None
    source_repository_id: str | None
    source_status: str

    model_config = {"from_attributes": True}


class OperationView(BaseModel):
    operation_id: str
    application_id: str | None
    kind: str
    status: str
    poll_url: str


class OperationDetail(BaseModel):
    id: str
    application_id: str | None
    subject_type: str | None
    subject_id: str | None
    kind: str
    status: str
    phase: str
    report: dict | None
    result: dict | None
    error_code: str | None
    public_error: str | None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProviderView(BaseModel):
    id: str
    github_app_id: int
    app_slug: str
    name: str
    owner_login: str | None
    owner_type: str | None
    status: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class InstallationView(BaseModel):
    id: str
    provider_id: str
    github_installation_id: int
    account_login: str
    account_type: str | None
    repository_selection: str
    permissions: dict
    status: str
    repository_count: int = 0
    installed_at: datetime | None = None
    last_synced_at: datetime | None = None
    suspended_at: datetime | None = None
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


class RepositoryView(BaseModel):
    id: str
    github_repository_id: int
    full_name: str
    private: bool
    default_branch: str | None
    archived: bool
    disabled: bool
    observed_at: datetime | None = None
    removed_at: datetime | None = None

    model_config = {"from_attributes": True}
