"""HTTP contracts for the Cloudflare OAuth and discovery router."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthStartResponse(BaseModel):
    tenant_id: str
    flow_id: str
    authorization_url: str
    redirect_uri: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None


class OAuthClientStatusResponse(BaseModel):
    registered: bool
    needs_repair: bool = False
    account_id: str | None = None
    client_id: str | None = None
    redirect_uri: str | None = None
    scopes: list[str] = Field(default_factory=list)


class OAuthClientRegistrationRequest(BaseModel):
    """The bootstrap token is optional in the type so validation never echoes it."""

    bootstrap_token: str | None = None
    account_id: str | None = None


class ConnectionStatusResponse(BaseModel):
    connected: bool
    tenant_id: str
    account_id: str | None = None
    account_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    status: str = "disconnected"
    updated_at: str | None = None


class DisconnectRequest(_Strict):
    apply: bool = True


class DisconnectResponse(BaseModel):
    tenant_id: str
    applied: bool
    disconnected: bool
    already_disconnected: bool = False
    revoked: bool = False
    account_id: str | None = None
    account_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    pending_flows: int = 0
    cancelled_flows: int = 0


class CloudflareZoneItem(BaseModel):
    id: str
    name: str
    status: str = ""
    account_id: str = ""
    account_name: str = ""
    name_servers: list[str] = Field(default_factory=list)
    created_on: str | None = None
    modified_on: str | None = None
    record_count: int = 0


class CloudflareZoneListResponse(BaseModel):
    items: list[CloudflareZoneItem] = Field(default_factory=list)


class CloudflareRecordItem(BaseModel):
    """Tenant-scoped DNS record returned only to an authorized connection."""

    id: str
    name: str
    type: str
    content: str
    ttl: int = 0
    proxied: bool = False
    priority: int | None = None
    created_on: str | None = None
    modified_on: str | None = None


class CloudflareRecordListResponse(BaseModel):
    zone_id: str
    items: list[CloudflareRecordItem] = Field(default_factory=list)


class CloudflareRecordWrite(_Strict):
    name: str = Field(min_length=1, max_length=253)
    type: str = Field(min_length=1, max_length=16)
    content: str = Field(min_length=1, max_length=65535)
    ttl: int = Field(default=1, ge=1, le=2147483647)
    proxied: bool = False
    priority: int | None = Field(default=None, ge=0, le=65535)

    @field_validator("name", "type")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()

    @field_validator("type")
    @classmethod
    def uppercase_type(cls, value: str) -> str:
        return value.upper()


class CloudflareRecordDeleteResponse(BaseModel):
    zone_id: str
    record_id: str
    deleted: bool


class CloudflareProvisionRequest(_Strict):
    domain: str = Field(min_length=1, max_length=254)
    ipv4: str = Field(min_length=1, max_length=45)
    www: bool = False
    replace: bool = False


class CloudflareProvisionRecord(BaseModel):
    id: str | None = None
    name: str
    type: str
    content: str
    ttl: int
    proxied: bool


class CloudflareProvisionPlanResponse(BaseModel):
    domain: str
    ipv4: str
    www: bool
    replace: bool
    zone_action: str
    zone: CloudflareZoneItem | None = None
    nameservers: list[str] = Field(default_factory=list)
    desired_records: list[CloudflareProvisionRecord] = Field(default_factory=list)
    conflicts: list[CloudflareProvisionRecord] = Field(default_factory=list)
    actions: list[dict[str, object]] = Field(default_factory=list)
    drift: bool


class CloudflareProvisionApplyResponse(CloudflareProvisionPlanResponse):
    applied: bool
    records: list[CloudflareProvisionRecord] = Field(default_factory=list)
