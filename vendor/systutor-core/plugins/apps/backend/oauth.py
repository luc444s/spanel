# ruff: noqa: E501
"""Per-user GitHub OAuth state, PKCE and token exchange."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
REVOKE_URL = "https://api.github.com/applications/{client_id}/token"


class OAuthUnavailable(RuntimeError):
    pass


class StateStore(Protocol):
    def put(self, key: str, value: str, ttl: int) -> None: ...

    def pop(self, key: str) -> str | None: ...


class RedisStateStore:
    def __init__(self, client: Any) -> None:
        self.client = client

    def put(self, key: str, value: str, ttl: int) -> None:
        try:
            if not self.client.set(key, value, ex=ttl, nx=True):
                raise OAuthUnavailable("OAuth state collision")
        except Exception as exc:
            raise OAuthUnavailable("OAuth state store unavailable") from exc

    def pop(self, key: str) -> str | None:
        try:
            value = self.client.getdel(key)
        except Exception as exc:
            raise OAuthUnavailable("OAuth state store unavailable") from exc
        if isinstance(value, bytes):
            return value.decode()
        return value


@dataclass(frozen=True)
class OAuthStart:
    authorization_url: str
    state: str


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    scope: tuple[str, ...]
    expires_in: int | None
    refresh_token_expires_in: int | None


class GithubHttp(Protocol):
    def token(self, payload: dict[str, str]) -> dict[str, Any]: ...

    def user(self, access_token: str) -> dict[str, Any]: ...

    def revoke(self, client_id: str, client_secret: str, access_token: str) -> None: ...


class UrllibGithubHttp:
    def _json(self, request: urllib.request.Request) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            data = json.loads(response.read().decode())
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected GitHub response")
        return data

    def token(self, payload: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            TOKEN_URL,
            data=json.dumps(payload).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return self._json(request)

    def user(self, access_token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
            },
        )
        return self._json(request)

    def revoke(self, client_id: str, client_secret: str, access_token: str) -> None:
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        request = urllib.request.Request(
            REVOKE_URL.format(client_id=client_id),
            method="DELETE",
            data=json.dumps({"access_token": access_token}).encode(),
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15):  # noqa: S310
            pass


class OAuthFlow:
    def __init__(
        self,
        store: StateStore,
        http: GithubHttp,
        client_id: str,
        client_secret: str,
        callback_url: str,
    ) -> None:
        self.store = store
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url

    def start(self, user_id: str, redirect: str = "/app/apps") -> OAuthStart:
        if not redirect.startswith("/app/apps") or redirect.startswith("//"):
            redirect = "/app/apps"
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        challenge_text = challenge.rstrip(b"=").decode()
        self.store.put(
            f"apps:oauth:{state}",
            json.dumps({"user_id": user_id, "verifier": verifier, "redirect": redirect}),
            600,
        )
        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.callback_url,
                "scope": "repo offline_access",
                "state": state,
                "code_challenge": challenge_text,
                "code_challenge_method": "S256",
            }
        )
        return OAuthStart(f"{AUTHORIZE_URL}?{query}", state)

    def callback(self, code: str, state: str) -> tuple[str, OAuthTokens, dict[str, Any]]:
        encoded = self.store.pop(f"apps:oauth:{state}")
        if encoded is None:
            raise ValueError("OAuth state is invalid or expired")
        saved = json.loads(encoded)
        response = self.http.token(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.callback_url,
                "code_verifier": saved["verifier"],
            }
        )
        if response.get("error") or not response.get("access_token"):
            raise ValueError("GitHub token exchange failed")
        scopes = tuple(part.strip() for part in str(response.get("scope", "")).split(",") if part.strip())
        if "repo" not in scopes:
            raise ValueError("GitHub repo scope is required")
        tokens = OAuthTokens(
            str(response["access_token"]),
            response.get("refresh_token"),
            scopes,
            _optional_int(response.get("expires_in")),
            _optional_int(response.get("refresh_token_expires_in")),
        )
        user = self.http.user(tokens.access_token)
        if not user.get("id") or not user.get("login"):
            raise ValueError("GitHub identity response is invalid")
        return str(saved["user_id"]), tokens, user

    def refresh(self, refresh_token: str) -> OAuthTokens:
        response = self.http.token({
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        })
        if response.get("error") or not response.get("access_token"):
            raise ValueError("bad_refresh_token")
        scopes = tuple(part.strip() for part in str(response.get("scope", "")).split(",") if part.strip())
        if "repo" not in scopes:
            raise ValueError("insufficient_scope")
        return OAuthTokens(
            str(response["access_token"]), response.get("refresh_token") or refresh_token,
            scopes, _optional_int(response.get("expires_in")),
            _optional_int(response.get("refresh_token_expires_in")),
        )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
