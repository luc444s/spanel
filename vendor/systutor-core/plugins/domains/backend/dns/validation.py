"""DNS name, record and RRset policy.

All validation is pure and stdlib-only so the same rules are used by the CLI,
the FastAPI service and the zone-file importer.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence

from plugins.domains.backend.dns.errors import DnsValidationError
from plugins.domains.backend.dns.models import DnsRecord, DnsRRset, DnsZone

SUPPORTED_TYPES: tuple[str, ...] = (
    "A",
    "AAAA",
    "CNAME",
    "MX",
    "TXT",
    "CAA",
    "SRV",
    "NS",
)
IMPORT_TYPES: tuple[str, ...] = (*SUPPORTED_TYPES, "SOA")
TYPE_ORDER = {name: index for index, name in enumerate(("SOA", *SUPPORTED_TYPES))}

MAX_TTL = 604800
_LABEL = re.compile(r"^(?:\*|[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)$")
_HOSTNAME = re.compile(
    r"^(?:\*\.)?[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
    r"(?:\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)*$"
)
_CAA_TAGS = {"issue", "issuewild", "iodef"}


def normalize_zone_name(value: str) -> str:
    text = (value or "").strip().lower().removesuffix(".")
    if not text or text == "." or len(text) > 253:
        raise DnsValidationError("invalid zone name")
    if ".." in text or any(character in text for character in "/:@?# \t\r\n"):
        raise DnsValidationError("invalid zone name")
    labels = text.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        raise DnsValidationError("invalid zone name")
    if any(label == "*" for label in labels):
        raise DnsValidationError("a zone name cannot contain a wildcard")
    return text


def canonical_name(value: str) -> str:
    text = (value or "").strip()
    return text if text.endswith(".") else f"{text}."


def normalize_owner(value: str, zone: str) -> str:
    zone_name = normalize_zone_name(zone)
    raw = (value or "").strip().lower()
    if raw in {"", "@"}:
        return zone_name
    if raw.endswith("."):
        raw = raw[:-1]
    if not raw or len(raw) > 253 or ".." in raw:
        raise DnsValidationError("invalid record owner")
    if any(character in raw for character in "/:@?# \t\r\n"):
        raise DnsValidationError("invalid record owner")
    if raw == zone_name:
        return zone_name
    if raw.endswith(f".{zone_name}"):
        labels = raw.split(".")
    elif "." in raw:
        raise DnsValidationError("record owner is outside the zone")
    else:
        labels = f"{raw}.{zone_name}".split(".")
    if len(labels) > 127 or not all(_LABEL.fullmatch(label) for label in labels):
        raise DnsValidationError("invalid record owner")
    if any(label == "*" for label in labels[1:]):
        raise DnsValidationError("a wildcard is only valid as the first label")
    return ".".join(labels)


def validate_ttl(value: int | str) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError) as exc:
        raise DnsValidationError("TTL must be an integer") from exc
    if not 1 <= ttl <= MAX_TTL:
        raise DnsValidationError(f"TTL must be between 1 and {MAX_TTL}")
    return ttl


def _hostname(value: str, zone: str, *, allow_root: bool = False) -> str:
    raw = (value or "").strip().lower()
    if allow_root and raw in {".", ""}:
        return "."
    if raw == "@":
        raw = zone
    absolute = raw.endswith(".")
    if absolute:
        raw = raw[:-1]
    if not raw:
        raise DnsValidationError("a target hostname is required")
    if not absolute:
        raw = f"{raw}.{zone}"
    if len(raw) > 253 or not _HOSTNAME.fullmatch(raw) or ".." in raw:
        raise DnsValidationError("invalid target hostname")
    if any(label == "*" for label in raw.split(".")[1:]):
        raise DnsValidationError("a wildcard is only valid as the first label")
    return f"{raw}."


def _split_rdata(value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in value.strip():
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\":
            current.append(character)
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
            current.append(character)
            continue
        if character.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(character)
    if escaped or quoted:
        raise DnsValidationError("unbalanced quotes in record value")
    if current:
        tokens.append("".join(current))
    return tokens


def _quoted_segments(value: str) -> list[str]:
    text = value.strip()
    if not text:
        raise DnsValidationError("TXT content is required")
    segments: list[str] = []
    if not text.startswith('"'):
        segments.append(text)
    else:
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                break
            if text[index] != '"':
                raise DnsValidationError("invalid TXT content")
            index += 1
            segment: list[str] = []
            while index < len(text):
                character = text[index]
                if character == "\\":
                    index += 1
                    if index >= len(text):
                        raise DnsValidationError("invalid TXT escape")
                    segment.append(text[index])
                elif character == '"':
                    index += 1
                    break
                else:
                    segment.append(character)
                index += 1
            else:
                raise DnsValidationError("unterminated TXT string")
            segments.append("".join(segment))
    if not segments:
        raise DnsValidationError("TXT content is required")
    for segment in segments:
        if len(segment.encode("utf-8")) > 255:
            raise DnsValidationError("TXT strings must be at most 255 bytes")
        if any(ord(character) < 32 for character in segment):
            raise DnsValidationError("TXT content must not contain control characters")
    return segments


def _quote(segment: str) -> str:
    return '"' + segment.replace("\\", "\\\\").replace('"', '\\"') + '"'


def canonical_record(record_type: str, value: str, zone: str) -> str:
    type_ = record_type.strip().upper()
    text = (value or "").strip()
    if type_ == "A":
        try:
            return str(ipaddress.IPv4Address(text))
        except ValueError as exc:
            raise DnsValidationError("invalid IPv4 address") from exc
    if type_ == "AAAA":
        try:
            return str(ipaddress.IPv6Address(text))
        except ValueError as exc:
            raise DnsValidationError("invalid IPv6 address") from exc
    if type_ in {"CNAME", "NS"}:
        return _hostname(text, zone)
    if type_ == "MX":
        parts = text.split()
        if len(parts) != 2:
            raise DnsValidationError("MX requires priority and exchange")
        try:
            priority = int(parts[0])
        except ValueError as exc:
            raise DnsValidationError("MX priority must be an integer") from exc
        if not 0 <= priority <= 65535:
            raise DnsValidationError("MX priority must be between 0 and 65535")
        return f"{priority} {_hostname(parts[1], zone)}"
    if type_ == "TXT":
        return " ".join(_quote(segment) for segment in _quoted_segments(text))
    if type_ == "CAA":
        parts = _split_rdata(text)
        if len(parts) < 3:
            raise DnsValidationError("CAA requires flags, tag and value")
        try:
            flags = int(parts[0])
        except ValueError as exc:
            raise DnsValidationError("CAA flags must be an integer") from exc
        if not 0 <= flags <= 255:
            raise DnsValidationError("CAA flags must be between 0 and 255")
        tag = parts[1].lower()
        if tag not in _CAA_TAGS:
            raise DnsValidationError("unsupported CAA tag")
        ca_value = " ".join(parts[2:])
        if ca_value.startswith('"') and ca_value.endswith('"') and len(ca_value) >= 2:
            ca_value = ca_value[1:-1]
        if not ca_value or any(character in ca_value for character in '\r\n"'):
            raise DnsValidationError("invalid CAA value")
        return f'{flags} {tag} "{ca_value}"'
    if type_ == "SRV":
        parts = text.split()
        if len(parts) != 4:
            raise DnsValidationError("SRV requires priority, weight, port and target")
        numbers: list[int] = []
        for raw in parts[:3]:
            try:
                number = int(raw)
            except ValueError as exc:
                raise DnsValidationError("SRV numbers must be integers") from exc
            if not 0 <= number <= 65535:
                raise DnsValidationError("SRV numbers must be between 0 and 65535")
            numbers.append(number)
        return " ".join(str(number) for number in numbers) + " " + _hostname(
            parts[3], zone, allow_root=True
        )
    if type_ == "SOA":
        parts = text.split()
        if len(parts) != 7:
            raise DnsValidationError("SOA requires seven fields")
        mname = _hostname(parts[0], zone)
        rname = _hostname(parts[1], zone)
        serials: list[int] = []
        for raw in parts[2:]:
            try:
                number = int(raw)
            except ValueError as exc:
                raise DnsValidationError("SOA numbers must be integers") from exc
            if number < 0:
                raise DnsValidationError("SOA numbers must not be negative")
            serials.append(number)
        return " ".join([mname, rname, *(str(number) for number in serials)])
    raise DnsValidationError(f"unsupported record type: {type_ or '(missing)'}")


def normalize_rrset(
    *,
    zone: str,
    name: str,
    type: str,
    ttl: int | str,
    values: Sequence[str],
    allow_soa: bool = False,
) -> DnsRRset:
    zone_name = normalize_zone_name(zone)
    type_ = (type or "").strip().upper()
    allowed = IMPORT_TYPES if allow_soa else SUPPORTED_TYPES
    if type_ not in allowed:
        raise DnsValidationError(f"unsupported record type: {type_ or '(missing)'}")
    owner = normalize_owner(name, zone_name)
    if type_ == "CNAME" and owner == zone_name:
        raise DnsValidationError("a CNAME is not allowed at the zone apex")
    if not values:
        raise DnsValidationError("an RRset requires at least one record")
    canonical: list[str] = []
    for value in values:
        content = canonical_record(type_, value, zone_name)
        if content in canonical:
            raise DnsValidationError("duplicate record value in RRset")
        canonical.append(content)
    return DnsRRset(
        name=owner,
        type=type_,
        ttl=validate_ttl(ttl),
        records=tuple(DnsRecord(content=item) for item in sorted(canonical)),
    )


def validate_zone_changes(
    zone: DnsZone,
    changes: Iterable[DnsRRset],
    *,
    deletes: Iterable[tuple[str, str]] = (),
    allow_soa: bool = False,
) -> None:
    """Validate the complete state after a set of RRset replacements/deletes."""

    state: dict[tuple[str, str], DnsRRset] = {
        (rrset.name, rrset.type): rrset for rrset in zone.rrsets
    }
    for name, type_ in deletes:
        state.pop((name, type_.upper()), None)
    seen: set[tuple[str, str]] = set()
    for rrset in changes:
        key = (rrset.name, rrset.type)
        if key in seen:
            raise DnsValidationError("duplicate RRset in one request")
        seen.add(key)
        if rrset.type == "SOA" and not allow_soa:
            raise DnsValidationError("SOA records can only be changed through zone import")
        state[key] = rrset
    for (name, type_), _rrset in state.items():
        if type_ == "CNAME" and name == zone.name:
            raise DnsValidationError("a CNAME is not allowed at the zone apex")
        if type_ == "CNAME" and any(
            other_name == name and other_type != "CNAME"
            for other_name, other_type in state
        ):
            raise DnsValidationError("CNAME cannot coexist with other record types")


def rrset_hash(rrset: DnsRRset) -> str:
    payload = "\n".join(
        [rrset.name, rrset.type, str(rrset.ttl), *sorted(rrset.values())]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def zone_version(zone: DnsZone) -> str:
    lines = [f"zone\t{zone.name}\t{zone.kind}"]
    for rrset in sorted(zone.rrsets, key=lambda item: (item.name, item.type)):
        for value in sorted(rrset.values()):
            lines.append(f"{rrset.name}\t{rrset.type}\t{rrset.ttl}\t{value}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def rrset_key(rrset: DnsRRset) -> tuple[str, str]:
    return (rrset.name, rrset.type)


def plan_changes(
    current: DnsZone, desired: Mapping[tuple[str, str], DnsRRset]
) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for key in sorted(desired):
        rrset = desired[key]
        existing = current.rrset(rrset.name, rrset.type)
        if existing is None:
            changes.append({"action": "add", **rrset.to_dict()})
        elif (existing.ttl, existing.values()) != (rrset.ttl, rrset.values()):
            changes.append({"action": "change", **rrset.to_dict()})
    return changes
