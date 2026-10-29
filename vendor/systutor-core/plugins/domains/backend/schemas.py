"""Contratos HTTP del plugin de dominios."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainItem(BaseModel):
    domain: str
    site_id: str | None = None
    container: str | None = None
    owner: Literal["hosting", "domains"]
    editable: bool = False
    conflict: bool = False
    published: bool = True
    tenant_id: str | None = None
    tenant_name: str | None = None


class DomainCheckResponse(BaseModel):
    available: bool
    reason: str | None = None
    suffix: str | None = None


class SiteOption(BaseModel):
    site_id: str
    container: str
    primary_domain: str | None = None


class DomainsResponse(BaseModel):
    items: list[DomainItem]
    sites: list[SiteOption]
    proxy_available: bool = True
    proxy_error: str | None = None


class CreateDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str | None = Field(default=None, min_length=1, max_length=64)
    domain: str = Field(min_length=4, max_length=253)
    www: bool = False


class LinkDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1, max_length=64)
    www: bool = False


class CreateDomainsResponse(BaseModel):
    items: list[DomainItem]


class CertificateResponse(BaseModel):
    domain: str
    state: str
    issuer: str | None = None
    subject: str | None = None
    not_after: str | None = None
    sans: list[str] = Field(default_factory=list)
    verified: bool = False
    error: str | None = None
