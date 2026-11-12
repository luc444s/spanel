"""Cloudflare host configuration and fixed trusted endpoints.

Only the encryption-key file, the public HTTPS origin and a finite timeout are
configurable.  The key is an owner-only regular file holding exactly 32 raw
bytes or a base64/base64url encoding of 32 bytes (the same format the Apps
plugin already mounts); there is no plaintext environment fallback for any
secret.
"""

from __future__ import annotations

import base64
import binascii
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

API_BASE = "https://api.cloudflare.com/client/v4"
AUTHORIZATION_URL = "https://dash.cloudflare.com/oauth2/auth"
TOKEN_URL = "https://dash.cloudflare.com/oauth2/token"
REVOKE_URL = "https://dash.cloudflare.com/oauth2/revoke"

CALLBACK_PATH = "/api/v1/plugins/domains/dns/cloudflare/oauth/callback"
REQUIRED_SCOPE_NAMES = ("Zone Read", "DNS Read", "DNS Write")


class CloudflareConfigError(ValueError):
    """Host configuration is missing or unsafe. The message has no secrets."""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _origin(parts) -> str:
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    origin = f"{parts.scheme}://{host}"
    if parts.port is not None:
        origin = f"{origin}:{parts.port}"
    return origin


def _decode_key(raw: bytes) -> bytes | None:
    """Return the 32-byte key, accepting raw bytes or a base64url/base64 text key.

    The Apps plugin stores its key as base64url text (44 characters plus a
    trailing newline) and the host mounts the same file here.  Raw 32-byte files
    keep working unchanged.  Anything that does not resolve to exactly 32 bytes
    is rejected.
    """
    if len(raw) == 32:
        return raw
    text = raw.strip()
    if not text or b"\n" in text or b"\r" in text or len(text) > 64:
        return None
    try:
        decoded = base64.urlsafe_b64decode(text + b"=" * (-len(text) % 4))
    except (ValueError, binascii.Error):
        return None
    return decoded if len(decoded) == 32 else None


@dataclass(frozen=True)
class CloudflareConfig:
    encryption_key_file: Path
    public_url: str
    timeout_seconds: float = 10.0
    key_version: int = 1

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CloudflareConfig:
        env = os.environ if environ is None else environ
        key_file = (
            _clean(env.get("SPANEL_CLOUDFLARE_ENCRYPTION_KEY_FILE"))
            or "/run/secrets/apps_key"
        )
        public_url = _clean(env.get("SPANEL_CLOUDFLARE_PUBLIC_URL")).rstrip("/")
        if not public_url:
            domain = _clean(env.get("SPANEL_DOMAIN"))
            if domain:
                public_url = f"https://{domain}"
        raw_timeout = _clean(env.get("SPANEL_CLOUDFLARE_TIMEOUT_SECONDS")) or "10"
        raw_version = _clean(env.get("SPANEL_CLOUDFLARE_KEY_VERSION")) or "1"

        if not public_url:
            raise CloudflareConfigError(
                "SPANEL_DOMAIN or SPANEL_CLOUDFLARE_PUBLIC_URL is required"
            )
        parts = urlsplit(public_url)
        if parts.scheme != "https" or not parts.hostname:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_PUBLIC_URL must be an absolute HTTPS origin"
            )
        if parts.username or parts.password:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_PUBLIC_URL must not contain credentials"
            )
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_PUBLIC_URL must not contain a path, query or fragment"
            )
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_TIMEOUT_SECONDS must be a number"
            ) from exc
        if not 0.1 <= timeout <= 120:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_TIMEOUT_SECONDS must be between 0.1 and 120"
            )
        try:
            key_version = int(raw_version)
        except ValueError as exc:
            raise CloudflareConfigError(
                "SPANEL_CLOUDFLARE_KEY_VERSION must be an integer"
            ) from exc
        if key_version < 1:
            raise CloudflareConfigError("SPANEL_CLOUDFLARE_KEY_VERSION must be positive")
        return cls(
            encryption_key_file=Path(key_file),
            public_url=_origin(parts),
            timeout_seconds=timeout,
            key_version=key_version,
        )

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}{CALLBACK_PATH}"

    def validate_redirect_uri(self, value: str) -> str:
        raw = (value or "").strip()
        parts = urlsplit(raw)
        if parts.scheme != "https" or not parts.hostname:
            raise CloudflareConfigError("the redirect URI must be an absolute HTTPS URL")
        if parts.username or parts.password:
            raise CloudflareConfigError("the redirect URI must not contain credentials")
        if parts.query or parts.fragment:
            raise CloudflareConfigError("the redirect URI must not contain a query or fragment")
        if _origin(parts) != self.public_url:
            raise CloudflareConfigError(
                "the redirect URI origin must exactly equal SPANEL_CLOUDFLARE_PUBLIC_URL"
            )
        return raw

    def inspect_key_file(self) -> dict[str, bool]:
        path = self.encryption_key_file
        exists = path.is_file()
        regular = False
        private = False
        readable = False
        exact = False
        if exists:
            try:
                mode = path.stat().st_mode
                regular = stat.S_ISREG(mode)
                private = regular and (stat.S_IMODE(mode) & 0o077) == 0
                readable = os.access(path, os.R_OK)
                exact = regular and _decode_key(path.read_bytes()) is not None
            except OSError:
                regular = private = readable = exact = False
        return {
            "exists": exists,
            "regular": regular,
            "private": private,
            "readable": readable,
            "exact_32_bytes": exact,
        }

    def load_key(self) -> bytes:
        facts = self.inspect_key_file()
        if not facts["exists"] or not facts["regular"]:
            raise CloudflareConfigError(
                "the Cloudflare encryption key file is missing or is not a regular file"
            )
        if not facts["private"]:
            raise CloudflareConfigError(
                "the Cloudflare encryption key file must not be group/world accessible"
            )
        if not facts["readable"]:
            raise CloudflareConfigError("the Cloudflare encryption key file is not readable")
        try:
            raw = self.encryption_key_file.read_bytes()
        except OSError as exc:
            raise CloudflareConfigError(
                "the Cloudflare encryption key file could not be read"
            ) from exc
        key = _decode_key(raw)
        if key is None:
            raise CloudflareConfigError(
                "the Cloudflare encryption key file must contain exactly 32 bytes "
                "or a base64url-encoded 32-byte key"
            )
        return key

    def describe(self) -> dict[str, object]:
        facts = self.inspect_key_file()
        return {
            "configured": True,
            "public_url": self.public_url,
            "redirect_uri": self.redirect_uri,
            "timeout_seconds": self.timeout_seconds,
            "key_version": self.key_version,
            **{f"key_{name}": value for name, value in facts.items()},
        }
