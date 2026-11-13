"""Injected synchronous Cloudflare HTTP client.

This is the only module allowed to talk to Cloudflare.  It performs OAuth
client registration, the authorization-code exchange, refresh, best-effort
revocation and read-only account/zone/DNS-record discovery.  Errors are
normalized and never carry response bodies, tokens or credentials.
"""

from __future__ import annotations

import base64
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from plugins.domains.backend.cloudflare.config import (
    API_BASE,
    AUTHORIZATION_URL,
    REVOKE_URL,
    TOKEN_URL,
    CloudflareConfig,
)

Transport = Callable[
    [str, str, dict[str, str], bytes | None, float],
    tuple[int, dict[str, str], bytes],
]
RefreshCallback = Callable[[], "CloudflareCredentials"]


class CloudflareError(Exception):
    """Base normalized error. The message never contains secrets or bodies."""

    status_code = 503
    code = "domains.dns.cloudflare.unavailable"
    exit_code = 5

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class CloudflareValidation(CloudflareError):
    status_code = 422
    code = "domains.dns.cloudflare.validation"
    exit_code = 2


class CloudflarePermission(CloudflareError):
    status_code = 403
    code = "domains.dns.cloudflare.permission"
    exit_code = 6


class CloudflareNotFound(CloudflareError):
    status_code = 404
    code = "domains.dns.cloudflare.not_found"
    exit_code = 6


class CloudflareConflict(CloudflareError):
    status_code = 409
    code = "domains.dns.cloudflare.conflict"
    exit_code = 4


class CloudflareRateLimit(CloudflareError):
    status_code = 429
    code = "domains.dns.cloudflare.rate_limit"
    exit_code = 5


class CloudflareUpstream(CloudflareError):
    status_code = 502
    code = "domains.dns.cloudflare.upstream"
    exit_code = 5


class CloudflareUnavailable(CloudflareError):
    status_code = 503
    code = "domains.dns.cloudflare.unavailable"
    exit_code = 5


class CloudflareTimeout(CloudflareError):
    status_code = 504
    code = "domains.dns.cloudflare.timeout"
    exit_code = 5


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str
    token_type: str = "bearer"

    def expires_at(self, now: datetime | None = None) -> datetime:
        moment = now or datetime.now(UTC)
        return moment + timedelta(seconds=max(0, self.expires_in))


@dataclass(frozen=True)
class CloudflareCredentials:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class CloudflareZone:
    id: str
    name: str
    status: str
    account_id: str
    account_name: str
    name_servers: tuple[str, ...]
    created_on: str | None
    modified_on: str | None
    record_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "name_servers": list(self.name_servers),
            "created_on": self.created_on,
            "modified_on": self.modified_on,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class CloudflareRecord:
    """A DNS record visible only through the tenant-scoped records API."""

    id: str
    name: str
    type: str
    content: str
    ttl: int
    proxied: bool
    priority: int | None
    created_on: str | None
    modified_on: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "content": self.content,
            "ttl": self.ttl,
            "proxied": self.proxied,
            "priority": self.priority,
            "created_on": self.created_on,
            "modified_on": self.modified_on,
        }


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    # dash.cloudflare.com rejects Python urllib's default fingerprint with a
    # Cloudflare 1010/403 before OAuth is evaluated. Use an honest, stable
    # application identifier; this is not browser impersonation.
    request_headers = {
        "User-Agent": "PanelTutor/0.1 (+https://spanel.lab)",
        **headers,
    }
    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed trusted URL
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
        raise CloudflareTimeout("Cloudflare timed out") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise CloudflareTimeout("Cloudflare timed out") from exc
        raise CloudflareUnavailable("Cloudflare is not reachable") from exc
    except OSError as exc:
        raise CloudflareUnavailable("Cloudflare is not reachable") from exc


def _status_error(status: int) -> CloudflareError:
    if status in {401, 403}:
        return CloudflarePermission("Cloudflare rejected the authorization", http_status=status)
    if status == 404:
        return CloudflareNotFound("Cloudflare resource not found")
    if status == 409:
        return CloudflareConflict("Cloudflare reported a conflict")
    if status == 429:
        return CloudflareRateLimit("Cloudflare rate limit reached")
    if status in {408, 504}:
        return CloudflareTimeout("Cloudflare timed out")
    if status in {400, 422}:
        return CloudflareValidation("Cloudflare rejected the request")
    if status == 502:
        return CloudflareUpstream("Cloudflare returned an invalid upstream response")
    if status >= 500:
        return CloudflareUnavailable("Cloudflare is unavailable")
    return CloudflareValidation("Cloudflare rejected the request")


class CloudflareClient:
    def __init__(
        self,
        config: CloudflareConfig,
        *,
        transport: Transport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or _default_transport

    @property
    def config(self) -> CloudflareConfig:
        return self._config

    # -- transport ---------------------------------------------------------
    def _http(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        try:
            status, _headers, raw = self._transport(
                method, url, headers, body, self._config.timeout_seconds
            )
        except CloudflareError:
            raise
        except TimeoutError as exc:
            raise CloudflareTimeout("Cloudflare timed out") from exc
        except Exception as exc:  # noqa: BLE001 - transport details never leak
            raise CloudflareUnavailable("Cloudflare is not reachable") from exc
        return status, raw

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        status, raw = self._http(method, url, headers, body)
        if not 200 <= status < 300:
            raise _status_error(status)
        if not raw:
            return {}, {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloudflareUpstream("Cloudflare returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid response")
        if payload.get("success") is False:
            raise CloudflareUpstream("Cloudflare returned an invalid response")
        result_info = payload.get("result_info")
        return payload.get("result", payload), result_info if isinstance(result_info, dict) else {}

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        if not token:
            raise CloudflareValidation("a Cloudflare access token is required")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _api_get(self, path: str, token: str) -> tuple[Any, dict[str, Any]]:
        return self._request("GET", f"{API_BASE}{path}", headers=self._bearer(token))

    def _api_write(
        self,
        method: str,
        path: str,
        token: str,
        payload: dict[str, object] | None = None,
    ) -> Any:
        headers = {**self._bearer(token), "Content-Type": "application/json"}
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        result, _info = self._request(
            method, f"{API_BASE}{path}", headers=headers, body=body
        )
        return result

    @staticmethod
    def _basic(client_id: str, client_secret: str) -> str:
        raw = f"{client_id}:{client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    @staticmethod
    def _form(values: dict[str, str]) -> bytes:
        return urlencode(values).encode("utf-8")

    # -- OAuth client registration ----------------------------------------
    def list_token_accounts(self, bootstrap_token: str) -> list[dict[str, str]]:
        """Accounts visible to the bootstrap token; used only for registration."""
        result, _info = self._api_get("/accounts?per_page=50", bootstrap_token)
        if not isinstance(result, list):
            raise CloudflareUpstream("Cloudflare returned an invalid account list")
        accounts: list[dict[str, str]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id", "")).strip()
            if account_id:
                accounts.append({"id": account_id, "name": str(item.get("name", "")).strip()})
        return accounts

    def list_scopes(self, bootstrap_token: str) -> list[dict[str, str]]:
        result, _info = self._api_get("/oauth/scopes?per_page=200", bootstrap_token)
        if not isinstance(result, list):
            raise CloudflareUpstream("Cloudflare returned an invalid scope list")
        scopes: list[dict[str, str]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            scope_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if scope_id and name:
                scopes.append({"id": scope_id, "name": name})
        return scopes

    def register_client(
        self,
        *,
        account_id: str,
        bootstrap_token: str,
        redirect_uri: str,
        scope_ids: list[str],
        client_name: str = "PanelTutor",
    ) -> tuple[str, str]:
        # Cloudflare's current REST resource is `oauth_clients` (underscore),
        # not the dashboard-style `/oauth/clients` path.
        path = f"/accounts/{quote(account_id, safe='')}/oauth_clients"
        body = {
            "client_name": client_name,
            "client_type": "private",
            # Refresh tokens are part of the Authorization Code flow and let
            # the tenant keep working after the one-hour access token expires.
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "redirect_uris": [redirect_uri],
            "scopes": scope_ids,
            "token_endpoint_auth_method": "client_secret_basic",
        }
        result, _info = self._request(
            "POST",
            f"{API_BASE}{path}",
            headers={**self._bearer(bootstrap_token), "Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
        )
        if not isinstance(result, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid client registration")
        client_id = str(result.get("client_id", "")).strip()
        secret = str(result.get("client_secret", "")).strip()
        if not client_id or not secret:
            raise CloudflareUpstream("Cloudflare returned an invalid client registration")
        return client_id, secret

    def update_client(
        self,
        *,
        account_id: str,
        client_id: str,
        bootstrap_token: str,
        redirect_uri: str,
        scope_ids: list[str],
        client_name: str = "PanelTutor",
    ) -> None:
        """Upgrade an existing private client without rotating its secret."""
        path = (
            f"/accounts/{quote(account_id, safe='')}/oauth_clients/"
            f"{quote(client_id, safe='')}"
        )
        body = {
            "client_name": client_name,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "redirect_uris": [redirect_uri],
            "scopes": scope_ids,
            "token_endpoint_auth_method": "client_secret_basic",
        }
        self._request(
            "PATCH",
            f"{API_BASE}{path}",
            headers={**self._bearer(bootstrap_token), "Content-Type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
        )

    # -- authorization and tokens -----------------------------------------
    def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        body = self._form(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )
        return self._token_request(client_id, client_secret, body)

    def refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> OAuthTokens:
        body = self._form(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        return self._token_request(client_id, client_secret, body)

    def _token_request(self, client_id: str, client_secret: str, body: bytes) -> OAuthTokens:
        status, raw = self._http(
            "POST",
            TOKEN_URL,
            {
                "Authorization": self._basic(client_id, client_secret),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body,
        )
        if not 200 <= status < 300:
            raise _status_error(status)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloudflareUpstream("Cloudflare returned an invalid token response") from exc
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise CloudflareUpstream("Cloudflare returned an invalid token response")
        access = str(payload.get("access_token", "")).strip()
        if not access:
            raise CloudflareUpstream("Cloudflare returned an invalid token response")
        try:
            expires_in = int(payload.get("expires_in", 3600) or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        refresh_value = str(payload.get("refresh_token", "") or "").strip() or None
        return OAuthTokens(
            access_token=access,
            refresh_token=refresh_value,
            expires_in=max(0, expires_in),
            scope=str(payload.get("scope", "") or ""),
            token_type=str(payload.get("token_type", "bearer") or "bearer"),
        )

    def revoke(
        self,
        *,
        client_id: str,
        client_secret: str,
        token: str,
    ) -> None:
        if not token:
            return
        status, _raw = self._http(
            "POST",
            REVOKE_URL,
            {
                "Authorization": self._basic(client_id, client_secret),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            self._form({"token": token}),
        )
        if not 200 <= status < 300:
            raise _status_error(status)

    def authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: list[str],
    ) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": " ".join(scopes),
            }
        )
        return f"{AUTHORIZATION_URL}?{query}"

    # -- tenant API --------------------------------------------------------
    def _with_refresh(
        self,
        credentials: CloudflareCredentials,
        refresh: RefreshCallback | None,
        call: Callable[[str], Any],
    ) -> Any:
        refreshed = False
        current = credentials
        while True:
            try:
                return call(current.access_token)
            except CloudflarePermission as exc:
                if refreshed or exc.http_status != 401 or refresh is None:
                    raise
                refreshed = True
                current = refresh()

    def list_accounts(
        self,
        credentials: CloudflareCredentials,
        refresh: RefreshCallback | None = None,
    ) -> list[dict[str, str]]:
        def call(token: str) -> list[dict[str, str]]:
            result, _info = self._api_get("/accounts?per_page=50", token)
            if not isinstance(result, list):
                raise CloudflareUpstream("Cloudflare returned an invalid account list")
            accounts: list[dict[str, str]] = []
            for item in result:
                if not isinstance(item, dict):
                    continue
                account_id = str(item.get("id", "")).strip()
                if account_id:
                    accounts.append(
                        {"id": account_id, "name": str(item.get("name", "")).strip()}
                    )
            return accounts

        return self._with_refresh(credentials, refresh, call)

    @staticmethod
    def _zone(item: dict[str, Any]) -> CloudflareZone:
        account = item.get("account") if isinstance(item.get("account"), dict) else {}
        servers = item.get("name_servers")
        return CloudflareZone(
            id=str(item.get("id", "")).strip(),
            name=str(item.get("name", "")).strip(),
            status=str(item.get("status", "")).strip(),
            account_id=str((account or {}).get("id", "")).strip(),
            account_name=str((account or {}).get("name", "")).strip(),
            name_servers=tuple(
                str(value) for value in (servers or []) if isinstance(value, str)
            ),
            created_on=str(item.get("created_on", "") or "") or None,
            modified_on=str(item.get("modified_on", "") or "") or None,
        )

    @staticmethod
    def _record(item: dict[str, Any]) -> CloudflareRecord:
        raw_priority = item.get("priority")
        try:
            priority = int(raw_priority) if raw_priority is not None else None
        except (TypeError, ValueError):
            priority = None
        return CloudflareRecord(
            id=str(item.get("id", "")).strip(),
            name=str(item.get("name", "")).strip(),
            type=str(item.get("type", "")).strip().upper(),
            content=str(item.get("content", "") or ""),
            ttl=int(item.get("ttl", 0) or 0),
            proxied=bool(item.get("proxied", False)),
            priority=priority,
            created_on=str(item.get("created_on", "") or "") or None,
            modified_on=str(item.get("modified_on", "") or "") or None,
        )

    def list_zones(
        self,
        credentials: CloudflareCredentials,
        refresh: RefreshCallback | None = None,
    ) -> list[CloudflareZone]:
        zones: list[CloudflareZone] = []
        page = 1
        while True:
            def call(token: str, page_number: int = page) -> tuple[Any, dict[str, Any]]:
                return self._api_get(
                    f"/zones?page={page_number}&per_page=50", token
                )

            result, info = self._with_refresh(credentials, refresh, call)
            if not isinstance(result, list):
                raise CloudflareUpstream("Cloudflare returned an invalid zone list")
            zones.extend(self._zone(item) for item in result if isinstance(item, dict))
            total_pages = self._total_pages(info, page, len(result), 50)
            if page >= total_pages or not result:
                break
            page += 1
        return zones

    def get_zone(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        refresh: RefreshCallback | None = None,
    ) -> CloudflareZone:
        def call(token: str) -> Any:
            result, _info = self._api_get(f"/zones/{quote(zone_id, safe='')}", token)
            return result

        result = self._with_refresh(credentials, refresh, call)
        if not isinstance(result, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid zone")
        return self._zone(result)

    def create_zone(
        self,
        credentials: CloudflareCredentials,
        *,
        account_id: str,
        name: str,
        refresh: RefreshCallback | None = None,
    ) -> CloudflareZone:
        def call(token: str) -> Any:
            return self._api_write(
                "POST",
                "/zones",
                token,
                {"account": {"id": account_id}, "name": name, "type": "full"},
            )

        result = self._with_refresh(credentials, refresh, call)
        if not isinstance(result, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid zone")
        return self._zone(result)

    def delete_zone(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        refresh: RefreshCallback | None = None,
    ) -> None:
        def call(token: str) -> Any:
            return self._api_write(
                "DELETE", f"/zones/{quote(zone_id, safe='')}", token
            )

        self._with_refresh(credentials, refresh, call)

    def list_records(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        refresh: RefreshCallback | None = None,
    ) -> list[CloudflareRecord]:
        records: list[CloudflareRecord] = []
        page = 1
        while True:
            def call(token: str, page_number: int = page) -> tuple[Any, dict[str, Any]]:
                return self._api_get(
                    f"/zones/{quote(zone_id, safe='')}/dns_records"
                    f"?page={page_number}&per_page=100",
                    token,
                )

            result, info = self._with_refresh(credentials, refresh, call)
            if not isinstance(result, list):
                raise CloudflareUpstream("Cloudflare returned an invalid record list")
            for item in result:
                if not isinstance(item, dict):
                    continue
                records.append(self._record(item))
            total_pages = self._total_pages(info, page, len(result), 100)
            if page >= total_pages or not result:
                break
            page += 1
        return records

    def create_record(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        payload: dict[str, object],
        refresh: RefreshCallback | None = None,
    ) -> CloudflareRecord:
        def call(token: str) -> Any:
            return self._api_write(
                "POST",
                f"/zones/{quote(zone_id, safe='')}/dns_records",
                token,
                payload,
            )

        result = self._with_refresh(credentials, refresh, call)
        if not isinstance(result, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid DNS record")
        return self._record(result)

    def update_record(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        record_id: str,
        payload: dict[str, object],
        refresh: RefreshCallback | None = None,
    ) -> CloudflareRecord:
        def call(token: str) -> Any:
            return self._api_write(
                "PUT",
                f"/zones/{quote(zone_id, safe='')}/dns_records/"
                f"{quote(record_id, safe='')}",
                token,
                payload,
            )

        result = self._with_refresh(credentials, refresh, call)
        if not isinstance(result, dict):
            raise CloudflareUpstream("Cloudflare returned an invalid DNS record")
        return self._record(result)

    def delete_record(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        record_id: str,
        refresh: RefreshCallback | None = None,
    ) -> None:
        def call(token: str) -> Any:
            return self._api_write(
                "DELETE",
                f"/zones/{quote(zone_id, safe='')}/dns_records/"
                f"{quote(record_id, safe='')}",
                token,
            )

        self._with_refresh(credentials, refresh, call)

    def count_records(
        self,
        credentials: CloudflareCredentials,
        zone_id: str,
        refresh: RefreshCallback | None = None,
    ) -> int:
        def call(token: str) -> tuple[Any, dict[str, Any]]:
            return self._api_get(
                f"/zones/{quote(zone_id, safe='')}/dns_records?page=1&per_page=1",
                token,
            )

        result, info = self._with_refresh(credentials, refresh, call)
        if not isinstance(result, list):
            raise CloudflareUpstream("Cloudflare returned an invalid record list")
        total = info.get("total_count")
        try:
            return int(total) if total is not None else len(result)
        except (TypeError, ValueError):
            return len(result)

    @staticmethod
    def _total_pages(
        info: dict[str, Any], page: int, returned: int, per_page: int
    ) -> int:
        raw = info.get("total_pages")
        try:
            total = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            total = 0
        if total >= page:
            return total
        if returned >= per_page:
            return page + 1
        return page
