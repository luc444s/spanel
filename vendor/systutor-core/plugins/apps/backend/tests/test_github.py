from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from plugins.apps.backend.github import (
    GithubError,
    InvalidPrivateKey,
    app_jwt_for,
    token_from_payload,
    validate_installation,
    validate_manifest_app,
)


def pem() -> str:
    return rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_jwt_is_short_lived_and_clock_skew_tolerant() -> None:
    now = datetime.now(UTC)
    token = app_jwt_for(pem(), 4242, now=now)
    header, claims, signature = token.split(".")
    assert json.loads(base64.urlsafe_b64decode(header + "=="))["alg"] == "RS256"
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=="))
    assert decoded["iss"] == 4242
    assert decoded["iat"] <= int(now.timestamp())
    assert decoded["exp"] - int(now.timestamp()) <= 600
    assert signature


@pytest.mark.parametrize("value", ["", "not-a-key", "-----BEGIN PRIVATE KEY-----\nxxx\n"])
def test_invalid_private_keys_fail_closed(value: str) -> None:
    with pytest.raises(InvalidPrivateKey):
        app_jwt_for(value, 1)


def test_opaque_installation_tokens_are_accepted_with_variable_length() -> None:
    token = "ghs_" + "a" * 80
    result = token_from_payload({
        "token": token,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=59)).isoformat(),
    })
    assert result.token == token
    assert result.expires_at is not None


@pytest.mark.parametrize("expires", [62, 120])
def test_overlong_installation_tokens_are_rejected(expires: int) -> None:
    with pytest.raises(GithubError):
        token_from_payload({
            "token": "opaque",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=expires)).isoformat(),
        })


def test_manifest_conversion_validates_types_and_key() -> None:
    app = validate_manifest_app({
        "id": 4242, "slug": "spanel-apps-test", "name": "Test", "pem": pem(),
        "client_secret": "must-not-be-returned", "webhook_secret": "must-not-be-returned",
        "owner": {"login": "acme", "type": "Organization"},
    })
    assert app["github_app_id"] == 4242
    assert "client_secret" not in app
    assert "webhook_secret" not in app
    for broken in [
        {"id": True, "slug": "x", "name": "x", "pem": pem()},
        {"id": 0, "slug": "x", "name": "x", "pem": pem()},
        {"id": 1, "slug": "../x", "name": "x", "pem": pem()},
        {"id": 1, "slug": "x", "name": "", "pem": pem()},
        {"id": 1, "slug": "x", "name": "x", "pem": "invalid"},
    ]:
        with pytest.raises((GithubError, InvalidPrivateKey)):
            validate_manifest_app(broken)


def test_installation_validation_rejects_wrong_app_and_suspension() -> None:
    payload = {
        "id": 77, "app_id": 4242,
        "account": {"id": 5, "login": "acme", "type": "Organization"},
        "repository_selection": "selected", "permissions": {"contents": "read"},
        "suspended_at": None,
    }
    verified = validate_installation(payload, app_id=4242, installation_id=77)
    assert verified["account_login"] == "acme"
    for mutate in [
        {"id": 88},
        {"app_id": 9999},
        {"suspended_at": "2026-01-01T00:00:00Z"},
        {"repository_selection": "everything"},
        {"permissions": []},
        {"account": {"id": 0, "login": ""}},
    ]:
        with pytest.raises(GithubError):
            validate_installation(
                {**payload, **mutate}, app_id=4242, installation_id=77
            )
