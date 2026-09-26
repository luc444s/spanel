from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from plugins.mail.backend.provider import MailProvider
from plugins.mail.backend.schemas import MailAccountsResponse, MailMessageResponse
from plugins.mail.backend.settings import MailSettings, get_mail_settings
from systutor.core.cache import CacheBackend, cache
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.models import Tenant

logger = logging.getLogger(__name__)

# Single key shared by every tenant. The cached value is ALWAYS the raw list coming
# from the mail provider; tenant filtering happens after the cache, never inside it.
ACCOUNTS_CACHE_KEY = "mail:accounts:raw"


class MailService:
    def __init__(
        self,
        provider: MailProvider,
        db: Session,
        *,
        cache_backend: CacheBackend | None = None,
        settings: MailSettings | None = None,
    ) -> None:
        self._provider = provider
        self._db = db
        self._cache_override = cache_backend
        self._settings_override = settings

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

    def _cache_read_raw(self) -> list[str] | None:
        """Returns the cached raw account list, or None on miss/failure/corruption."""
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
        if not isinstance(payload, list) or not all(isinstance(i, str) for i in payload):
            logger.warning("mail accounts cache value has unexpected shape, treating as miss")
            return None
        return list(payload)

    def _cache_write_raw(self, accounts: list[str]) -> None:
        try:
            self._cache().set(
                ACCOUNTS_CACHE_KEY,
                json.dumps(accounts),
                self._settings().mail_accounts_cache_ttl,
            )
        except Exception as exc:  # cache must never break the request
            logger.warning("mail accounts cache write failed: %s", exc)

    def _cache_invalidate(self) -> None:
        try:
            self._cache().delete(ACCOUNTS_CACHE_KEY)
        except Exception as exc:  # cache must never break the request
            logger.warning("mail accounts cache invalidation failed: %s", exc)

    def _get_tenant_domain(self, tenant_id: str) -> str:
        tenant = self._db.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found")
        if not tenant.domain:
            raise AppError(
                "Tenant has no domain configured",
                status_code=422,
                code="tenant_no_domain",
            )
        return tenant.domain

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
        """Read-through: cache first, provider on miss, then populate the cache."""
        if not self._cache_enabled():
            return self._call_provider_list_accounts()

        cached = self._cache_read_raw()
        if cached is not None:
            return cached

        accounts = self._call_provider_list_accounts()
        self._cache_write_raw(accounts)
        return accounts

    def list_accounts(self, tenant_id: str, is_superadmin: bool = False) -> MailAccountsResponse:
        if is_superadmin:
            return MailAccountsResponse(domain="all", accounts=self._raw_accounts())

        domain = self._get_tenant_domain(tenant_id)
        all_accounts = self._raw_accounts()

        filtered = [
            account
            for account in all_accounts
            if account.lower().endswith(f"@{domain.lower()}")
        ]
        return MailAccountsResponse(domain=domain, accounts=filtered)

    def create_account(
        self,
        tenant_id: str,
        user_id: str,
        username: str,
        password: str,
    ) -> MailMessageResponse:
        domain = self._get_tenant_domain(tenant_id)
        email = f"{username}@{domain}"

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
            self._cache_invalidate()
        return MailMessageResponse(message="Account created successfully", email=email)

    def change_password(
        self,
        tenant_id: str,
        user_id: str,
        email: str,
        password: str,
    ) -> MailMessageResponse:
        domain = self._get_tenant_domain(tenant_id)

        if "@" not in email:
            raise AppError("Invalid email format", status_code=422, code="invalid_email")

        _, email_domain = email.rsplit("@", 1)
        if email_domain.lower() != domain.lower():
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
