"""HTTP contracts for the Cloudflare OAuth and discovery router."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
    """Record content is intentionally absent from the public contract."""

    id: str
    name: str
    type: str
    ttl: int = 0
    proxied: bool = False
    created_on: str | None = None
    modified_on: str | None = None


class CloudflareRecordListResponse(BaseModel):
    zone_id: str
    items: list[CloudflareRecordItem] = Field(default_factory=list)
