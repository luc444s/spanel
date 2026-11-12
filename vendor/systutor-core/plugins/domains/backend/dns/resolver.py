"""Minimal DNS client used by the operator ``nameservers verify`` command.

Only the record types needed by this capability are decoded.  The module has no
third-party dependency so it can also run inside the isolated PowerDNS lab.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct
from dataclasses import dataclass

from plugins.domains.backend.dns.errors import DnsQueryError, DnsValidationError

_QTYPES = {
    "A": 1,
    "NS": 2,
    "CNAME": 5,
    "SOA": 6,
    "MX": 15,
    "TXT": 16,
    "AAAA": 28,
    "SRV": 33,
    "CAA": 257,
}
_TYPE_NAMES = {value: key for key, value in _QTYPES.items()}


@dataclass(frozen=True)
class DnsAnswerRecord:
    name: str
    type: str
    ttl: int
    data: str


@dataclass(frozen=True)
class DnsAnswer:
    name: str
    type: str
    authoritative: bool
    truncated: bool
    rcode: int
    records: tuple[DnsAnswerRecord, ...]


def build_query(name: str, type_: str, *, qid: int | None = None) -> bytes:
    qtype = _QTYPES.get(type_.upper())
    if qtype is None:
        raise DnsValidationError(f"unsupported DNS query type: {type_}")
    identifier = secrets.randbelow(65536) if qid is None else qid & 0xFFFF
    labels = name.strip().rstrip(".").lower().split(".")
    qname = b""
    for label in labels:
        encoded = label.encode("ascii")
        if not encoded or len(encoded) > 63:
            raise DnsValidationError("invalid DNS query name")
        qname += bytes([len(encoded)]) + encoded
    qname += b"\x00"
    header = struct.pack("!HHHHHH", identifier, 0x0000, 1, 0, 0, 0)
    return header + qname + struct.pack("!HH", qtype, 1)


def _read_name(data: bytes, offset: int, *, depth: int = 0) -> tuple[str, int]:
    if depth > 16:
        raise DnsQueryError("DNS response contains a compression loop")
    labels: list[str] = []
    cursor = offset
    while True:
        if cursor >= len(data):
            raise DnsQueryError("DNS response is truncated")
        length = data[cursor]
        if length == 0:
            return ".".join(labels), cursor + 1
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(data):
                raise DnsQueryError("DNS response has an invalid pointer")
            pointer = ((length & 0x3F) << 8) | data[cursor + 1]
            pointed, _ = _read_name(data, pointer, depth=depth + 1)
            if pointed:
                labels.extend(pointed.split("."))
            return ".".join(labels), cursor + 2
        if length & 0xC0:
            raise DnsQueryError("DNS response uses an unsupported label type")
        if cursor + 1 + length > len(data):
            raise DnsQueryError("DNS response is truncated")
        labels.append(data[cursor + 1 : cursor + 1 + length].decode("ascii", errors="replace"))
        cursor += 1 + length


def parse_response(data: bytes, *, expected_id: int | None = None) -> DnsAnswer:
    if len(data) < 12:
        raise DnsQueryError("DNS response is too short")
    identifier, flags, qdcount, ancount, _nscount, _arcount = struct.unpack(
        "!HHHHHH", data[:12]
    )
    if expected_id is not None and identifier != expected_id:
        raise DnsQueryError("DNS response id does not match the query")
    rcode = flags & 0x000F
    authoritative = bool(flags & 0x0400)
    truncated = bool(flags & 0x0200)
    offset = 12
    qname = ""
    qtype = ""
    for _ in range(qdcount):
        qname, offset = _read_name(data, offset)
        if offset + 4 > len(data):
            raise DnsQueryError("DNS response question is truncated")
        type_number, _qclass = struct.unpack("!HH", data[offset : offset + 4])
        qtype = _TYPE_NAMES.get(type_number, str(type_number))
        offset += 4
    records: list[DnsAnswerRecord] = []
    for _ in range(ancount):
        name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise DnsQueryError("DNS response answer is truncated")
        type_number, _class, ttl, rdlength = struct.unpack(
            "!HHIH", data[offset : offset + 10]
        )
        offset += 10
        end = offset + rdlength
        if end > len(data):
            raise DnsQueryError("DNS response answer is truncated")
        value = _decode_rdata(data, type_number, offset, end)
        records.append(
            DnsAnswerRecord(
                name=name.rstrip(".").lower(),
                type=_TYPE_NAMES.get(type_number, str(type_number)),
                ttl=ttl,
                data=value,
            )
        )
        offset = end
    return DnsAnswer(
        name=qname.rstrip(".").lower(),
        type=qtype,
        authoritative=authoritative,
        truncated=truncated,
        rcode=rcode,
        records=tuple(records),
    )


def _decode_rdata(data: bytes, type_number: int, offset: int, end: int) -> str:
    if type_number == 1 and end - offset == 4:
        return str(ipaddress.IPv4Address(data[offset:end]))
    if type_number == 28 and end - offset == 16:
        return str(ipaddress.IPv6Address(data[offset:end]))
    if type_number in {2, 5, 12}:
        value, _ = _read_name(data, offset)
        return value
    if type_number == 6:
        mname, cursor = _read_name(data, offset)
        rname, cursor = _read_name(data, cursor)
        if cursor + 20 > end:
            raise DnsQueryError("SOA response is truncated")
        numbers = struct.unpack("!IIIII", data[cursor : cursor + 20])
        return f"{mname} {rname} " + " ".join(str(number) for number in numbers)
    if type_number == 15:
        preference = struct.unpack("!H", data[offset : offset + 2])[0]
        exchange, _ = _read_name(data, offset + 2)
        return f"{preference} {exchange}"
    if type_number == 16:
        cursor = offset
        segments: list[str] = []
        while cursor < end:
            length = data[cursor]
            cursor += 1
            if cursor + length > end:
                raise DnsQueryError("TXT response is truncated")
            segments.append(data[cursor : cursor + length].decode("utf-8", errors="replace"))
            cursor += length
        return "".join(segments)
    if type_number == 33:
        priority, weight, port = struct.unpack("!HHH", data[offset : offset + 6])
        target, _ = _read_name(data, offset + 6)
        return f"{priority} {weight} {port} {target}"
    if type_number == 257:
        flags = data[offset]
        tag_length = data[offset + 1]
        tag = data[offset + 2 : offset + 2 + tag_length].decode("ascii", errors="replace")
        value = data[offset + 2 + tag_length : end].decode("utf-8", errors="replace")
        return f'{flags} {tag} "{value}"'
    return data[offset:end].hex()


def _exchange(host: str, port: int, payload: bytes, *, tcp: bool, timeout: float) -> bytes:
    try:
        if tcp:
            with socket.create_connection((host, port), timeout=timeout) as connection:
                connection.sendall(struct.pack("!H", len(payload)) + payload)
                size_data = _recv_exact(connection, 2)
                size = struct.unpack("!H", size_data)[0]
                return _recv_exact(connection, size)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(timeout)
            connection.sendto(payload, (host, port))
            response, _address = connection.recvfrom(4096)
            return response
    except (OSError, TimeoutError) as exc:
        raise DnsQueryError(f"DNS {('TCP' if tcp else 'UDP')} query failed") from exc


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise DnsQueryError("DNS TCP connection closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def query(
    name: str,
    type_: str,
    *,
    resolver: str,
    port: int = 53,
    tcp: bool = False,
    timeout: float = 5.0,
) -> DnsAnswer:
    if not 1 <= port <= 65535:
        raise DnsValidationError("resolver port must be between 1 and 65535")
    if not timeout or timeout <= 0:
        raise DnsValidationError("resolver timeout must be positive")
    qid = secrets.randbelow(65536)
    payload = build_query(name, type_, qid=qid)
    response = _exchange(resolver, port, payload, tcp=tcp, timeout=timeout)
    answer = parse_response(response, expected_id=qid)
    if answer.rcode != 0:
        raise DnsQueryError(f"DNS {type_.upper()} query returned rcode {answer.rcode}")
    return answer


def verify_nameservers(
    zone: str,
    *,
    resolver: str,
    port: int = 53,
    timeout: float = 5.0,
) -> dict[str, object]:
    transports: dict[str, object] = {}
    for label, tcp in (("udp", False), ("tcp", True)):
        soa = query(zone, "SOA", resolver=resolver, port=port, tcp=tcp, timeout=timeout)
        ns = query(zone, "NS", resolver=resolver, port=port, tcp=tcp, timeout=timeout)
        if not soa.authoritative or not soa.records:
            raise DnsQueryError(f"{label.upper()} SOA answer is not authoritative")
        if not ns.records:
            raise DnsQueryError(f"{label.upper()} NS answer is empty")
        transports[label] = {
            "soa": [record.data for record in soa.records],
            "ns": [record.data for record in ns.records],
            "authoritative": soa.authoritative,
        }
    return {
        "zone": zone,
        "resolver": resolver,
        "port": port,
        "transports": transports,
        "physical_redundancy": False,
        "warning": "ns1 and ns2 may share one server; this is not physical redundancy",
    }
