"""Public URL validation and GitHub URL construction.

Callbacks are derived exclusively from ``SPANEL_APPS_PUBLIC_URL``. Request
headers such as ``Host`` or ``X-Forwarded-Host`` never participate in security
decisions.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

API_PREFIX = "/api/v1/plugins/apps"
MANIFEST_CALLBACK = f"{API_PREFIX}/github/manifest/callback"
INSTALL_CALLBACK = f"{API_PREFIX}/github/install/callback"
MANIFEST_NEW = "https://github.com/settings/apps/new"
MANIFEST_NEW_ORG = "https://github.com/organizations/{slug}/settings/apps/new"


class PublicUrlError(ValueError):
    """The configured public URL cannot be used for safe callbacks."""


@dataclass(frozen=True)
class PublicOrigin:
    origin: str

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def manifest_callback(self) -> str:
        return self.url(MANIFEST_CALLBACK)

    def install_callback(self, state: str) -> str:
        return f"{self.url(INSTALL_CALLBACK)}?state={state}"


def _loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_public_url(value: str, *, production: bool) -> PublicOrigin:
    raw = value.strip()
    if not raw:
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL is required")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL must be an absolute origin")
    if parsed.username or parsed.password:
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL must not contain a path, query or fragment")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL port is invalid")
    if production and parsed.scheme != "https":
        raise PublicUrlError("SPANEL_APPS_PUBLIC_URL must use HTTPS in production")
    if not production and parsed.scheme == "http" and not _loopback(parsed.hostname):
        raise PublicUrlError("HTTP is only allowed for loopback outside production")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    origin = f"{parsed.scheme}://{host}"
    if parsed.port is not None:
        origin = f"{origin}:{parsed.port}"
    return PublicOrigin(origin=origin)


def manifest_action_url(owner_type: str, organization: str | None) -> str:
    if owner_type == "personal":
        return MANIFEST_NEW
    if owner_type == "organization":
        if not organization or not _valid_slug(organization):
            raise PublicUrlError("organization slug is invalid")
        return MANIFEST_NEW_ORG.format(slug=organization)
    raise PublicUrlError("owner_type must be personal or organization")


def _valid_slug(value: str) -> bool:
    return bool(value) and len(value) <= 39 and value[0].isalnum() and all(
        character.isalnum() or character == "-" for character in value
    )


def install_url(app_slug: str) -> str:
    if not _valid_slug(app_slug):
        raise PublicUrlError("GitHub app slug is invalid")
    return f"https://github.com/apps/{app_slug}/installations/new"
