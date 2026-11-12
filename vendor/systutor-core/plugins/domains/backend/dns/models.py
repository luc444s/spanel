"""Internal DNS models.

Names are normalized without a trailing dot inside PanelTutor.  The PowerDNS
client adds the trailing dot at the HTTP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DnsRecord:
    content: str
    disabled: bool = False

    def to_powerdns(self) -> dict[str, object]:
        return {"content": self.content, "disabled": self.disabled}


@dataclass(frozen=True)
class DnsRRset:
    name: str
    type: str
    ttl: int
    records: tuple[DnsRecord, ...] = ()

    def values(self) -> tuple[str, ...]:
        return tuple(record.content for record in self.records if not record.disabled)

    def to_powerdns(self) -> dict[str, object]:
        return {
            "name": f"{self.name}.",
            "type": self.type,
            "ttl": self.ttl,
            "records": [record.to_powerdns() for record in self.records],
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "ttl": self.ttl,
            "records": list(self.values()),
        }


@dataclass(frozen=True)
class DnsZone:
    name: str
    kind: str = "Native"
    rrsets: tuple[DnsRRset, ...] = field(default_factory=tuple)

    def rrset(self, name: str, type: str) -> DnsRRset | None:
        needle_type = type.upper()
        for rrset in self.rrsets:
            if rrset.name == name and rrset.type == needle_type:
                return rrset
        return None

    @property
    def serial(self) -> int | None:
        soa = self.rrset(self.name, "SOA")
        if soa is None or not soa.values():
            return None
        fields = soa.values()[0].split()
        if len(fields) < 3:
            return None
        try:
            return int(fields[2])
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "serial": self.serial,
            "version": None,
            "rrsets": [rrset.to_dict() for rrset in sorted(self.rrsets, key=_rrset_key)],
        }


def _rrset_key(rrset: DnsRRset) -> tuple[str, str]:
    return (rrset.name, rrset.type)


def parse_powerdns_rrset(payload: dict[str, Any]) -> DnsRRset:
    name = str(payload.get("name", "")).strip().rstrip(".").lower()
    type_ = str(payload.get("type", "")).strip().upper()
    ttl = int(payload.get("ttl", 0) or 0)
    records: list[DnsRecord] = []
    for raw in payload.get("records", []) or []:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if content:
            records.append(DnsRecord(content=content, disabled=bool(raw.get("disabled"))))
    return DnsRRset(name=name, type=type_, ttl=ttl, records=tuple(records))


def parse_powerdns_zone(payload: dict[str, Any]) -> DnsZone:
    name = str(payload.get("name", "")).strip().rstrip(".").lower()
    kind = str(payload.get("kind", "Native") or "Native")
    rrsets = tuple(
        parse_powerdns_rrset(item)
        for item in payload.get("rrsets", []) or []
        if isinstance(item, dict)
    )
    return DnsZone(name=name, kind=kind, rrsets=rrsets)
