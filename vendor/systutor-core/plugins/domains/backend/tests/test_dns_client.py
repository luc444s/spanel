"""Shared PowerDNS client contract tests (A.SPEC 0067)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import (
    DnsConfigError,
    PowerDnsAuth,
    PowerDnsConflict,
    PowerDnsNotFound,
    PowerDnsTimeout,
    PowerDnsUnavailable,
)
from plugins.domains.backend.dns.models import DnsRRset, DnsZone
from plugins.domains.backend.dns.validation import normalize_rrset, zone_version

CANARY = "canary-api-key-value"


def write_key(tmp_path: Path, mode: int = 0o600) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "pdns-key"
    path.write_text(CANARY + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def config(tmp_path: Path, *, mode: int = 0o600) -> PowerDnsConfig:
    return PowerDnsConfig(
        api_url="http://pdns.internal:8081",
        api_key_file=write_key(tmp_path, mode),
        server_id="localhost",
        timeout_seconds=2,
    )


class Transport:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body) if body else None,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return status, {}, json.dumps(payload).encode() if payload is not None else b""


def zone_payload(name: str = "example.com.") -> dict:
    return {
        "name": name,
        "kind": "Native",
        "rrsets": [
            {
                "name": name,
                "type": "SOA",
                "ttl": 300,
                "records": [
                    {
                        "content": f"ns1.{name} hostmaster.{name} 2026010101 3600 600 1209600 300",
                        "disabled": False,
                    }
                ],
            },
            {
                "name": name,
                "type": "A",
                "ttl": 300,
                "records": [{"content": "192.0.2.10", "disabled": False}],
            },
        ],
    }


def test_config_requires_private_regular_key_file(tmp_path: Path) -> None:
    private = write_key(tmp_path)
    parsed = PowerDnsConfig.from_env(
        {
            "SPANEL_PDNS_API_URL": "http://pdns.internal:8081/",
            "SPANEL_PDNS_API_KEY_FILE": str(private),
            "SPANEL_PDNS_SERVER_ID": "localhost",
            "SPANEL_PDNS_TIMEOUT_SECONDS": "3.5",
        }
    )
    assert parsed.api_url == "http://pdns.internal:8081"
    assert parsed.timeout_seconds == 3.5
    assert parsed.load_api_key() == CANARY
    assert parsed.describe()["api_key_file_private"] is True

    public = write_key(tmp_path / "public", 0o644)
    with pytest.raises(DnsConfigError, match="group/world"):
        PowerDnsConfig(
            api_url="http://pdns.internal:8081",
            api_key_file=public,
        ).load_api_key()


def test_plaintext_key_is_not_a_fallback(tmp_path: Path) -> None:
    with pytest.raises(DnsConfigError, match="plaintext"):
        PowerDnsConfig.from_env(
            {
                "SPANEL_PDNS_API_URL": "http://pdns.internal:8081",
                "SPANEL_PDNS_API_KEY": CANARY,
            }
        )


def test_client_authenticates_and_canonicalizes(tmp_path: Path) -> None:
    transport = Transport(
        [
            (200, {"id": "localhost"}),
            (200, [{"name": "example.com.", "kind": "Native"}]),
            (200, zone_payload()),
        ]
    )
    client = PowerDnsClient(config(tmp_path), transport=transport)
    assert client.health()["id"] == "localhost"
    assert client.list_zones()[0]["name"] == "example.com."
    zone = client.get_zone("Example.COM.")
    assert zone.name == "example.com"
    assert zone.rrset("example.com", "SOA") is not None
    assert all(call["headers"]["X-API-Key"] == CANARY for call in transport.calls)
    assert transport.calls[2]["url"].endswith("/zones/example.com.?rrsets=true")
    assert "api/v1/servers/localhost" in transport.calls[0]["url"]


def test_client_create_patch_and_delete(tmp_path: Path) -> None:
    transport = Transport(
        [
            (201, {"name": "example.com.", "kind": "Native"}),
            (204, None),
            (204, None),
        ]
    )
    client = PowerDnsClient(config(tmp_path), transport=transport)
    created = client.create_zone(
        "example.com", nameservers=["ns1.example.com", "ns2.example.com"]
    )
    assert created.name == "example.com"
    rrset = normalize_rrset(
        zone="example.com",
        name="www",
        type="A",
        ttl=300,
        values=["192.0.2.1"],
    )
    client.patch_rrsets("example.com", [rrset])
    client.delete_zone("example.com")
    assert transport.calls[1]["body"]["rrsets"][0]["changetype"] == "REPLACE"
    assert transport.calls[1]["body"]["rrsets"][0]["name"] == "www.example.com."
    assert transport.calls[0]["body"]["soa_edit_api"] == "OFF"
    assert transport.calls[2]["method"] == "DELETE"


def test_errors_never_leak_key_or_full_url(tmp_path: Path) -> None:
    transport = Transport(
        [
            (
                401,
                {
                    "error": f"bad {CANARY} at http://pdns.internal:8081/api/v1/servers",
                },
            )
        ]
    )
    client = PowerDnsClient(config(tmp_path), transport=transport)
    with pytest.raises(PowerDnsAuth) as caught:
        client.health()
    message = str(caught.value)
    assert CANARY not in message
    assert "http://" not in message
    assert "redacted-url" in message


def test_status_mapping(tmp_path: Path) -> None:
    mappings = [
        (404, PowerDnsNotFound),
        (409, PowerDnsConflict),
        (503, PowerDnsUnavailable),
    ]
    for status, error_type in mappings:
        transport = Transport([(status, {"error": "fixture"})])
        client = PowerDnsClient(config(tmp_path), transport=transport)
        with pytest.raises(error_type):
            client.health()


def test_timeout_is_bounded(tmp_path: Path) -> None:
    transport = Transport([TimeoutError("timed out")])
    client = PowerDnsClient(config(tmp_path), transport=transport)
    with pytest.raises(PowerDnsTimeout):
        client.health()
    assert transport.calls[0]["timeout"] == 2


def test_version_is_canonical_and_order_independent(tmp_path: Path) -> None:
    first = PowerDnsClient(
        config(tmp_path), transport=Transport([(200, zone_payload())])
    ).current_version("example.com")
    second = PowerDnsClient(
        config(tmp_path), transport=Transport([(200, zone_payload())])
    ).current_version("example.com")
    assert first == second
    reordered = zone_payload()
    reordered["rrsets"] = list(reversed(reordered["rrsets"]))
    third = PowerDnsClient(
        config(tmp_path), transport=Transport([(200, reordered)])
    ).current_version("example.com")
    assert third == first


def test_rrset_models_expose_canonical_powerdns_shape() -> None:
    rrset = DnsRRset(name="www.example.com", type="A", ttl=300)
    assert rrset.to_powerdns()["name"] == "www.example.com."
    assert zone_version(DnsZone(name="example.com"))
