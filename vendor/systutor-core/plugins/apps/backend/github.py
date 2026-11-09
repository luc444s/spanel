"""Minimal GitHub App API client, JWT signing and token validation.

Only the short manifest handshake and installation verification run in HTTP
callbacks. Installation tokens are created exclusively by the worker. No
response body is ever logged and errors are deliberately opaque.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API = "https://api.github.com"
CONVERSION_URL = f"{API}/app-manifests/{{code}}/conversions"
INSTALLATION_URL = f"{API}/app/installations/{{installation_id}}"
TOKEN_URL = f"{API}/app/installations/{{installation_id}}/access_tokens"
REPOSITORIES_URL = f"{API}/installation/repositories"
MAX_TOKEN_SECONDS = 3600
TOKEN_SKEW_SECONDS = 60


class GithubError(RuntimeError):
    """A GitHub interaction failed. The message is safe to expose."""


class InvalidPrivateKey(ValueError):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def load_private_key(pem: str) -> rsa.RSAPrivateKey:
    if not isinstance(pem, str) or "PRIVATE KEY" not in pem:
        raise InvalidPrivateKey("GitHub App private key is not PEM")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise InvalidPrivateKey("GitHub App private key cannot be parsed") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise InvalidPrivateKey("GitHub App private key must be RSA")
    if key.key_size < 2048:
        raise InvalidPrivateKey("GitHub App private key is too small")
    return key


def app_jwt_for(pem: str, app_id: int, *, now: datetime | None = None) -> str:
    key = load_private_key(pem)
    moment = int((now or datetime.now(UTC)).timestamp())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iat": moment - 60, "exp": moment + 540, "iss": app_id}
    signing = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
              f"{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64url(signature)}"


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: datetime | None

    def __post_init__(self) -> None:
        if not self.token or len(self.token) > 4096:
            raise GithubError("GitHub returned an invalid installation token")


class GithubClient(Protocol):
    def exchange_manifest(self, code: str) -> dict[str, Any]: ...

    def installation(self, jwt: str, installation_id: int) -> dict[str, Any]: ...

    def installation_token(
        self,
        jwt: str,
        installation_id: int,
        *,
        repository_id: int | None = None,
        permissions: dict[str, str] | None = None,
    ) -> InstallationToken: ...

    def installation_repositories(
        self, token: str, page: int, per_page: int = 100
    ) -> dict[str, Any]: ...


class UrllibGithubClient:
    def _json(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                data = json.loads(response.read().decode())
        except Exception as exc:
            raise GithubError("GitHub request failed") from exc
        if not isinstance(data, dict):
            raise GithubError("GitHub returned an unexpected response")
        return data

    def exchange_manifest(self, code: str) -> dict[str, Any]:
        if not code or len(code) > 255:
            raise GithubError("GitHub manifest code is invalid")
        request = urllib.request.Request(
            CONVERSION_URL.format(code=urllib.parse.quote(code, safe="")),
            data=b"",
            method="POST",
            headers={"Accept": "application/vnd.github+json"},
        )
        return self._json(request)

    def installation(self, jwt: str, installation_id: int) -> dict[str, Any]:
        request = urllib.request.Request(
            INSTALLATION_URL.format(installation_id=installation_id),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return self._json(request)

    def installation_token(
        self,
        jwt: str,
        installation_id: int,
        *,
        repository_id: int | None = None,
        permissions: dict[str, str] | None = None,
    ) -> InstallationToken:
        payload: dict[str, Any] = {}
        if repository_id is not None:
            payload["repository_ids"] = [int(repository_id)]
        if permissions is not None:
            payload["permissions"] = dict(permissions)
        request = urllib.request.Request(
            TOKEN_URL.format(installation_id=installation_id),
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return token_from_payload(self._json(request))

    def installation_repositories(
        self, token: str, page: int, per_page: int = 100
    ) -> dict[str, Any]:
        query = urllib.parse.urlencode({"page": page, "per_page": per_page})
        request = urllib.request.Request(
            f"{REPOSITORIES_URL}?{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return self._json(request)


def token_from_payload(data: dict[str, Any]) -> InstallationToken:
    token = data.get("token")
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise GithubError("GitHub returned an invalid installation token")
    expires_at = _parse_timestamp(data.get("expires_at"))
    if expires_at is not None:
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        if remaining > MAX_TOKEN_SECONDS + TOKEN_SKEW_SECONDS:
            raise GithubError("GitHub returned an overlong installation token")
    return InstallationToken(token, expires_at)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GithubError("GitHub returned an invalid token expiry")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GithubError("GitHub returned an invalid token expiry") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def validate_manifest_app(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the manifest conversion fields that SPanel relies on."""

    app_id = data.get("id")
    if isinstance(app_id, bool) or not isinstance(app_id, int) or app_id <= 0:
        raise GithubError("GitHub App id is invalid")
    slug = data.get("slug")
    if not isinstance(slug, str) or not slug or len(slug) > 100 or not _slug(slug):
        raise GithubError("GitHub App slug is invalid")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise GithubError("GitHub App name is invalid")
    pem = data.get("pem")
    load_private_key(pem if isinstance(pem, str) else "")
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return {
        "github_app_id": app_id,
        "app_slug": slug,
        "name": name,
        "owner_login": _text(owner.get("login"), 255),
        "owner_type": _text(owner.get("type"), 32),
        "pem": pem,
    }


def validate_installation(
    data: dict[str, Any], *, app_id: int, installation_id: int
) -> dict[str, Any]:
    if data.get("id") != installation_id or isinstance(data.get("id"), bool):
        raise GithubError("Installation id does not match the callback")
    if data.get("app_id") != app_id:
        raise GithubError("Installation belongs to a different GitHub App")
    if data.get("suspended_at") is not None:
        raise GithubError("Installation is suspended")
    account = data.get("account")
    if not isinstance(account, dict) or not account.get("id") or not account.get("login"):
        raise GithubError("Installation account metadata is invalid")
    selection = data.get("repository_selection")
    if selection not in {"all", "selected"}:
        raise GithubError("Installation repository selection is invalid")
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        raise GithubError("Installation permissions are invalid")
    return {
        "account_id": _text(account.get("id"), 64),
        "account_login": _text(account.get("login"), 255),
        "account_type": _text(account.get("type"), 32),
        "repository_selection": selection,
        "permissions": {
            str(key): str(value) for key, value in permissions.items()
        },
    }


def _slug(value: str) -> bool:
    return all(character.isalnum() or character in {"-", "_"} for character in value)


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def token_still_valid(token: InstallationToken, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(UTC)
    if token.expires_at is None:
        return True
    return token.expires_at > moment + timedelta(seconds=30)
