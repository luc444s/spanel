"""Cloudflare client contract: auth, registration, refresh, pagination, errors."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

import plugins.domains.backend.cloudflare.client as cloudflare_client_module
from plugins.domains.backend.cloudflare.client import (
    CloudflareClient,
    CloudflareConflict,
    CloudflareCredentials,
    CloudflareNotFound,
    CloudflarePermission,
    CloudflareRateLimit,
    CloudflareTimeout,
    CloudflareUnavailable,
    CloudflareUpstream,
    CloudflareValidation,
)
from plugins.domains.backend.cloudflare.config import CloudflareConfig, CloudflareConfigError

CANARY = "cf-secret-canary-value"


def write_key(tmp_path: Path, size: int = 32, mode: int = 0o600) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "cloudflare_key"
    path.write_bytes(b"k" * size)
    path.chmod(mode)
    return path


def write_base64_key(tmp_path: Path, raw: bytes = b"k" * 32) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "cloudflare_key_b64"
    encoded = base64.urlsafe_b64encode(raw).decode()
    path.write_text(encoded + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def config(tmp_path: Path, **kwargs) -> CloudflareConfig:
    return CloudflareConfig(
        encryption_key_file=write_key(tmp_path),
        public_url="https://spanel.lab",
        timeout_seconds=3,
        **kwargs,
    )


def envelope(result, info=None):
    payload = {"success": True, "result": result}
    if info is not None:
        payload["result_info"] = info
    return payload


class Transport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body.decode() if body else "",
                "timeout": timeout,
            }
        )
        response = self.handler(method, url, body)
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return status, {}, json.dumps(payload).encode() if payload is not None else b""


def test_config_requires_private_exact_32_byte_key(tmp_path: Path) -> None:
    private = write_key(tmp_path)
    parsed = CloudflareConfig.from_env(
        {
            "SPANEL_CLOUDFLARE_ENCRYPTION_KEY_FILE": str(private),
            "SPANEL_CLOUDFLARE_PUBLIC_URL": "https://spanel.lab/",
            "SPANEL_CLOUDFLARE_TIMEOUT_SECONDS": "4.5",
        }
    )
    assert parsed.public_url == "https://spanel.lab"
    assert parsed.redirect_uri.endswith("/dns/cloudflare/oauth/callback")
    assert parsed.load_key() == b"k" * 32
    assert parsed.inspect_key_file()["exact_32_bytes"] is True

    encoded = write_base64_key(tmp_path)
    parsed_encoded = CloudflareConfig(encoded, "https://spanel.lab")
    assert parsed_encoded.load_key() == b"k" * 32
    assert parsed_encoded.inspect_key_file()["exact_32_bytes"] is True

    short = write_key(tmp_path / "short", size=31)
    with pytest.raises(CloudflareConfigError, match="exactly 32"):
        CloudflareConfig(short, "https://spanel.lab").load_key()

    public = write_key(tmp_path / "public", mode=0o644)
    with pytest.raises(CloudflareConfigError, match="group/world"):
        CloudflareConfig(public, "https://spanel.lab").load_key()


def test_config_derives_cloudflare_defaults_from_spanel_domain() -> None:
    parsed = CloudflareConfig.from_env({"SPANEL_DOMAIN": "panel.example.com"})
    assert parsed.encryption_key_file == Path("/run/secrets/apps_key")
    assert parsed.public_url == "https://panel.example.com"
    assert parsed.redirect_uri == (
        "https://panel.example.com/api/v1/plugins/domains/dns/cloudflare/oauth/callback"
    )
    assert parsed.timeout_seconds == 10


def test_config_uses_existing_apps_public_url() -> None:
    parsed = CloudflareConfig.from_env(
        {"SPANEL_APPS_PUBLIC_URL": "https://spanel.systutor.com"}
    )
    assert parsed.public_url == "https://spanel.systutor.com"
    assert parsed.redirect_uri == (
        "https://spanel.systutor.com"
        "/api/v1/plugins/domains/dns/cloudflare/oauth/callback"
    )


def test_default_transport_identifies_paneltutor_instead_of_python_urllib(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(cloudflare_client_module, "urlopen", fake_urlopen)
    status, _headers, _body = cloudflare_client_module._default_transport(
        "GET", "https://api.cloudflare.com/client/v4/zones", {}, None, 3
    )
    assert status == 200
    assert captured == {
        "user_agent": "PanelTutor/0.1 (+https://spanel.lab)",
        "timeout": 3,
    }


def test_no_plaintext_env_secret_and_redirect_origin_is_exact(tmp_path: Path) -> None:
    defaults = CloudflareConfig.from_env(
        {
            "SPANEL_CLOUDFLARE_PUBLIC_URL": "https://spanel.lab",
            "SPANEL_CLOUDFLARE_CLIENT_SECRET": CANARY,
        }
    )
    assert defaults.encryption_key_file == Path("/run/secrets/apps_key")
    assert CANARY not in str(defaults.describe())
    parsed = config(tmp_path)
    with pytest.raises(CloudflareConfigError, match="HTTPS"):
        parsed.validate_redirect_uri("http://spanel.lab/callback")
    with pytest.raises(CloudflareConfigError, match="origin"):
        parsed.validate_redirect_uri("https://evil.example/callback")
    with pytest.raises(CloudflareConfigError, match="credentials"):
        parsed.validate_redirect_uri("https://user:pass@spanel.lab/callback")
    assert parsed.validate_redirect_uri(parsed.redirect_uri) == parsed.redirect_uri


def test_registration_resolves_scopes_and_keeps_secret_out_of_calls(tmp_path: Path) -> None:
    scope_result = [
        {"id": "scope-zone", "name": "Zone Read"},
        {"id": "scope-dns-read", "name": "DNS Read"},
        {"id": "scope-dns-write", "name": "DNS Write"},
    ]

    def handler(method, url, body):
        if url.endswith("/oauth/scopes?per_page=200"):
            return 200, envelope(scope_result)
        if "/oauth_clients" in url:
            return 200, envelope({"client_id": "client-1", "client_secret": CANARY})
        raise AssertionError(url)

    transport = Transport(handler)
    client = CloudflareClient(config(tmp_path), transport=transport)
    scopes = client.list_scopes("bootstrap-token")
    assert [scope["name"] for scope in scopes] == [
        "Zone Read",
        "DNS Read",
        "DNS Write",
    ]
    client_id, secret = client.register_client(
        account_id="acct1",
        bootstrap_token="bootstrap-token",
        redirect_uri="https://spanel.lab/api/v1/plugins/domains/dns/cloudflare/oauth/callback",
        scope_ids=["scope-zone", "scope-dns-read", "scope-dns-write"],
    )
    assert (client_id, secret) == ("client-1", CANARY)
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer bootstrap-token"
    registered = json.loads(transport.calls[1]["body"])
    assert registered["client_type"] == "private"
    assert registered["token_endpoint_auth_method"] == "client_secret_basic"
    assert registered["grant_types"] == ["authorization_code", "refresh_token"]
    assert registered["redirect_uris"] == [registered["redirect_uris"][0]]
    assert registered["scopes"] == ["scope-zone", "scope-dns-read", "scope-dns-write"]
    assert transport.calls[1]["url"].endswith("/accounts/acct1/oauth_clients")
    assert transport.calls[1]["timeout"] == 3


def test_existing_client_is_patched_to_enable_refresh(tmp_path: Path) -> None:
    transport = Transport(
        lambda method, url, body: (200, envelope({"client_id": "client-1"}))
    )
    client = CloudflareClient(config(tmp_path), transport=transport)
    client.update_client(
        account_id="acct1",
        client_id="client-1",
        bootstrap_token="bootstrap-token",
        redirect_uri="https://spanel.lab/cb",
        scope_ids=["zone.read", "dns.read", "dns.write"],
    )
    call = transport.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/accounts/acct1/oauth_clients/client-1")
    body = json.loads(call["body"])
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["scopes"] == ["zone.read", "dns.read", "dns.write"]
    assert call["headers"]["Authorization"] == "Bearer bootstrap-token"


def test_code_exchange_refresh_and_revoke_use_basic_auth(tmp_path: Path) -> None:
    def handler(method, url, body):
        if url.endswith("/oauth2/token"):
            return 200, {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": "zone.read dns.read",
                "token_type": "bearer",
            }
        if url.endswith("/oauth2/revoke"):
            return 200, {}
        raise AssertionError(url)

    transport = Transport(handler)
    client = CloudflareClient(config(tmp_path), transport=transport)
    tokens = client.exchange_code(
        client_id="client-1",
        client_secret=CANARY,
        code="code-1",
        redirect_uri="https://spanel.lab/cb",
        code_verifier="verifier-1",
    )
    assert tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    assert tokens.expires_in == 3600
    refreshed = client.refresh(
        client_id="client-1", client_secret=CANARY, refresh_token="refresh-1"
    )
    assert refreshed.refresh_token == "refresh-1"
    client.revoke(client_id="client-1", client_secret=CANARY, token="refresh-1")
    expected = "Basic " + base64.b64encode(f"client-1:{CANARY}".encode()).decode()
    assert all(call["headers"]["Authorization"] == expected for call in transport.calls)
    exchanged = parse_qs(transport.calls[0]["body"])
    assert exchanged["grant_type"] == ["authorization_code"]
    assert exchanged["code_verifier"] == ["verifier-1"]
    assert parse_qs(transport.calls[2]["body"])["token"] == ["refresh-1"]


def test_authorization_url_is_fixed_and_contains_pkce(tmp_path: Path) -> None:
    client = CloudflareClient(config(tmp_path), transport=Transport(lambda *_: (200, {})))
    url = client.authorization_url(
        client_id="client-1",
        redirect_uri="https://spanel.lab/cb",
        state="state-1",
        code_challenge="challenge-1",
        scopes=["Zone Read", "DNS Read", "DNS Write"],
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == "https://dash.cloudflare.com"
    assert parts.path == "/oauth2/auth"
    query = parse_qs(parts.query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["Zone Read DNS Read DNS Write"]
    assert query["state"] == ["state-1"]


def test_zone_and_record_pagination(tmp_path: Path) -> None:
    zone_page_one = [
        {
            "id": "z1",
            "name": "example.com",
            "status": "active",
            "account": {"id": "acct1", "name": "Lab"},
            "name_servers": ["ada.ns.cloudflare.com"],
            "created_on": "2026-01-01T00:00:00Z",
            "modified_on": None,
        }
    ]
    zone_page_two = [{"id": "z2", "name": "other.test", "status": "pending"}]
    record_page_one = [
        {
            "id": "r1",
            "name": "example.com",
            "type": "TXT",
            "ttl": 120,
            "proxied": False,
            "content": "TXT-CONTENT-CANARY",
            "created_on": "2026-01-01T00:00:00Z",
            "modified_on": "2026-01-02T00:00:00Z",
        }
    ]
    record_page_two = [
        {"id": "r2", "name": "www.example.com", "type": "A", "ttl": 300, "proxied": True}
    ]

    def handler(method, url, body):
        if "/zones?page=1" in url:
            return 200, envelope(zone_page_one, {"page": 1, "total_pages": 2})
        if "/zones?page=2" in url:
            return 200, envelope(zone_page_two, {"page": 2, "total_pages": 2})
        if "/zones/z1" in url and "/dns_records" not in url:
            return 200, envelope(zone_page_one[0])
        if "/dns_records?page=1" in url:
            return 200, envelope(record_page_one, {"page": 1, "total_pages": 2, "total_count": 2})
        if "/dns_records?page=2" in url:
            return 200, envelope(record_page_two, {"page": 2, "total_pages": 2, "total_count": 2})
        if "/dns_records?page=1&per_page=1" in url:
            return 200, envelope([], {"total_count": 2})
        raise AssertionError(url)

    transport = Transport(handler)
    client = CloudflareClient(config(tmp_path), transport=transport)
    credentials = CloudflareCredentials("access-1")
    zones = client.list_zones(credentials)
    assert [zone.id for zone in zones] == ["z1", "z2"]
    assert zones[0].account_name == "Lab"
    assert zones[0].name_servers == ("ada.ns.cloudflare.com",)
    assert client.get_zone(credentials, "z1").name == "example.com"
    records = client.list_records(credentials, "z1")
    assert [record.type for record in records] == ["TXT", "A"]
    assert records[0].content == "TXT-CONTENT-CANARY"
    assert client.count_records(credentials, "z1") == 2


def test_record_create_update_delete_contract(tmp_path: Path) -> None:
    created = {
        "id": "r1",
        "name": "www.example.com",
        "type": "A",
        "content": "192.0.2.10",
        "ttl": 300,
        "proxied": True,
    }

    def handler(method, url, body):
        if method == "POST":
            return 200, envelope(created)
        if method == "PUT":
            return 200, envelope({**created, "content": "192.0.2.11"})
        if method == "DELETE":
            return 200, envelope({"id": "r1"})
        raise AssertionError((method, url))

    transport = Transport(handler)
    client = CloudflareClient(config(tmp_path), transport=transport)
    credentials = CloudflareCredentials("access-1")
    payload: dict[str, object] = {
        "name": "www.example.com",
        "type": "A",
        "content": "192.0.2.10",
        "ttl": 300,
        "proxied": True,
    }
    assert client.create_record(credentials, "z1", payload).id == "r1"
    assert client.update_record(credentials, "z1", "r1", payload).content == "192.0.2.11"
    client.delete_record(credentials, "z1", "r1")

    assert [call["method"] for call in transport.calls] == ["POST", "PUT", "DELETE"]
    assert transport.calls[0]["url"].endswith("/zones/z1/dns_records")
    assert transport.calls[1]["url"].endswith("/zones/z1/dns_records/r1")
    assert json.loads(transport.calls[0]["body"]) == payload
    assert transport.calls[2]["body"] == ""


def test_refresh_once_on_401_then_permission(tmp_path: Path) -> None:
    calls = {"count": 0}

    def handler(method, url, body):
        calls["count"] += 1
        if calls["count"] == 1:
            return 401, {"success": False, "errors": [{"message": "expired"}]}
        return 200, envelope([])

    transport = Transport(handler)
    client = CloudflareClient(config(tmp_path), transport=transport)
    refreshed = CloudflareCredentials("access-2")

    def refresh():
        return refreshed

    assert client.list_zones(CloudflareCredentials("access-1"), refresh) == []
    assert calls["count"] == 2
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer access-2"

    failing = Transport(lambda *_: (401, {"success": False}))
    client = CloudflareClient(config(tmp_path), transport=failing)
    with pytest.raises(CloudflarePermission):
        client.list_zones(CloudflareCredentials("access-1"), refresh)
    assert len(failing.calls) == 2


def test_error_mapping_is_opaque(tmp_path: Path) -> None:
    mappings = [
        (400, CloudflareValidation),
        (403, CloudflarePermission),
        (404, CloudflareNotFound),
        (409, CloudflareConflict),
        (429, CloudflareRateLimit),
        (500, CloudflareUnavailable),
        (502, CloudflareUpstream),
        (504, CloudflareTimeout),
    ]
    for status, error_type in mappings:
        transport = Transport(
            lambda *_, status=status: (
                status,
                {"success": False, "errors": [{"message": CANARY}], "body": CANARY},
            )
        )
        client = CloudflareClient(config(tmp_path), transport=transport)
        with pytest.raises(error_type) as caught:
            client.list_scopes("bootstrap-token")
        assert CANARY not in str(caught.value)
        assert "https://" not in str(caught.value)


def test_timeout_is_bounded_and_invalid_payload_is_upstream(tmp_path: Path) -> None:
    transport = Transport(lambda *_: TimeoutError("boom"))
    client = CloudflareClient(config(tmp_path), transport=transport)
    with pytest.raises(CloudflareTimeout):
        client.list_scopes("bootstrap-token")
    assert transport.calls[0]["timeout"] == 3

    invalid = Transport(lambda *_: (200, {"success": True, "result": "not-a-list"}))
    client = CloudflareClient(config(tmp_path), transport=invalid)
    with pytest.raises(CloudflareUpstream):
        client.list_scopes("bootstrap-token")
