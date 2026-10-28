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
    outside_tenant: bool = False


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


class CreateDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=4, max_length=253)
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


class CertificateItem(BaseModel):
    """Un certificado de correo, solo metadatos y rutas."""

    hostname: str
    name: str
    scope: Literal["global", "tenant"]
    domain: str | None = None
    source: Literal["certbot", "static", "step-ca"]
    deployment: Literal["default", "sni", "none", "failed"]
    status: Literal["active", "expiring", "expired", "missing", "error"]
    sans: list[str] = Field(default_factory=list)
    issuer: str | None = None
    subject: str | None = None
    serial: str | None = None
    fingerprint: str | None = None
    not_before: str | None = None
    not_after: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    last_checked_at: str | None = None
    last_issued_at: str | None = None
    last_error: str | None = None


class CertificatesResponse(BaseModel):
    items: list[CertificateItem] = Field(default_factory=list)
    stale: bool = True
    generated_at: str | None = None
    agent_last_seen: str | None = None
    error: str | None = None


class CertificateRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ensure", "force"] = "ensure"
    reason: str | None = Field(default=None, max_length=200)


class CertificateRequestResponse(BaseModel):
    job_id: str
    hostname: str
    state: Literal["queued"] = "queued"
    poll_url: str


class CertificateJobResponse(BaseModel):
    job_id: str
    hostname: str
    state: Literal["queued", "running", "succeeded", "failed"]
    action: str | None = None
    requested_at: str | None = None
    updated_at: str | None = None
    message: str | None = None
    error: str | None = None


class CertificateRefreshResponse(BaseModel):
    requested: bool
    request_id: str | None = None
    state: Literal["queued"] = "queued"


class CertificateHealthItem(BaseModel):
    """Salud de un hostname; solo metadatos y booleanos por puerto."""

    hostname: str
    serial: str | None = None
    served_serial: str | None = None
    no_sni_serial: str | None = None
    not_after: str | None = None
    days_left: int | None = None
    sni: dict[str, bool] = Field(default_factory=dict)
    no_sni: bool | None = None
    no_sni_ports: dict[str, bool] = Field(default_factory=dict)
    ok: bool | None = None
    last_verified_at: str | None = None
    last_ok_at: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None


class CertificateDefaultHealth(BaseModel):
    name: str
    serial: str | None = None
    no_sni_serial: str | None = None
    ok: bool | None = None


class CertificateHealthResponse(BaseModel):
    generated_at: str | None = None
    agent_state: str = "unknown"
    agent_last_run_at: str | None = None
    interval_seconds: int = 900
    stale: bool = True
    default: CertificateDefaultHealth | None = None
    certificates: list[CertificateHealthItem] = Field(default_factory=list)
    critical_count: int = 0
    warning_count: int = 0


class CertificateAlertItem(BaseModel):
    code: str
    severity: Literal["warning", "critical"] = "warning"
    hostname: str | None = None
    message: str
    created_at: str | None = None


class CertificateAlertsResponse(BaseModel):
    generated_at: str | None = None
    alerts: list[CertificateAlertItem] = Field(default_factory=list)
    delivery_errors: list[str] = Field(default_factory=list)


class CertificateAdminActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)
