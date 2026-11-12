"""Deterministic zone-file import and export for the supported record set."""

from __future__ import annotations

from collections.abc import Iterable

from plugins.domains.backend.dns.errors import DnsValidationError
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.validation import (
    IMPORT_TYPES,
    TYPE_ORDER,
    canonical_name,
    canonical_record,
    normalize_owner,
    normalize_rrset,
    validate_ttl,
)


def _strip_comment(line: str) -> str:
    result: list[str] = []
    quoted = False
    escaped = False
    for character in line:
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\":
            result.append(character)
            escaped = True
            continue
        if character == '"':
            quoted = not quoted
            result.append(character)
            continue
        if character == ";" and not quoted:
            break
        result.append(character)
    return "".join(result)


def _logical_lines(text: str) -> list[str]:
    pending = ""
    depth = 0
    quoted = False
    escaped = False
    output: list[str] = []
    for physical in text.splitlines():
        if not pending:
            pending = physical
        else:
            pending += " " + physical.strip()
        for character in physical:
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == '"':
                quoted = not quoted
                continue
            if not quoted and character == "(":
                depth += 1
            elif not quoted and character == ")":
                depth -= 1
                if depth < 0:
                    raise DnsValidationError("unbalanced zone-file parentheses")
        if depth == 0 and not quoted:
            output.append(pending)
            pending = ""
    if pending.strip() or depth:
        raise DnsValidationError("unterminated zone-file record")
    return output


def _fields(value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in value.strip():
        if escaped:
            current.append("\\")
            current.append(character)
            escaped = False
            continue
        if character == "\\":
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
        raise DnsValidationError("unbalanced quotes in zone file")
    if current:
        tokens.append("".join(current))
    return tokens


def export_zone(zone: DnsZone, *, default_ttl: int | None = None) -> str:
    ttl = validate_ttl(default_ttl) if default_ttl is not None else _zone_default_ttl(zone)
    lines = [f"$ORIGIN {canonical_name(zone.name)}", f"$TTL {ttl}"]
    rrsets = sorted(
        zone.rrsets,
        key=lambda item: (
            0 if item.name == zone.name else 1,
            item.name,
            TYPE_ORDER.get(item.type, 99),
            item.ttl,
        ),
    )
    for rrset in rrsets:
        for value in sorted(rrset.values()):
            lines.append(
                f"{canonical_name(rrset.name)}\t{rrset.ttl}\tIN\t{rrset.type}\t{value}"
            )
    return "\n".join(lines) + "\n"


def _zone_default_ttl(zone: DnsZone) -> int:
    soa = zone.rrset(zone.name, "SOA")
    if soa is not None and soa.values():
        fields = soa.values()[0].split()
        if len(fields) == 7:
            try:
                return validate_ttl(int(fields[6]))
            except (ValueError, DnsValidationError):
                pass
    values = [rrset.ttl for rrset in zone.rrsets if rrset.ttl > 0]
    return min(values) if values else 300


def parse_zonefile(
    text: str, *, origin: str, default_ttl: int | None = None
) -> list[DnsRRset]:
    zone = normalize_owner(origin, origin)
    current_origin = zone
    current_ttl = validate_ttl(default_ttl) if default_ttl is not None else None
    last_owner: str | None = None
    grouped: dict[tuple[str, str], tuple[int, list[str]]] = {}

    for raw_line in _logical_lines(text):
        leading_space = bool(raw_line[:1].isspace())
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("$"):
            items = _fields(line)
            directive = items[0].upper()
            if directive == "$INCLUDE":
                raise DnsValidationError("$INCLUDE is not supported")
            if directive == "$ORIGIN":
                if len(items) != 2:
                    raise DnsValidationError("$ORIGIN requires one name")
                candidate = items[1].lower().removesuffix(".")
                if candidate != zone and not candidate.endswith(f".{zone}"):
                    raise DnsValidationError("$ORIGIN is outside the imported zone")
                current_origin = candidate
                continue
            if directive == "$TTL":
                if len(items) != 2:
                    raise DnsValidationError("$TTL requires one number")
                current_ttl = validate_ttl(items[1])
                continue
            raise DnsValidationError(f"unsupported zone-file directive: {directive}")

        items = _fields(line)
        if not items:
            continue
        if leading_space:
            if last_owner is None:
                raise DnsValidationError("record omits its owner before any owner was declared")
            owner = last_owner
        else:
            owner = _absolute_owner(items.pop(0), current_origin)
            last_owner = owner
        if not items:
            raise DnsValidationError("zone-file record is incomplete")

        ttl = current_ttl
        if items[0].isdigit():
            ttl = validate_ttl(items.pop(0))
        if items and items[0].upper() in {"IN", "CH", "HS"}:
            if items.pop(0).upper() != "IN":
                raise DnsValidationError("only the IN class is supported")
        if not items:
            raise DnsValidationError("zone-file record lacks a type")
        type_ = items.pop(0).upper()
        if type_ not in IMPORT_TYPES:
            raise DnsValidationError(f"unsupported zone-file record type: {type_}")
        if ttl is None:
            raise DnsValidationError("zone-file record lacks a TTL")
        rdata = " ".join(items)
        if not rdata:
            raise DnsValidationError("zone-file record lacks data")
        try:
            content = canonical_record(type_, rdata, current_origin)
        except DnsValidationError as exc:
            raise DnsValidationError(f"invalid {type_} record: {exc}") from exc
        normalized_owner = normalize_owner(owner, zone)
        key = (normalized_owner, type_)
        if key in grouped:
            previous_ttl, values = grouped[key]
            if previous_ttl != ttl:
                raise DnsValidationError("one RRset cannot contain different TTLs")
            if content not in values:
                values.append(content)
        else:
            grouped[key] = (ttl, [content])

    result: list[DnsRRset] = []
    for (owner, type_), (ttl, values) in sorted(grouped.items()):
        result.append(
            normalize_rrset(
                zone=zone,
                name=owner,
                type=type_,
                ttl=ttl,
                values=values,
                allow_soa=True,
            )
        )
    return result


def _absolute_owner(value: str, origin: str) -> str:
    if value == "@":
        return origin
    raw = value.lower()
    if raw.endswith("."):
        absolute = raw[:-1]
        if absolute != origin and not absolute.endswith(f".{origin}"):
            raise DnsValidationError("zone-file record is outside the imported origin")
        return absolute
    if "." not in raw:
        return f"{raw}.{origin}"
    # A fully qualified owner inside the current origin is kept as written;
    # relative multi-label owners are also resolved against the origin and
    # rejected later if they leave the zone.
    return raw if raw == origin or raw.endswith(f".{origin}") else f"{raw}.{origin}"


def rrset_values(rrsets: Iterable[DnsRRset]) -> dict[tuple[str, str], DnsRRset]:
    return {(rrset.name, rrset.type): rrset for rrset in rrsets}
