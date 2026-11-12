"""Public nameserver configuration and deterministic apex reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from plugins.domains.backend.dns.errors import DnsValidationError
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.validation import (
    canonical_name,
    normalize_rrset,
    normalize_zone_name,
    validate_ttl,
)

NOT_PHYSICALLY_REDUNDANT = (
    "ns1 and ns2 may resolve to the same address; this is not physical redundancy"
)


@dataclass(frozen=True)
class PublicNameserverSettings:
    ns1_hostname: str
    ns2_hostname: str
    ns1_ipv4: str | None = None
    ns1_ipv6: str | None = None
    ns2_ipv4: str | None = None
    ns2_ipv6: str | None = None
    soa_contact: str = ""
    default_ttl: int = 300
    version: int = 0

    @property
    def warning(self) -> str:
        return NOT_PHYSICALLY_REDUNDANT

    def to_dict(self) -> dict[str, object]:
        return {
            "ns1": self.ns1_hostname,
            "ns2": self.ns2_hostname,
            "ns1_ipv4": self.ns1_ipv4,
            "ns1_ipv6": self.ns1_ipv6,
            "ns2_ipv4": self.ns2_ipv4,
            "ns2_ipv6": self.ns2_ipv6,
            "soa_contact": self.soa_contact,
            "default_ttl": self.default_ttl,
            "version": self.version,
            "physical_redundancy": False,
            "warning": self.warning,
        }


def _hostname(value: str, *, label: str) -> str:
    raw = (value or "").strip().lower().removesuffix(".")
    if not raw or len(raw) > 253:
        raise DnsValidationError(f"{label} must be a DNS hostname")
    try:
        normalized = normalize_zone_name(raw)
    except DnsValidationError as exc:
        raise DnsValidationError(f"{label} must be a DNS hostname") from exc
    return normalized


def _soa_contact(value: str, *, zone: str | None = None) -> str:
    raw = (value or "").strip().lower().removesuffix(".")
    if "@" in raw:
        local, domain = raw.split("@", 1)
        if not local or not domain or "." in local:
            raise DnsValidationError("SOA contact email is invalid")
        raw = f"{local}.{domain}"
    if not raw:
        if zone is None:
            raise DnsValidationError("SOA contact is required")
        raw = f"hostmaster.{zone}"
    return _hostname(raw, label="SOA contact")


def build_settings(
    *,
    ns1_hostname: str,
    ns2_hostname: str,
    ns1_ipv4: str | None = None,
    ns1_ipv6: str | None = None,
    ns2_ipv4: str | None = None,
    ns2_ipv6: str | None = None,
    soa_contact: str,
    default_ttl: int | str = 300,
    version: int = 0,
) -> PublicNameserverSettings:
    settings = PublicNameserverSettings(
        ns1_hostname=_hostname(ns1_hostname, label="ns1"),
        ns2_hostname=_hostname(ns2_hostname, label="ns2"),
        ns1_ipv4=_optional_ip(ns1_ipv4, 4),
        ns1_ipv6=_optional_ip(ns1_ipv6, 6),
        ns2_ipv4=_optional_ip(ns2_ipv4, 4),
        ns2_ipv6=_optional_ip(ns2_ipv6, 6),
        soa_contact=_soa_contact(soa_contact),
        default_ttl=validate_ttl(default_ttl),
        version=int(version),
    )
    if settings.ns1_hostname == settings.ns2_hostname and not (
        settings.ns1_ipv4 or settings.ns1_ipv6 or settings.ns2_ipv4 or settings.ns2_ipv6
    ):
        # Two identical names with no address are still valid; the warning is
        # what prevents the UI from claiming redundancy.
        pass
    return settings


def _optional_ip(value: str | None, version: int) -> str | None:
    import ipaddress

    raw = (value or "").strip()
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise DnsValidationError(f"invalid IPv{version} address") from exc
    if address.version != version:
        raise DnsValidationError(f"expected an IPv{version} address")
    return str(address)


def with_version(settings: PublicNameserverSettings, version: int) -> PublicNameserverSettings:
    return replace(settings, version=int(version))


def default_serial() -> int:
    now = datetime.now(UTC)
    return int(now.strftime("%Y%m%d%H"))


def seed_rrsets(
    zone: str,
    settings: PublicNameserverSettings,
    *,
    serial: int | None = None,
) -> list[DnsRRset]:
    zone_name = normalize_zone_name(zone)
    serial_value = int(serial) if serial is not None else default_serial()
    soa_content = (
        f"{canonical_name(settings.ns1_hostname)} "
        f"{canonical_name(_soa_contact(settings.soa_contact, zone=zone_name))} "
        f"{serial_value} 3600 600 1209600 {settings.default_ttl}"
    )
    rrsets = [
        normalize_rrset(
            zone=zone_name,
            name=zone_name,
            type="SOA",
            ttl=settings.default_ttl,
            values=[soa_content],
            allow_soa=True,
        ),
        normalize_rrset(
            zone=zone_name,
            name=zone_name,
            type="NS",
            ttl=settings.default_ttl,
            values=[
                canonical_name(settings.ns1_hostname),
                canonical_name(settings.ns2_hostname),
            ],
        ),
    ]
    glue: dict[tuple[str, str], list[str]] = {}
    for hostname, ipv4, ipv6 in (
        (settings.ns1_hostname, settings.ns1_ipv4, settings.ns1_ipv6),
        (settings.ns2_hostname, settings.ns2_ipv4, settings.ns2_ipv6),
    ):
        if hostname != zone_name and not hostname.endswith(f".{zone_name}"):
            continue
        if ipv4:
            glue.setdefault((hostname, "A"), []).append(ipv4)
        if ipv6:
            glue.setdefault((hostname, "AAAA"), []).append(ipv6)
    for (owner, type_), values in sorted(glue.items()):
        rrsets.append(
            normalize_rrset(
                zone=zone_name,
                name=owner,
                type=type_,
                ttl=settings.default_ttl,
                values=sorted(set(values)),
            )
        )
    return rrsets


def reconcile_rrsets(
    zone: DnsZone,
    settings: PublicNameserverSettings,
    *,
    serial: int | None = None,
) -> list[DnsRRset]:
    """Apex SOA/NS and in-zone glue that must be replaced for this zone."""
    desired = seed_rrsets(zone.name, settings, serial=serial)
    return [
        rrset
        for rrset in desired
        if rrset.name == zone.name or rrset.type in {"A", "AAAA"}
    ]


def settings_drift(
    current: PublicNameserverSettings | None,
    desired: PublicNameserverSettings,
) -> list[str]:
    if current is None:
        return ["settings are not configured"]
    fields = (
        "ns1_hostname",
        "ns2_hostname",
        "ns1_ipv4",
        "ns1_ipv6",
        "ns2_ipv4",
        "ns2_ipv6",
        "soa_contact",
        "default_ttl",
    )
    return [
        name
        for name in fields
        if getattr(current, name, None) != getattr(desired, name, None)
    ]
