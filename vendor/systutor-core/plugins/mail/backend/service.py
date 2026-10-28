from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from fastapi import FastAPI
from sqlalchemy.orm import Session

from plugins.mail.backend.provider import MailProvider
from plugins.mail.backend.schemas import MailAccountsResponse, MailMessageResponse
from plugins.mail.backend.settings import MailSettings, get_mail_settings
from systutor.contracts.domain_namespace import list_tenant_domains, normalize_domain_name
from systutor.core.cache import CacheBackend, cache
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.models import Tenant

logger = logging.getLogger(__name__)

# Single key shared by every tenant. The cached value is ALWAYS the raw list coming
# from the mail provider; tenant filtering happens after the cache, never inside it.
ACCOUNTS_CACHE_KEY = "mail:accounts:raw"

# One background refresh at a time per process. A burst of requests that all find the
# entry stale should produce a single `docker exec`, not one per request.
_REFRESH_LOCK = threading.Lock()


def _run_in_background(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="mail-accounts-refresh", daemon=True).start()


class MailService:
    def __init__(
        self,
        provider: MailProvider,
        db: Session,
        *,
        cache_backend: CacheBackend | None = None,
        settings: MailSettings | None = None,
        app: FastAPI | None = None,
        refresh_runner: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._provider = provider
        self._db = db
        self._cache_override = cache_backend
        self._settings_override = settings
        self._app = app
        self._refresh_runner = refresh_runner

    def _settings(self) -> MailSettings:
        if self._settings_override is not None:
            return self._settings_override
        return get_mail_settings()

    def _cache(self) -> CacheBackend:
        if self._cache_override is not None:
            return self._cache_override
        return cache()

    def _cache_enabled(self) -> bool:
        return self._settings().mail_accounts_cache_enabled

    def _cache_entry_ttl(self) -> int:
        """Redis lifetime of the entry.

        With stale-while-revalidate on, the entry must outlive its freshness window:
        that is what lets a request answer immediately while the refresh runs.
        """
        settings = self._settings()
        if settings.mail_accounts_cache_swr:
            return max(
                settings.mail_accounts_cache_ttl,
                settings.mail_accounts_cache_stale_ttl,
            )
        return settings.mail_accounts_cache_ttl

    def _cache_read_raw(self) -> tuple[list[str], float | None] | None:
        """Returns `(accounts, stored_at)` or None on miss/failure/corruption.

        `stored_at` is None for entries written before this format existed (a bare
        JSON list). Those are served once and refreshed in the background, so a deploy
        never turns the cache into a cold start.
        """
        try:
            raw = self._cache().get(ACCOUNTS_CACHE_KEY)
        except Exception as exc:  # cache must never break the request
            logger.warning("mail accounts cache read failed: %s", exc)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("mail accounts cache value is not valid JSON, treating as miss")
            return None
        if isinstance(payload, list):
            if all(isinstance(item, str) for item in payload):
                return list(payload), None
            logger.warning("mail accounts cache value has unexpected shape, treating as miss")
            return None
        if isinstance(payload, dict):
            accounts = payload.get("accounts")
            stored_at = payload.get("stored_at")
            if (
                isinstance(accounts, list)
                and all(isinstance(item, str) for item in accounts)
                and isinstance(stored_at, (int, float))
                and not isinstance(stored_at, bool)
            ):
                return list(accounts), float(stored_at)
        logger.warning("mail accounts cache value has unexpected shape, treating as miss")
        return None

    def _cache_write_raw(self, accounts: list[str]) -> None:
        try:
            self._cache().set(
                ACCOUNTS_CACHE_KEY,
                json.dumps({"accounts": accounts, "stored_at": time.time()}),
                self._cache_entry_ttl(),
            )
        except Exception as exc:  # cache must never break the request
            logger.warning("mail accounts cache write failed: %s", exc)

    def _cache_add_account(self, email: str) -> None:
        """Reflects a successful create immediately, so the next read is not a cold miss."""
        cached = self._cache_read_raw()
        if cached is None:
            return
        accounts = cached[0]
        if email not in accounts:
            accounts = [*accounts, email]
        self._cache_write_raw(accounts)

    def _refresh_now(self) -> None:
        try:
            accounts = self._call_provider_list_accounts()
        except Exception as exc:  # background refresh cannot break the served response
            logger.warning("mail accounts background refresh failed: %s", exc)
            return
        self._cache_write_raw(accounts)

    def _schedule_refresh(self) -> None:
        if self._refresh_runner is not None:
            self._refresh_runner(self._refresh_now)
            return
        if not _REFRESH_LOCK.acquire(blocking=False):
            return
        try:
            _run_in_background(self._refresh_with_lock_release)
        except Exception as exc:  # e.g. cannot start a thread
            _REFRESH_LOCK.release()
            logger.warning("mail accounts background refresh not started: %s", exc)

    def _refresh_with_lock_release(self) -> None:
        try:
            self._refresh_now()
        finally:
            _REFRESH_LOCK.release()

    def _get_tenant(self, tenant_id: str) -> Tenant:
        tenant = self._db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found")
        return tenant

    def _tenant_domains(self, tenant_id: str) -> tuple[str | None, list[str]]:
        """`tenants.domain` más los dominios administrados, primario primero."""
        tenant = self._get_tenant(tenant_id)
        primary = tenant.domain.strip().lower().removesuffix(".") if tenant.domain else None
        managed: set[str] = set()
        for raw in list_tenant_domains(self._app, self._db, tenant_id):
            try:
                managed.add(normalize_domain_name(raw))
            except ValueError:
                continue
        domains: list[str] = []
        if primary:
            domains.append(primary)
            managed.discard(primary)
        domains.extend(sorted(managed))
        return primary, domains

    def _require_domain_in_tenant(
        self, tenant_id: str, domain: str, *, allow_primary: bool = True
    ) -> str:
        primary, domains = self._tenant_domains(tenant_id)
        if not domains:
            raise AppError(
                "Tenant has no domain configured",
                status_code=422,
                code="tenant_no_domain",
            )
        needle = domain.strip().lower().removesuffix(".")
        allowed = domains if allow_primary else [
            item for item in domains if item != primary
        ]
        if needle not in allowed:
            raise AppError(
                "Email domain does not match tenant domain",
                status_code=403,
                code="domain_mismatch",
            )
        return needle

    def _call_provider_list_accounts(self) -> list[str]:
        try:
            return self._provider.list_accounts()
        except AppError as exc:
            if exc.status_code == 500:
                raise AppError(
                    "Mail server unavailable",
                    status_code=503,
                    code="service_unavailable",
                ) from exc
            raise

    def _raw_accounts(self) -> list[str]:
        """Read-through with stale-while-revalidate.

        Fresh entry: answer from cache. Expired but still within the stale cap: answer
        from cache and refresh in the background, so the user never waits for DMS just
        because the TTL elapsed. Too old or absent: block on the provider.
        """
        if not self._cache_enabled():
            return self._call_provider_list_accounts()

        settings = self._settings()
        cached = self._cache_read_raw()
        if cached is not None:
            accounts, stored_at = cached
            if stored_at is None:
                # Pre-0038 entry (bare list): serve it, then upgrade it in place.
                if settings.mail_accounts_cache_swr:
                    self._schedule_refresh()
                return accounts
            age = max(0.0, time.time() - stored_at)
            if age <= settings.mail_accounts_cache_ttl:
                return accounts
            if (
                settings.mail_accounts_cache_swr
                and age <= settings.mail_accounts_cache_stale_ttl
            ):
                self._schedule_refresh()
                return accounts

        accounts = self._call_provider_list_accounts()
        self._cache_write_raw(accounts)
        return accounts

    def list_accounts(self, tenant_id: str, can_read_all: bool = False) -> MailAccountsResponse:
        primary, domains = self._tenant_domains(tenant_id)
        if can_read_all:
            return MailAccountsResponse(
                domain="all", domains=domains, accounts=self._raw_accounts()
            )

        if not domains:
            raise AppError(
                "Tenant has no domain configured",
                status_code=422,
                code="tenant_no_domain",
            )
        all_accounts = self._raw_accounts()
        owned = set(domains)
        filtered = [
            account
            for account in all_accounts
            if account.rsplit("@", 1)[-1].lower() in owned
        ]
        return MailAccountsResponse(
            domain=primary or domains[0], domains=domains, accounts=filtered
        )

    def create_account(
        self,
        tenant_id: str,
        user_id: str,
        username: str,
        password: str,
        domain: str | None = None,
    ) -> MailMessageResponse:
        primary, _domains = self._tenant_domains(tenant_id)
        if domain is not None and domain.strip():
            selected = self._require_domain_in_tenant(tenant_id, domain)
        else:
            if primary is None:
                raise AppError(
                    "Tenant has no domain configured",
                    status_code=422,
                    code="tenant_no_domain",
                )
            selected = primary
        email = f"{username}@{selected}"

        try:
            self._provider.create_account(email, password)
        except AppError as exc:
            if exc.status_code == 409:
                raise AppError(
                    f"Account already exists: {email}",
                    status_code=409,
                    code="conflict",
                ) from exc
            if exc.status_code == 500:
                raise AppError(
                    "Mail server unavailable",
                    status_code=503,
                    code="service_unavailable",
                ) from exc
            raise

        logger.info("Mail account created: %s by user %s", email, user_id)
        if self._cache_enabled():
            self._cache_add_account(email)
            if self._settings().mail_accounts_cache_swr:
                self._schedule_refresh()
        return MailMessageResponse(message="Account created successfully", email=email)

    def change_password(
        self,
        tenant_id: str,
        user_id: str,
        email: str,
        password: str,
        can_manage_all: bool = False,
    ) -> MailMessageResponse:
        if "@" not in email:
            raise AppError("Invalid email format", status_code=422, code="invalid_email")

        # `mail.accounts.all` already shows accounts of every domain in
        # list_accounts, so it must be able to change them too. Without this the
        # UI offers an account that the API always rejects with domain_mismatch.
        if not can_manage_all:
            _, domains = self._tenant_domains(tenant_id)
            _, email_domain = email.rsplit("@", 1)
            if not domains:
                raise AppError(
                    "Tenant has no domain configured",
                    status_code=422,
                    code="tenant_no_domain",
                )
            if email_domain.strip().lower() not in set(domains):
                raise AppError(
                    "Email domain does not match tenant domain",
                    status_code=403,
                    code="domain_mismatch",
                )

        try:
            self._provider.change_password(email, password)
        except NotFoundError as exc:
            raise NotFoundError(f"Account not found: {email}") from exc
        except AppError as exc:
            if exc.status_code == 500:
                raise AppError(
                    "Mail server unavailable",
                    status_code=503,
                    code="service_unavailable",
                ) from exc
            raise

        logger.info("Mail account password changed: %s by user %s", email, user_id)
        return MailMessageResponse(message="Password updated successfully", email=email)
