from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from plugins.mail.backend.provider import MailProvider
from plugins.mail.backend.schemas import MailAccountsResponse, MailMessageResponse
from systutor.core.errors import AppError, NotFoundError
from systutor.kernel.tenants.models import Tenant

logger = logging.getLogger(__name__)


class MailService:
    def __init__(self, provider: MailProvider, db: Session) -> None:
        self._provider = provider
        self._db = db

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

    def list_accounts(self, tenant_id: str, is_superadmin: bool = False) -> MailAccountsResponse:
        if is_superadmin:
            try:
                all_accounts = self._provider.list_accounts()
            except AppError as exc:
                if exc.status_code == 500:
                    raise AppError(
                        "Mail server unavailable",
                        status_code=503,
                        code="service_unavailable",
                    ) from exc
                raise
            return MailAccountsResponse(domain="all", accounts=all_accounts)

        domain = self._get_tenant_domain(tenant_id)
        try:
            all_accounts = self._provider.list_accounts()
        except AppError as exc:
            if exc.status_code == 500:
                raise AppError(
                    "Mail server unavailable",
                    status_code=503,
                    code="service_unavailable",
                ) from exc
            raise

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
