"""Tenant-aware Cloudflare OAuth, persistence and DNS management.

This module orchestrates the OAuth flow and storage.  It never returns tokens,
client secrets, authorization codes or PKCE verifiers. DNS record content is
returned only through tenant-scoped APIs and is never included in audits.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.domains.backend.cloudflare.client import (
    CloudflareClient,
    CloudflareConflict,
    CloudflareCredentials,
    CloudflareError,
    CloudflareNotFound,
    CloudflarePermission,
    CloudflareValidation,
    CloudflareZone,
    OAuthTokens,
)
from plugins.domains.backend.cloudflare.config import (
    OFFLINE_ACCESS_SCOPE,
    REQUIRED_SCOPE_NAMES,
    CloudflareConfig,
    CloudflareConfigError,
)
from plugins.domains.backend.cloudflare.crypto import (
    CloudflareCipher,
    CloudflareCryptoError,
    EncryptedValue,
)
from plugins.domains.backend.cloudflare.oauth import (
    FLOW_CONSUMED,
    FLOW_DENIED,
    FLOW_EXPIRED,
    FLOW_FAILED,
    FLOW_PENDING,
    failure_page,
    generate_pkce,
    generate_state,
    resolve_scope_ids,
    state_hash,
    success_page,
)
from plugins.domains.backend.cloudflare.store import (
    cancel_pending_flows,
    get_connection,
    get_flow,
    get_flow_by_state_hash,
    get_oauth_client,
    list_pending_flows,
)
from plugins.domains.backend.models import (
    CloudflareConnection,
    CloudflareOAuthClient,
    CloudflareOAuthFlow,
)
from systutor.kernel.audit.service import record_audit
from systutor.kernel.tenants.context import TenantContext

Publisher = Callable[[str, dict[str, object], str | None, str], None]

PURPOSE_CLIENT_SECRET = "cloudflare-oauth-client-secret"
PURPOSE_VERIFIER = "cloudflare-pkce-verifier"
PURPOSE_ACCESS = "cloudflare-access-token"
PURPOSE_REFRESH = "cloudflare-refresh-token"
FLOW_TTL_SECONDS = 300
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
_ERROR_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")


def _oauth_scope_names(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        if isinstance(value, dict) and value.get("name"):
            names.append(str(value["name"]))
        elif isinstance(value, str):
            names.append(value)
    return names


def _oauth_scope_ids(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value["id"]) for value in values if isinstance(value, dict) and value.get("id")]


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    moment = _aware(value)
    return moment.isoformat() if moment is not None else None


class CallbackResult:
    def __init__(self, success: bool, html: str) -> None:
        self.success = success
        self.html = html


class CloudflareService:
    def __init__(
        self,
        db: Session,
        app: FastAPI | None,
        client: CloudflareClient,
        config: CloudflareConfig,
        *,
        cipher: CloudflareCipher | None = None,
        publisher: Publisher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._app = app
        self._client = client
        self._config = config
        self._cipher_instance = cipher
        self._publisher = publisher
        self._clock = clock or _now

    @property
    def client(self) -> CloudflareClient:
        return self._client

    @property
    def config(self) -> CloudflareConfig:
        return self._config

    def _cipher(self) -> CloudflareCipher:
        if self._cipher_instance is None:
            try:
                self._cipher_instance = CloudflareCipher.from_config(self._config)
            except (CloudflareConfigError, CloudflareCryptoError) as exc:
                raise CloudflareValidation(str(exc)) from exc
        return self._cipher_instance

    # -- encryption helpers ------------------------------------------------
    @staticmethod
    def _client_identity(account_id: str, client_id: str) -> str:
        return f"{account_id}:{client_id}"

    @staticmethod
    def _flow_identity(tenant_id: str, flow_id: str) -> str:
        return f"{tenant_id}:{flow_id}"

    def _decrypt(self, purpose: str, identity: str, row: Any) -> str:
        value = EncryptedValue(
            ciphertext=row.ciphertext,
            nonce=row.nonce,
            key_version=row.key_version,
        )
        try:
            return self._cipher().decrypt(purpose, identity, value)
        except CloudflareCryptoError as exc:
            raise CloudflareValidation("stored Cloudflare credentials are unreadable") from exc

    # -- audit and events ---------------------------------------------------
    def _audit(
        self,
        action: str,
        *,
        tenant_id: str | None,
        entity_id: str,
        details: dict[str, object],
        context: TenantContext | None = None,
    ) -> None:
        actor_user_id = context.current_user_id if context is not None else None
        actor_type = "user" if context is not None else "system"
        branch_id = context.current_branch_id if context is not None else None
        if context is not None:
            tenant_id = context.current_tenant_id
        record_audit(
            self._db,
            tenant_id=tenant_id,
            branch_id=branch_id,
            actor_user_id=actor_user_id,
            actor_type=actor_type,
            module="domains",
            action=action,
            entity_type="cloudflare",
            entity_id=entity_id,
            result="success",
            correlation_id=None,
            request_id=None,
            details=details,
        )

    def _publish(
        self,
        event_name: str,
        payload: dict[str, object],
        tenant_id: str | None,
        entity_id: str,
    ) -> None:
        if self._publisher is not None:
            self._publisher(event_name, payload, tenant_id, entity_id)
            return
        if self._app is None:
            return
        context = getattr(self._app.state, "domains_context", None)
        if context is None:
            return
        try:
            context.publish_event(
                event_name=event_name,
                payload=payload,
                tenant_id=tenant_id,
                entity_type="cloudflare",
                entity_id=entity_id,
            )
        except Exception:  # noqa: BLE001 - audit already recorded the commit
            pass

    # -- OAuth client registration -----------------------------------------
    def register(
        self,
        *,
        account_id: str,
        bootstrap_token: str,
        redirect_uri: str,
        apply: bool,
    ) -> dict[str, object]:
        account = self._resolve_account(account_id, bootstrap_token)
        try:
            callback = self._config.validate_redirect_uri(redirect_uri)
        except CloudflareConfigError as exc:
            raise CloudflareValidation(str(exc)) from exc
        if not bootstrap_token:
            raise CloudflareValidation("the bootstrap token file is empty")
        plan: dict[str, object] = {
            "applied": False,
            "account_id": account,
            "redirect_uri": callback,
            "grant_type": "authorization_code",
            "token_endpoint_auth_method": "client_secret_basic",
            "client_type": "private",
            "scopes": list(REQUIRED_SCOPE_NAMES),
            "client_secret": "<redacted>",
        }
        if not apply:
            return plan

        scopes = self._client.list_scopes(bootstrap_token)
        scope_ids = resolve_scope_ids(scopes)
        scope_rows = [
            {"id": scope_id, "name": name}
            for name, scope_id in zip(REQUIRED_SCOPE_NAMES, scope_ids, strict=True)
        ] + [{"id": OFFLINE_ACCESS_SCOPE, "name": "Offline Access"}]
        existing = get_oauth_client(self._db)
        if existing is not None:
            if existing.account_id != account:
                raise CloudflareConflict(
                    "a Cloudflare OAuth client is registered for another account"
                )
            # Idempotent repair: clients created before refresh support only had
            # `authorization_code`, so their access token expired after an hour
            # and could not be renewed. PATCH keeps the existing secret.
            self._client.update_client(
                account_id=existing.account_id,
                client_id=existing.client_id,
                bootstrap_token=bootstrap_token,
                redirect_uri=callback,
                scope_ids=scope_ids,
            )
            existing.scopes_json = scope_rows
            existing.redirect_uri = callback
            self._audit(
                "domains.dns.cloudflare_registered",
                tenant_id=None,
                entity_id=existing.account_id,
                details={
                    "account_id": existing.account_id,
                    "client_id": existing.client_id,
                    "redirect_uri": callback,
                    "repaired": True,
                },
            )
            self._db.commit()
            return {
                "applied": True,
                "account_id": existing.account_id,
                "client_id": existing.client_id,
                "redirect_uri": existing.redirect_uri,
                "scopes": list(REQUIRED_SCOPE_NAMES),
            }

        client_id, secret = self._client.register_client(
            account_id=account,
            bootstrap_token=bootstrap_token,
            redirect_uri=callback,
            scope_ids=scope_ids,
        )
        encrypted = self._cipher().encrypt(
            PURPOSE_CLIENT_SECRET,
            self._client_identity(account, client_id),
            secret,
        )
        row = CloudflareOAuthClient(
            id=1,
            account_id=account,
            client_id=client_id,
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            redirect_uri=callback,
            scopes_json=scope_rows,
        )
        self._db.add(row)
        self._db.flush()
        self._audit(
            "domains.dns.cloudflare_registered",
            tenant_id=None,
            entity_id=account,
            details={
                "account_id": account,
                "client_id": client_id,
                "redirect_uri": callback,
                "scopes": list(REQUIRED_SCOPE_NAMES),
            },
        )
        self._db.commit()
        return {
            "applied": True,
            "account_id": account,
            "client_id": client_id,
            "redirect_uri": callback,
            "scopes": list(REQUIRED_SCOPE_NAMES),
        }

    def _resolve_account(self, account_id: str, bootstrap_token: str) -> str:
        account = (account_id or "").strip()
        if account:
            if not _ACCOUNT_RE.match(account):
                raise CloudflareValidation("the Cloudflare account id is invalid")
            return account
        if not bootstrap_token:
            raise CloudflareValidation("a Cloudflare bootstrap token is required")
        try:
            accounts = self._client.list_token_accounts(bootstrap_token)
        except CloudflarePermission as exc:
            raise CloudflareValidation(
                "the token cannot list accounts; provide the Cloudflare account id"
            ) from exc
        ids = [entry["id"] for entry in accounts if entry.get("id")]
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise CloudflareValidation("the token has no accessible Cloudflare account")
        raise CloudflareValidation(
            "the token can access multiple accounts; provide the Cloudflare account id"
        )

    def client_status(self) -> dict[str, object]:
        row = get_oauth_client(self._db)
        if row is None:
            return {
                "registered": False,
                "account_id": None,
                "client_id": None,
                "redirect_uri": self._config.redirect_uri,
                "scopes": list(REQUIRED_SCOPE_NAMES),
            }
        scope_ids = _oauth_scope_ids(row.scopes_json)
        return {
            "registered": bool(scope_ids),
            "needs_repair": OFFLINE_ACCESS_SCOPE not in scope_ids,
            "account_id": row.account_id,
            "client_id": row.client_id,
            "redirect_uri": row.redirect_uri,
            "scopes": _oauth_scope_names(row.scopes_json),
        }

    def _client_secrets(self) -> tuple[CloudflareOAuthClient, str]:
        row = get_oauth_client(self._db)
        if row is None:
            raise CloudflareValidation("the Cloudflare OAuth client is not registered")
        secret = self._decrypt(
            PURPOSE_CLIENT_SECRET,
            self._client_identity(row.account_id, row.client_id),
            _SecretRow(row.secret_ciphertext, row.secret_nonce, row.key_version),
        )
        return row, secret

    # -- authorization flow -------------------------------------------------
    def start_flow_for_tenant(self, tenant_id: str) -> dict[str, object]:
        client_row, _secret = self._client_secrets()
        now = self._clock()
        state = generate_state()
        verifier, challenge = generate_pkce()
        flow = CloudflareOAuthFlow(
            tenant_id=tenant_id,
            state_hash=state_hash(state),
            verifier_ciphertext=b"",
            verifier_nonce=b"",
            key_version=self._cipher().key_version,
            redirect_uri=client_row.redirect_uri,
            status=FLOW_PENDING,
            expires_at=now + timedelta(seconds=FLOW_TTL_SECONDS),
        )
        self._db.add(flow)
        self._db.flush()
        encrypted = self._cipher().encrypt(
            PURPOSE_VERIFIER,
            self._flow_identity(tenant_id, flow.id),
            verifier,
        )
        flow.verifier_ciphertext = encrypted.ciphertext
        flow.verifier_nonce = encrypted.nonce
        flow.key_version = encrypted.key_version
        self._db.commit()
        scope_ids = _oauth_scope_ids(client_row.scopes_json)
        if OFFLINE_ACCESS_SCOPE not in scope_ids:
            scope_ids.append(OFFLINE_ACCESS_SCOPE)
        url = self._client.authorization_url(
            client_id=client_row.client_id,
            redirect_uri=client_row.redirect_uri,
            state=state,
            code_challenge=challenge,
            scopes=scope_ids,
        )
        return {
            "tenant_id": tenant_id,
            "flow_id": flow.id,
            "authorization_url": url,
            "redirect_uri": client_row.redirect_uri,
            "scopes": _oauth_scope_names(client_row.scopes_json),
            "expires_at": _iso(flow.expires_at),
        }

    def start(self, context: TenantContext) -> dict[str, object]:
        return self.start_flow_for_tenant(self._tenant_id(context))

    def flow_status(self, flow_id: str) -> dict[str, object]:
        flow = get_flow(self._db, flow_id)
        if flow is None:
            raise CloudflareNotFound("authorization flow not found")
        return {
            "flow_id": flow.id,
            "tenant_id": flow.tenant_id,
            "status": flow.status,
            "error_code": flow.error_code,
            "expires_at": _iso(flow.expires_at),
            "consumed_at": _iso(flow.consumed_at),
        }

    def complete_callback(
        self,
        *,
        state: str,
        code: str | None = None,
        error: str | None = None,
        redirect_uri: str | None = None,
    ) -> CallbackResult:
        if not state or len(state) > 512:
            return CallbackResult(False, failure_page())
        flow = get_flow_by_state_hash(self._db, state_hash(state))
        if flow is None:
            return CallbackResult(False, failure_page())
        now = self._clock()
        if redirect_uri is not None and redirect_uri != flow.redirect_uri:
            return CallbackResult(False, failure_page())
        if flow.status != FLOW_PENDING or flow.consumed_at is not None:
            return CallbackResult(False, failure_page())
        expires_at = _aware(flow.expires_at)
        if expires_at is None or expires_at <= now:
            flow.status = FLOW_EXPIRED
            flow.consumed_at = now
            self._db.commit()
            return CallbackResult(False, failure_page())

        if error is not None:
            flow.status = FLOW_DENIED
            flow.error_code = error if _ERROR_RE.match(error) else "access_denied"
            flow.consumed_at = now
            self._db.commit()
            return CallbackResult(False, failure_page())
        if not code:
            flow.status = FLOW_FAILED
            flow.error_code = "invalid_request"
            flow.consumed_at = now
            self._db.commit()
            return CallbackResult(False, failure_page())

        try:
            verifier = self._decrypt(
                PURPOSE_VERIFIER,
                self._flow_identity(flow.tenant_id, flow.id),
                _SecretRow(
                    flow.verifier_ciphertext, flow.verifier_nonce, flow.key_version
                ),
            )
        except CloudflareError:
            flow.status = FLOW_FAILED
            flow.error_code = "invalid_verifier"
            flow.consumed_at = now
            self._db.commit()
            return CallbackResult(False, failure_page())

        # Single use: consume before any network exchange can be observed.
        flow.status = FLOW_CONSUMED
        flow.consumed_at = now
        self._db.commit()

        client_row, secret = self._client_secrets()
        try:
            tokens = self._client.exchange_code(
                client_id=client_row.client_id,
                client_secret=secret,
                code=code,
                redirect_uri=flow.redirect_uri,
                code_verifier=verifier,
            )
        except Exception as exc:  # noqa: BLE001 - public callback stays opaque
            self._mark_callback_failed(flow.id, now, exc, stage="token_exchange")
            return CallbackResult(False, failure_page())

        try:
            account_id, account_name = self._discover_account(
                tokens, client_row.account_id
            )
            connection = self._upsert_connection(
                tenant_id=flow.tenant_id,
                account_id=account_id,
                account_name=account_name,
                tokens=tokens,
            )
        except Exception as exc:  # noqa: BLE001 - the public page must stay opaque
            self._mark_callback_failed(flow.id, now, exc, stage="account_discovery")
            return CallbackResult(False, failure_page())

        self._audit(
            "domains.dns.cloudflare_connected",
            tenant_id=flow.tenant_id,
            entity_id=flow.tenant_id,
            details={
                "account_id": account_id,
                "account_name": account_name,
                "scopes": list(connection.scopes_json or []),
            },
        )
        self._db.commit()
        self._publish(
            "domains.dns.cloudflare_connected",
            {"account_id": account_id, "account_name": account_name},
            flow.tenant_id,
            flow.tenant_id,
        )
        return CallbackResult(True, success_page())

    def _mark_callback_failed(
        self, flow_id: str, now: datetime, exc: Exception, *, stage: str
    ) -> None:
        """Persist only a stage and normalized code; never an upstream body."""
        self._db.rollback()
        failed = get_flow(self._db, flow_id)
        if failed is None:
            return
        code = exc.code.rsplit(".", 1)[-1] if isinstance(exc, CloudflareError) else "failed"
        failed.status = FLOW_FAILED
        failed.error_code = f"{stage}.{code}"[:64]
        failed.consumed_at = now
        self._db.commit()

    def _discover_account(
        self, tokens: OAuthTokens, preferred: str
    ) -> tuple[str, str]:
        credentials = CloudflareCredentials(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at(self._clock()),
        )
        # `/accounts` requires Account Settings Read, which is deliberately not
        # part of this client's consent. Zone Read already returns each zone's
        # account id/name, and a private OAuth client is bound to `preferred`.
        # An account with no zones is still a valid connection, so retain its
        # registered id and use a neutral display name.
        zones = self._client.list_zones(credentials)
        for zone in zones:
            if zone.account_id == preferred:
                return preferred, zone.account_name or "Cloudflare"
        if zones:
            return zones[0].account_id, zones[0].account_name or "Cloudflare"
        return preferred, "Cloudflare"

    def _upsert_connection(
        self,
        *,
        tenant_id: str,
        account_id: str,
        account_name: str,
        tokens: OAuthTokens,
    ) -> CloudflareConnection:
        existing = get_connection(self._db, tenant_id, for_update=True)
        scopes = list(REQUIRED_SCOPE_NAMES)
        access = self._cipher().encrypt(PURPOSE_ACCESS, tenant_id, tokens.access_token)
        refresh_value = tokens.refresh_token
        if refresh_value is None and existing is not None:
            # A refresh response may omit the unchanged refresh token.
            refresh_value = self._decrypt(
                PURPOSE_REFRESH,
                tenant_id,
                _SecretRow(
                    existing.refresh_ciphertext,
                    existing.refresh_nonce,
                    existing.key_version,
                ),
            ) if existing.refresh_ciphertext is not None else None
        refresh = (
            self._cipher().encrypt(PURPOSE_REFRESH, tenant_id, refresh_value)
            if refresh_value
            else None
        )
        expires_at = tokens.expires_at(self._clock())
        row = existing or CloudflareConnection(tenant_id=tenant_id)
        row.cloudflare_account_id = account_id
        row.account_name = account_name
        row.access_ciphertext = access.ciphertext
        row.access_nonce = access.nonce
        row.refresh_ciphertext = refresh.ciphertext if refresh else None
        row.refresh_nonce = refresh.nonce if refresh else None
        row.key_version = access.key_version
        row.scopes_json = scopes
        row.expires_at = expires_at
        row.status = "active"
        if existing is None:
            self._db.add(row)
        self._db.flush()
        self._db.commit()
        return row

    # -- connection status and refresh -------------------------------------
    def _tenant_id(self, context: TenantContext) -> str:
        if not context.current_tenant_id:
            raise CloudflarePermission("an active tenant is required")
        return context.current_tenant_id

    def _connection(self, tenant_id: str, *, for_update: bool = False) -> CloudflareConnection:
        row = get_connection(self._db, tenant_id, for_update=for_update)
        if row is None:
            raise CloudflareNotFound("no active Cloudflare connection for this tenant")
        return row

    def _credentials(self, row: CloudflareConnection) -> CloudflareCredentials:
        access = self._decrypt(
            PURPOSE_ACCESS,
            row.tenant_id,
            _SecretRow(row.access_ciphertext, row.access_nonce, row.key_version),
        )
        refresh = None
        if row.refresh_ciphertext is not None and row.refresh_nonce is not None:
            refresh = self._decrypt(
                PURPOSE_REFRESH,
                row.tenant_id,
                _SecretRow(row.refresh_ciphertext, row.refresh_nonce, row.key_version),
            )
        return CloudflareCredentials(
            access_token=access,
            refresh_token=refresh,
            expires_at=_aware(row.expires_at),
        )

    def _refresh_locked(
        self, tenant_id: str, stale_access: str
    ) -> CloudflareCredentials:
        row = get_connection(self._db, tenant_id, for_update=True)
        if row is None:
            raise CloudflareNotFound("no active Cloudflare connection for this tenant")
        current = self._credentials(row)
        if current.access_token != stale_access:
            return current
        if not current.refresh_token:
            raise CloudflarePermission("Cloudflare authorization must be renewed")
        client_row, secret = self._client_secrets()
        tokens = self._client.refresh(
            client_id=client_row.client_id,
            client_secret=secret,
            refresh_token=current.refresh_token,
        )
        access = self._cipher().encrypt(PURPOSE_ACCESS, tenant_id, tokens.access_token)
        refresh_value = tokens.refresh_token or current.refresh_token
        refresh = self._cipher().encrypt(PURPOSE_REFRESH, tenant_id, refresh_value)
        row.access_ciphertext = access.ciphertext
        row.access_nonce = access.nonce
        row.refresh_ciphertext = refresh.ciphertext
        row.refresh_nonce = refresh.nonce
        row.key_version = access.key_version
        row.expires_at = tokens.expires_at(self._clock())
        row.status = "active"
        self._db.commit()
        return CloudflareCredentials(
            access_token=tokens.access_token,
            refresh_token=refresh_value,
            expires_at=_aware(row.expires_at),
        )

    def status_for_tenant(self, tenant_id: str) -> dict[str, object]:
        row = self._connection(tenant_id)
        return {
            "connected": row.status == "active",
            "tenant_id": row.tenant_id,
            "account_id": row.cloudflare_account_id,
            "account_name": row.account_name,
            "scopes": list(row.scopes_json or []),
            "expires_at": _iso(row.expires_at),
            "status": row.status,
            "updated_at": _iso(row.updated_at),
        }

    def status(self, context: TenantContext) -> dict[str, object]:
        return self.status_for_tenant(self._tenant_id(context))

    # -- tenant-scoped discovery and DNS management ------------------------
    def _active_credentials(self, tenant_id: str) -> CloudflareCredentials:
        row = self._connection(tenant_id)
        credentials = self._credentials(row)
        expires_at = credentials.expires_at
        if expires_at is not None and expires_at <= self._clock():
            if credentials.refresh_token:
                return self._refresh_locked(tenant_id, credentials.access_token)
            raise CloudflarePermission("Cloudflare authorization must be renewed")
        return credentials

    def _zones(self, tenant_id: str) -> list[CloudflareZone]:
        credentials = self._active_credentials(tenant_id)

        def refresh() -> CloudflareCredentials:
            return self._refresh_locked(tenant_id, credentials.access_token)

        zones = self._client.list_zones(credentials, refresh)
        enriched: list[CloudflareZone] = []
        for zone in zones:
            count = self._client.count_records(credentials, zone.id, refresh)
            enriched.append(replace(zone, record_count=count))
        return enriched

    def zones_for_tenant(self, tenant_id: str) -> list[dict[str, object]]:
        return [zone.to_dict() for zone in self._zones(tenant_id)]

    def zones(self, context: TenantContext) -> list[dict[str, object]]:
        return self.zones_for_tenant(self._tenant_id(context))

    def records_for_tenant(
        self, tenant_id: str, zone_id: str, *, include_content: bool = False
    ) -> list[dict[str, object]]:
        credentials, refresh, _zone = self._zone_access(tenant_id, zone_id)
        items = [
            record.to_dict() for record in self._client.list_records(credentials, zone_id, refresh)
        ]
        if not include_content:
            for item in items:
                item.pop("content", None)
        return items

    def records(self, context: TenantContext, zone_id: str) -> list[dict[str, object]]:
        return self.records_for_tenant(
            self._tenant_id(context), zone_id, include_content=True
        )

    def _zone_access(
        self, tenant_id: str, zone_id: str
    ) -> tuple[CloudflareCredentials, Callable[[], CloudflareCredentials], CloudflareZone]:
        connection = self._connection(tenant_id)
        credentials = self._active_credentials(tenant_id)

        def refresh() -> CloudflareCredentials:
            return self._refresh_locked(tenant_id, credentials.access_token)

        zone = self._client.get_zone(credentials, zone_id, refresh)
        if (
            not zone.id
            or (
                zone.account_id
                and connection.cloudflare_account_id
                and zone.account_id != connection.cloudflare_account_id
            )
        ):
            raise CloudflareNotFound("Cloudflare zone not found")
        return credentials, refresh, zone

    @staticmethod
    def _record_audit_details(
        zone_id: str, record_id: str, values: dict[str, object]
    ) -> dict[str, object]:
        return {
            "zone_id": zone_id,
            "record_id": record_id,
            "type": str(values.get("type", "")),
            "name": str(values.get("name", "")),
        }

    def create_record(
        self,
        context: TenantContext,
        zone_id: str,
        values: dict[str, object],
    ) -> dict[str, object]:
        tenant_id = self._tenant_id(context)
        credentials, refresh, _zone = self._zone_access(tenant_id, zone_id)
        record = self._client.create_record(credentials, zone_id, values, refresh)
        self._audit(
            "domains.dns.cloudflare_record_created",
            tenant_id=tenant_id,
            entity_id=record.id,
            details=self._record_audit_details(zone_id, record.id, values),
            context=context,
        )
        self._db.commit()
        return record.to_dict()

    def update_record(
        self,
        context: TenantContext,
        zone_id: str,
        record_id: str,
        values: dict[str, object],
    ) -> dict[str, object]:
        tenant_id = self._tenant_id(context)
        credentials, refresh, _zone = self._zone_access(tenant_id, zone_id)
        record = self._client.update_record(
            credentials, zone_id, record_id, values, refresh
        )
        self._audit(
            "domains.dns.cloudflare_record_updated",
            tenant_id=tenant_id,
            entity_id=record.id,
            details=self._record_audit_details(zone_id, record.id, values),
            context=context,
        )
        self._db.commit()
        return record.to_dict()

    def delete_record(
        self, context: TenantContext, zone_id: str, record_id: str
    ) -> dict[str, object]:
        tenant_id = self._tenant_id(context)
        credentials, refresh, _zone = self._zone_access(tenant_id, zone_id)
        self._client.delete_record(credentials, zone_id, record_id, refresh)
        self._audit(
            "domains.dns.cloudflare_record_deleted",
            tenant_id=tenant_id,
            entity_id=record_id,
            details={"zone_id": zone_id, "record_id": record_id},
            context=context,
        )
        self._db.commit()
        return {"zone_id": zone_id, "record_id": record_id, "deleted": True}

    # -- disconnect ---------------------------------------------------------
    def disconnect_for_tenant(
        self,
        tenant_id: str,
        *,
        apply: bool,
        context: TenantContext | None = None,
    ) -> dict[str, object]:
        row = get_connection(self._db, tenant_id)
        if row is None:
            return {
                "tenant_id": tenant_id,
                "applied": bool(apply),
                "disconnected": False,
                "already_disconnected": True,
                "revoked": False,
                "pending_flows": 0,
            }
        pending = len(list_pending_flows(self._db, tenant_id))
        scopes = list(row.scopes_json or [])
        if not apply:
            return {
                "tenant_id": tenant_id,
                "applied": False,
                "disconnected": False,
                "already_disconnected": False,
                "revoked": False,
                "account_id": row.cloudflare_account_id,
                "account_name": row.account_name,
                "scopes": scopes,
                "pending_flows": pending,
            }

        revoked = self._revoke(row)
        cancel_pending_flows(self._db, tenant_id, now=self._clock())
        self._db.delete(row)
        self._db.flush()
        self._audit(
            "domains.dns.cloudflare_disconnected",
            tenant_id=tenant_id,
            entity_id=tenant_id,
            details={
                "account_id": row.cloudflare_account_id,
                "account_name": row.account_name,
                "scopes": scopes,
                "revoked": revoked,
                "cancelled_flows": pending,
            },
            context=context,
        )
        self._db.commit()
        self._publish(
            "domains.dns.cloudflare_disconnected",
            {"tenant_id": tenant_id, "revoked": revoked},
            tenant_id,
            tenant_id,
        )
        return {
            "tenant_id": tenant_id,
            "applied": True,
            "disconnected": True,
            "already_disconnected": False,
            "revoked": revoked,
            "cancelled_flows": pending,
        }

    def _revoke(self, row: CloudflareConnection) -> bool:
        try:
            client_row, secret = self._client_secrets()
            credentials = self._credentials(row)
            token = credentials.refresh_token or credentials.access_token
            self._client.revoke(
                client_id=client_row.client_id,
                client_secret=secret,
                token=token,
            )
            return True
        except Exception:  # noqa: BLE001 - revocation is explicitly best effort
            return False

    def disconnect(self, context: TenantContext, *, apply: bool) -> dict[str, object]:
        return self.disconnect_for_tenant(
            self._tenant_id(context), apply=apply, context=context
        )


class _SecretRow:
    """Small adapter exposing encrypted columns without ORM coupling."""

    __slots__ = ("ciphertext", "nonce", "key_version")

    def __init__(
        self, ciphertext: bytes | None, nonce: bytes | None, key_version: int
    ) -> None:
        self.ciphertext = ciphertext or b""
        self.nonce = nonce or b""
        self.key_version = key_version
