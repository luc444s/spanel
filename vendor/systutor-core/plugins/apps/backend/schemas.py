"""Public API schemas; secrets and server-owned runtime identifiers stay private."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class OAuthStartRequest(BaseModel):
    redirect: str = "/app/apps"


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str = Field(default="main", min_length=1, max_length=255)
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

    @model_validator(mode="after")
    def complete_ingress(self):
        values = (self.domain, self.ingress_service, self.ingress_port)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("domain, ingress_service and ingress_port must be provided together")
        return self


class DeployRequest(BaseModel):
    environment: list[tuple[str, str]] = Field(default_factory=list, max_length=100)


class ReconnectRequest(BaseModel):
    source_connection_user_id: str


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

    model_config = {"from_attributes": True}


class OperationView(BaseModel):
    operation_id: str
    application_id: str
    kind: str
    status: str
    poll_url: str


class OperationDetail(BaseModel):
    id: str
    application_id: str
    kind: str
    status: str
    phase: str
    report: dict | None
    result: dict | None
    error_code: str | None
    public_error: str | None

    model_config = {"from_attributes": True}
