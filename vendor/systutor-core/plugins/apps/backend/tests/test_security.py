from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from plugins.apps.backend.crypto import EncryptedValue, TokenCipher
from plugins.apps.backend.oauth import OAuthFlow, OAuthUnavailable


class Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttl = 0

    def put(self, key: str, value: str, ttl: int) -> None:
        self.values[key] = value
        self.ttl = ttl

    def pop(self, key: str) -> str | None:
        return self.values.pop(key, None)


class Github:
    def __init__(self) -> None:
        self.payload = None

    def token(self, payload):
        self.payload = payload
        return {
            "access_token": "canary-token",
            "refresh_token": "refresh",
            "scope": "repo,offline_access",
        }

    def user(self, access_token):
        assert access_token == "canary-token"
        return {"id": 42, "login": "octocat"}

    def revoke(self, client_id, client_secret, access_token):
        pass


def test_cipher_is_identity_bound_and_tamper_fails_closed() -> None:
    cipher = TokenCipher(b"x" * 32)
    encrypted = cipher.encrypt("user-1", "oauth-canary")
    assert b"oauth-canary" not in encrypted.ciphertext
    assert cipher.decrypt("user-1", encrypted) == "oauth-canary"
    with pytest.raises(InvalidTag):
        cipher.decrypt("user-2", encrypted)
    tampered = EncryptedValue(encrypted.ciphertext[:-1] + b"x", encrypted.nonce, 1)
    with pytest.raises(InvalidTag):
        cipher.decrypt("user-1", tampered)


def test_cipher_accepts_only_aes_256_key() -> None:
    text = base64.urlsafe_b64encode(b"x" * 32).decode()
    assert TokenCipher.from_text(text).key_version == 1
    with pytest.raises(ValueError):
        TokenCipher(b"short")


def test_oauth_state_is_one_time_pkce_and_identity_verified() -> None:
    store, github = Store(), Github()
    flow = OAuthFlow(store, github, "client", "secret", "https://panel/callback")
    started = flow.start("u1", "https://evil.example")
    assert store.ttl == 600
    assert "scope=repo+offline_access" in started.authorization_url
    assert "code_challenge_method=S256" in started.authorization_url
    user_id, tokens, identity = flow.callback("code", started.state)
    assert (user_id, tokens.access_token, identity["login"]) == ("u1", "canary-token", "octocat")
    assert github.payload["code_verifier"]
    with pytest.raises(ValueError, match="invalid or expired"):
        flow.callback("code", started.state)


def test_redis_failure_is_public_unavailable() -> None:
    class Broken(Store):
        def put(self, key, value, ttl):
            raise OAuthUnavailable("down")

    flow = OAuthFlow(Broken(), Github(), "client", "secret", "https://panel/callback")
    with pytest.raises(OAuthUnavailable):
        flow.start("u1")
