"""Synchronous PowerDNS HTTP client.

This is the only module allowed to call PowerDNS.  It can use the standard
library transport or an injected transport (used by tests).  Exceptions never
carry the API key or the full administrative URL.
"""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import (
    DnsConfigError,
    PowerDnsAuth,
    PowerDnsConflict,
    PowerDnsNotFound,
    PowerDnsTimeout,
    PowerDnsUnavailable,
    PowerDnsValidation,
)
from plugins.domains.backend.dns.models import (
    DnsRRset,
    DnsZone,
    parse_powerdns_zone,
)
from plugins.domains.backend.dns.validation import zone_version

Transport = Callable[
    [str, str, dict[str, str], bytes | None, float],
    tuple[int, dict[str, str], bytes],
]
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - host-owned URL
            return (
                int(response.status),
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except HTTPError as exc:
        return (
            int(exc.code),
            {key.lower(): value for key, value in exc.headers.items()},
            exc.read(),
        )
    except TimeoutError as exc:
        raise PowerDnsTimeout("PowerDNS timed out") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise PowerDnsTimeout("PowerDNS timed out") from exc
        raise PowerDnsUnavailable("PowerDNS is not reachable") from exc
    except OSError as exc:
        raise PowerDnsUnavailable("PowerDNS is not reachable") from exc


class PowerDnsClient:
    def __init__(
        self,
        config: PowerDnsConfig,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or _default_transport

    @property
    def config(self) -> PowerDnsConfig:
        return self._config

    # -- transport ---------------------------------------------------------
    def _redact(self, value: str, key: str) -> str:
        text = key and value.replace(key, "<redacted>") or value
        text = _URL_RE.sub("<redacted-url>", text)
        return " ".join(text.split())[:240]

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        key = self._config.load_api_key()
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "X-API-Key": key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{self._config.api_root}{path}"
        try:
            status, _response_headers, raw = self._transport(
                method, url, headers, body, self._config.timeout_seconds
            )
        except (PowerDnsTimeout, PowerDnsUnavailable):
            raise
        except DnsConfigError:
            raise
        except TimeoutError as exc:
            raise PowerDnsTimeout("PowerDNS timed out") from exc
        except Exception as exc:  # noqa: BLE001 - transport must never leak details
            raise PowerDnsUnavailable("PowerDNS is not reachable") from exc

        if not 200 <= status < 300:
            raise self._error(status, raw, key)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PowerDnsUnavailable("PowerDNS returned an invalid response") from exc

    def _error(self, status: int, raw: bytes, key: str) -> Exception:
        try:
            detail = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - defensive
            detail = ""
        detail = self._redact(detail, key)
        suffix = f": {detail}" if detail else ""
        if status in {401, 403}:
            return PowerDnsAuth(f"PowerDNS rejected the API credentials{suffix}")
        if status == 404:
            return PowerDnsNotFound("PowerDNS resource not found")
        if status == 409:
            return PowerDnsConflict(f"PowerDNS reported a conflict{suffix}")
        if status in {408, 504}:
            return PowerDnsTimeout("PowerDNS timed out")
        if status == 422:
            return PowerDnsValidation(f"PowerDNS rejected the request{suffix}")
        if status >= 500:
            return PowerDnsUnavailable("PowerDNS is unavailable")
        return PowerDnsValidation(f"PowerDNS rejected the request{suffix}")

    def _server_path(self, suffix: str = "") -> str:
        server = quote(self._config.server_id, safe="")
        return f"/servers/{server}{suffix}"

    def _zone_path(self, zone_name: str, suffix: str = "") -> str:
        canonical = f"{zone_name.strip().rstrip('.').lower()}."
        zone = quote(canonical, safe=".")
        return self._server_path(f"/zones/{zone}{suffix}")

    # -- health and zones -------------------------------------------------
    def health(self) -> dict[str, Any]:
        payload = self._request("GET", self._server_path())
        if not isinstance(payload, dict):
            raise PowerDnsUnavailable("PowerDNS returned an invalid health response")
        return payload

    def list_zones(self) -> list[dict[str, Any]]:
        payload = self._request("GET", self._server_path("/zones"))
        if isinstance(payload, dict):
            payload = payload.get("zones", [])
        if not isinstance(payload, list):
            raise PowerDnsUnavailable("PowerDNS returned an invalid zone list")
        return [item for item in payload if isinstance(item, dict)]

    def get_zone(self, zone_name: str) -> DnsZone:
        payload = self._request("GET", self._zone_path(zone_name, "?rrsets=true"))
        if not isinstance(payload, dict):
            raise PowerDnsUnavailable("PowerDNS returned an invalid zone")
        return parse_powerdns_zone(payload)

    def create_zone(
        self,
        zone_name: str,
        *,
        kind: str = "Native",
        nameservers: list[str] | None = None,
    ) -> DnsZone:
        payload: dict[str, Any] = {
            "name": f"{zone_name.strip().rstrip('.').lower()}.",
            "kind": kind,
            # PanelTutor manages SOA content and versions explicitly.  Turning
            # off PowerDNS' API serial rewriting makes imports deterministic.
            "soa_edit_api": "OFF",
        }
        if nameservers:
            payload["nameservers"] = [f"{name.rstrip('.')}." for name in nameservers]
        response = self._request("POST", self._server_path("/zones"), payload)
        if isinstance(response, dict) and response.get("name"):
            return parse_powerdns_zone(response)
        return self.get_zone(zone_name)

    def delete_zone(self, zone_name: str) -> None:
        self._request("DELETE", self._zone_path(zone_name))

    def patch_rrsets(
        self,
        zone_name: str,
        rrsets: list[DnsRRset],
        *,
        delete: list[tuple[str, str]] | None = None,
    ) -> None:
        changes: list[dict[str, Any]] = []
        for rrset in rrsets:
            item = rrset.to_powerdns()
            item["changetype"] = "REPLACE"
            changes.append(item)
        for name, type_ in delete or []:
            changes.append(
                {
                    "name": f"{name}.",
                    "type": type_.upper(),
                    "changetype": "DELETE",
                }
            )
        self._request("PATCH", self._zone_path(zone_name), {"rrsets": changes})

    def replace_rrset(self, zone_name: str, rrset: DnsRRset) -> None:
        self.patch_rrsets(zone_name, [rrset])

    def delete_rrset(self, zone_name: str, name: str, type_: str) -> None:
        self.patch_rrsets(zone_name, [], delete=[(name, type_)])

    def current_version(self, zone_name: str) -> str:
        return zone_version(self.get_zone(zone_name))
