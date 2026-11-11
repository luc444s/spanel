"""Contratos HTTP del plugin de dominios."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


# ---- rutas gestionadas (A.SPEC 0060) --------------------------------------

_SAFE_PROXY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[a-z]+)?$")


def _path_value(value: str) -> str:
    path = value.strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or len(path) > 255
        or any(character in path for character in "`\r\n\t?#\"")
    ):
        raise ValueError("invalid path")
    return path.rstrip("/") or "/"


def _proxy_names(values: list[str], label: str) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = raw.strip()
        if not _SAFE_PROXY_NAME.fullmatch(value):
            raise ValueError(f"invalid {label}")
        if value in result:
            raise ValueError(f"duplicate {label}")
        result.append(value)
    if len(result) > 20:
        raise ValueError(f"too many {label}")
    return result


class RouteItem(BaseModel):
    id: str
    domain: str
    path: str
    internal_path: str
    strip_path: bool
    port: int
    entrypoints: list[str] = Field(default_factory=list)
    https: bool = True
    middlewares: list[str] = Field(default_factory=list)
    site_id: str
    available: bool = True


class RouteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=4, max_length=253)
    path: str = "/"
    internal_path: str = "/"
    strip_path: bool = False
    port: int = Field(ge=1, le=65535)
    entrypoints: list[str] = Field(default_factory=list, max_length=20)
    https: bool = True
    middlewares: list[str] = Field(default_factory=list, max_length=20)
    site_id: str = Field(min_length=1, max_length=504)

    @field_validator("path", "internal_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return _path_value(value)

    @field_validator("entrypoints", "middlewares")
    @classmethod
    def valid_names(cls, value: list[str]) -> list[str]:
        return _proxy_names(value, "value")

    @field_validator("site_id")
    @classmethod
    def valid_site(cls, value: str) -> str:
        site = value.strip()
        if any(character in site for character in "`\r\n\t "):
            raise ValueError("invalid site_id")
        return site


class RouteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str | None = Field(default=None, min_length=4, max_length=253)
    path: str | None = None
    internal_path: str | None = None
    strip_path: bool | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    entrypoints: list[str] | None = Field(default=None, max_length=20)
    https: bool | None = None
    middlewares: list[str] | None = Field(default=None, max_length=20)
    site_id: str | None = Field(default=None, min_length=1, max_length=504)

    @field_validator("path", "internal_path")
    @classmethod
    def valid_path(cls, value: str | None) -> str | None:
        return None if value is None else _path_value(value)

    @field_validator("entrypoints", "middlewares")
    @classmethod
    def valid_names(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _proxy_names(value, "valor")

    @field_validator("site_id")
    @classmethod
    def valid_site(cls, value: str | None) -> str | None:
        if value is None:
            return None
        site = value.strip()
        if any(character in site for character in "`\r\n\t "):
            raise ValueError("invalid site_id")
        return site

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("the update contains no fields")
        for name in (
            "domain",
            "path",
            "internal_path",
            "strip_path",
            "port",
            "entrypoints",
            "https",
            "middlewares",
            "site_id",
        ):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class RoutesResponse(BaseModel):
    items: list[RouteItem]


class AvailableDomainsResponse(BaseModel):
    items: list[DomainItem]
    entrypoints: list[str] = Field(default_factory=list)
    middlewares: list[str] = Field(default_factory=list)


class RouteProbeResponse(BaseModel):
    route_id: str
    domain: str
    path: str
    http_status: int | None = None
    certificate: CertificateResponse | None = None
