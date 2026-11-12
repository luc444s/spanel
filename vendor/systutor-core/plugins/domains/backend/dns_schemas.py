"""HTTP contracts for the tenant DNS router."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoctorResponse(BaseModel):
    ok: bool
    authenticated: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    message: str | None = None


class NameserverSettingsInput(_Strict):
    ns1: str = Field(min_length=1, max_length=253)
    ns2: str = Field(min_length=1, max_length=253)
    ns1_ipv4: str | None = None
    ns1_ipv6: str | None = None
    ns2_ipv4: str | None = None
    ns2_ipv6: str | None = None
    soa_contact: str = Field(min_length=1, max_length=253)
    default_ttl: int = 300


class NameserverResponse(BaseModel):
    configured: bool
    ns1: str | None = None
    ns2: str | None = None
    ns1_ipv4: str | None = None
    ns1_ipv6: str | None = None
    ns2_ipv4: str | None = None
    ns2_ipv6: str | None = None
    soa_contact: str | None = None
    default_ttl: int | None = None
    version: int = 0
    physical_redundancy: bool = False
    warning: str


class NameserverPlanRequest(_Strict):
    settings: NameserverSettingsInput | None = None
    zones: list[str] | None = None


class NameserverApplyRequest(_Strict):
    settings: NameserverSettingsInput
    zones: list[str]
    zone_versions: dict[str, str] = Field(default_factory=dict)
    expected_version: int | None = None


class NameserverVerifyRequest(_Strict):
    zone: str = Field(min_length=1)
    resolver: str = Field(min_length=1, max_length=253)
    port: int = Field(default=53, ge=1, le=65535)
    timeout: float = Field(default=5.0, gt=0, le=120)


class ZoneCreateRequest(_Strict):
    zone_name: str = Field(min_length=1, max_length=253)


class ZoneItem(BaseModel):
    name: str
    kind: str = "Native"
    version: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ZoneListResponse(BaseModel):
    items: list[ZoneItem]


class RrsetInput(_Strict):
    name: str = Field(min_length=1, max_length=253)
    type: str = Field(min_length=1, max_length=16)
    ttl: int = Field(default=300, ge=1, le=604800)
    values: list[str] = Field(min_length=1)


class RrsetItem(BaseModel):
    name: str
    type: str
    ttl: int
    records: list[str] = Field(default_factory=list)


class ZoneResponse(BaseModel):
    name: str
    kind: str
    version: str | None = None
    serial: int | None = None
    rrsets: list[RrsetItem] = Field(default_factory=list)


class RrsetPlanRequest(_Strict):
    rrsets: list[RrsetInput]


class RrsetApplyRequest(_Strict):
    rrsets: list[RrsetInput]
    expected_version: str = Field(min_length=1)


class RrsetDeleteRequest(_Strict):
    name: str = Field(min_length=1, max_length=253)
    type: str = Field(min_length=1, max_length=16)
    expected_version: str = Field(min_length=1)


class ZoneImportRequest(_Strict):
    content: str = Field(min_length=1)
    expected_version: str | None = None


class ResolveResponse(BaseModel):
    domain: str
    zone: str
    relative_name: str


class PlanResponse(BaseModel):
    zone: str
    version: str
    drift: bool
    changes: list[dict[str, Any]] = Field(default_factory=list)
    not_physically_redundant: bool = True


class NameserverPlanResponse(BaseModel):
    settings_version: int
    settings_changed: bool
    settings_fields: list[str] = Field(default_factory=list)
    zones: list[dict[str, Any]] = Field(default_factory=list)
    zone_versions: dict[str, str] = Field(default_factory=dict)
    drift: bool
    physical_redundancy: bool = False
    warning: str


class NameserverApplyResponse(BaseModel):
    settings: dict[str, Any]
    changed_zones: list[str] = Field(default_factory=list)
    zone_versions: dict[str, str] = Field(default_factory=dict)
    physical_redundancy: bool = False
    warning: str


class NameserverVerifyResponse(BaseModel):
    zone: str
    resolver: str
    port: int
    transports: dict[str, Any] = Field(default_factory=dict)
    physical_redundancy: bool = False
    warning: str


class DeleteResponse(BaseModel):
    zone: str
    deleted: bool
    released_ownership: bool | None = None
