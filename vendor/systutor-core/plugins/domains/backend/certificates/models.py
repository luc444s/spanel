"""Modelo del inventario de certificados de correo.

Solo metadatos y rutas. La serialización se comprueba en tests para que una
clave privada nunca pueda viajar en `state.json` ni en la API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from plugins.domains.backend.certificates.validation import parse_datetime

Scope = Literal["global", "tenant"]
Source = Literal["certbot", "static", "step-ca"]
Deployment = Literal["default", "sni", "none", "failed"]
Status = Literal["active", "expiring", "expired", "missing", "error"]

SCOPES: tuple[str, ...] = ("global", "tenant")
SOURCES: tuple[str, ...] = ("certbot", "static", "step-ca")
DEPLOYMENTS: tuple[str, ...] = ("default", "sni", "none", "failed")
STATUSES: tuple[str, ...] = ("active", "expiring", "expired", "missing", "error")


def _one_of(value: object, allowed: tuple[str, ...], default: str) -> str:
    candidate = value if isinstance(value, str) else default
    return candidate if candidate in allowed else default


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _sans(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip().lower() for item in value if isinstance(item, str) and item.strip()]


@dataclass(slots=True)
class Certificate:
    hostname: str
    name: str
    scope: Scope
    domain: str | None
    source: Source
    deployment: Deployment
    status: Status
    sans: list[str] = field(default_factory=list)
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

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        default_name: str,
        now: datetime,
        renew_days: int,
    ) -> Certificate:
        hostname = str(payload.get("hostname") or payload.get("name") or "").strip().lower()
        if not hostname:
            raise ValueError("certificado sin hostname")
        scope = _one_of(payload.get("scope"), SCOPES, "tenant")
        if scope not in ("global", "tenant"):
            scope = "tenant"
        domain = _optional_str(payload.get("domain"))
        if hostname == default_name or scope == "global":
            scope = "global"
            domain = None
        elif hostname.startswith("mail."):
            domain = hostname[5:]

        status = _one_of(payload.get("status"), STATUSES, "missing")
        not_before = _optional_str(payload.get("not_before"))
        not_after = _optional_str(payload.get("not_after"))
        status = _health(status, not_before, not_after, now=now, renew_days=renew_days)

        return cls(
            hostname=hostname,
            name=str(payload.get("name") or hostname).strip() or hostname,
            scope=scope,  # type: ignore[arg-type]
            domain=domain,
            source=_one_of(payload.get("source"), SOURCES, "certbot"),  # type: ignore[arg-type]
            deployment=_one_of(payload.get("deployment"), DEPLOYMENTS, "none"),  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            sans=_sans(payload.get("sans")),
            issuer=_optional_str(payload.get("issuer")),
            subject=_optional_str(payload.get("subject")),
            serial=_optional_str(payload.get("serial")),
            fingerprint=_optional_str(payload.get("fingerprint")),
            not_before=not_before,
            not_after=not_after,
            cert_path=_optional_str(payload.get("cert_path")),
            key_path=_optional_str(payload.get("key_path")),
            last_checked_at=_optional_str(payload.get("last_checked_at")),
            last_issued_at=_optional_str(payload.get("last_issued_at")),
            last_error=_optional_str(payload.get("last_error")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "name": self.name,
            "scope": self.scope,
            "domain": self.domain,
            "source": self.source,
            "deployment": self.deployment,
            "status": self.status,
            "sans": list(self.sans),
            "issuer": self.issuer,
            "subject": self.subject,
            "serial": self.serial,
            "fingerprint": self.fingerprint,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "cert_path": self.cert_path,
            "key_path": self.key_path,
            "last_checked_at": self.last_checked_at,
            "last_issued_at": self.last_issued_at,
            "last_error": self.last_error,
        }

    def visible_to(
        self,
        *,
        tenant_id: str,
        is_platform: bool,
        tenant_domains: set[str],
    ) -> bool:
        """Un tenant ve solo `mail.<su dominio>`; el admin ve todo."""

        if is_platform:
            return True
        if self.scope == "global" or self.domain is None:
            return False
        return self.domain.strip().lower() in tenant_domains


def _health(
    stored: str,
    not_before: str | None,
    not_after: str | None,
    *,
    now: datetime,
    renew_days: int,
) -> str:
    """La fecha manda sobre el estado guardado, salvo missing/error explícitos."""

    if stored in ("missing", "error"):
        return stored
    after = parse_datetime(not_after)
    if after is None:
        return "missing"
    if after <= now:
        return "expired"
    before = parse_datetime(not_before)
    if before is not None and before > now + timedelta(minutes=5):
        return "error"
    if after <= now + timedelta(days=renew_days):
        return "expiring"
    return "active"


def utcnow() -> datetime:
    return datetime.now(UTC)
