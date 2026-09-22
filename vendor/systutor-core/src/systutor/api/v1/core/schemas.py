from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CoreUserRead(BaseModel):
    id: str
    tenant_id: str
    branch_id: str | None
    name: str
    email: str
    active: bool
    category: str | None
    roles: list[str]
    warehouse_ids: list[str]
    created_at: datetime
    updated_at: datetime


class CoreUserCreateRequest(BaseModel):
    name: str
    email: str
    password: str
    branch_id: str | None = None
    category: str | None = None
    role_ids: list[str] = []
    warehouse_ids: list[str] = []


class CoreUserUpdateRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    branch_id: str | None = None
    category: str | None = None
    role_ids: list[str] | None = None
    warehouse_ids: list[str] | None = None


class CoreUserCategoryRead(BaseModel):
    value: str
    label: str


class CoreRoleRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    permissions: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime


class CoreRoleCreateRequest(BaseModel):
    name: str
    permission_names: list[str] = []


class CoreRoleUpdateRequest(BaseModel):
    name: str | None = None
    permission_names: list[str] | None = None


class CoreBranchRead(BaseModel):
    id: str
    tenant_id: str
    name: str
    code: str
    active: bool
    created_at: datetime
    updated_at: datetime


class CoreBranchCreateRequest(BaseModel):
    name: str
    code: str


class CoreBranchUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None


class CorePermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    created_at: datetime


class CorePluginMigrateRequest(BaseModel):
    target_revision: str | None = None


class CoreDocumentRenderRequest(BaseModel):
    module: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=100)
    entity_id: str = Field(min_length=1, max_length=100)
    template_code: str = Field(min_length=1, max_length=100)
    payload: dict = Field(default_factory=dict)
    status: str = Field(default="DRAFT", max_length=30)


class CoreDocumentVersionRead(BaseModel):
    id: str
    tenant_id: str
    module: str
    entity_type: str
    entity_id: str
    template_code: str
    version_number: int
    status: str
    title: str | None
    file_path: str
    sha256: str
    created_by: str | None
    created_at: datetime


class CoreDocumentSignedDownloadRead(BaseModel):
    url: str
    expires_at: datetime


class CoreSignatureSessionCreateRequest(BaseModel):
    document_version_id: str
    signer_name: str | None = None
    signer_email: str | None = None
    signer_phone: str | None = None
    signer_role: str | None = None
    provider: str = "INTERNAL"
    verification_channel: str = "IN_APP"


class CoreSignatureSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    document_version_id: str
    signer_name: str | None
    signer_email: str | None
    signer_phone: str | None
    signer_role: str | None
    provider: str
    status: str
    verification_channel: str
    verification_ref: str | None
    completed_at: datetime | None
    created_at: datetime


class CoreSignatureCompleteRequest(BaseModel):
    signer_name: str
    signer_email: str | None = None
    signer_phone: str | None = None
    evidence_type: str = "MANUAL_CONFIRMATION"
    evidence_payload: dict = Field(default_factory=dict)
