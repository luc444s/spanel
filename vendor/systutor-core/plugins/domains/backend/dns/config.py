"""Host-owned PowerDNS configuration.

Only ``SPANEL_PDNS_API_URL``, ``SPANEL_PDNS_API_KEY_FILE``,
``SPANEL_PDNS_SERVER_ID`` and ``SPANEL_PDNS_TIMEOUT_SECONDS`` configure this
capability.  The API key is read from an owner-only regular file; there is no
plaintext environment fallback.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from plugins.domains.backend.dns.errors import DnsConfigError


def _clean(value: str | None) -> str:
    return (value or "").strip()


@dataclass(frozen=True)
class PowerDnsConfig:
    api_url: str
    api_key_file: Path
    server_id: str = "localhost"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PowerDnsConfig:
        env = os.environ if environ is None else environ
        api_url = _clean(env.get("SPANEL_PDNS_API_URL")).rstrip("/")
        key_file = _clean(env.get("SPANEL_PDNS_API_KEY_FILE"))
        server_id = _clean(env.get("SPANEL_PDNS_SERVER_ID")) or "localhost"
        raw_timeout = _clean(env.get("SPANEL_PDNS_TIMEOUT_SECONDS")) or "5"

        if not api_url:
            raise DnsConfigError("SPANEL_PDNS_API_URL is not configured")
        parts = urlsplit(api_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise DnsConfigError("SPANEL_PDNS_API_URL must be an absolute HTTP(S) URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise DnsConfigError("SPANEL_PDNS_API_URL must not contain credentials or queries")
        if parts.path not in {"", "/"}:
            raise DnsConfigError("SPANEL_PDNS_API_URL must not contain an API path")

        if not key_file:
            if _clean(env.get("SPANEL_PDNS_API_KEY")):
                raise DnsConfigError(
                    "plaintext SPANEL_PDNS_API_KEY is not supported; use SPANEL_PDNS_API_KEY_FILE"
                )
            raise DnsConfigError("SPANEL_PDNS_API_KEY_FILE is not configured")

        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise DnsConfigError("SPANEL_PDNS_TIMEOUT_SECONDS must be a number") from exc
        if not 0.1 <= timeout <= 120:
            raise DnsConfigError("SPANEL_PDNS_TIMEOUT_SECONDS must be between 0.1 and 120")
        if not server_id or "/" in server_id or "?" in server_id:
            raise DnsConfigError("SPANEL_PDNS_SERVER_ID is invalid")

        return cls(
            api_url=api_url,
            api_key_file=Path(key_file),
            server_id=server_id,
            timeout_seconds=timeout,
        )

    @property
    def api_root(self) -> str:
        return f"{self.api_url}/api/v1"

    def inspect_key_file(self) -> dict[str, bool]:
        """Return health facts without returning the path or its contents."""
        path = self.api_key_file
        exists = path.is_file()
        regular = False
        private = False
        readable = False
        if exists:
            try:
                mode = path.stat().st_mode
                regular = stat.S_ISREG(mode)
                private = regular and (stat.S_IMODE(mode) & 0o077) == 0
                readable = os.access(path, os.R_OK)
            except OSError:
                regular = private = readable = False
        return {
            "exists": exists,
            "regular": regular,
            "private": private,
            "readable": readable,
        }

    def load_api_key(self) -> str:
        facts = self.inspect_key_file()
        if not facts["exists"] or not facts["regular"]:
            raise DnsConfigError("the PowerDNS API key file is missing or is not a regular file")
        if not facts["private"]:
            raise DnsConfigError("the PowerDNS API key file must not be group/world readable")
        if not facts["readable"]:
            raise DnsConfigError("the PowerDNS API key file is not readable")
        try:
            value = self.api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DnsConfigError("the PowerDNS API key file could not be read") from exc
        if not value or "\x00" in value or "\n" in value or "\r" in value:
            raise DnsConfigError("the PowerDNS API key file is empty or malformed")
        return value

    def describe(self) -> dict[str, object]:
        facts = self.inspect_key_file()
        return {
            "configured": True,
            "server_id": self.server_id,
            "timeout_seconds": self.timeout_seconds,
            "api_key_file_exists": facts["exists"],
            "api_key_file_regular": facts["regular"],
            "api_key_file_private": facts["private"],
            "api_key_file_readable": facts["readable"],
        }
