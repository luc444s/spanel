from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from plugins.mail.backend.provider import DockerMailServerProvider
from plugins.mail.backend.schemas import (
    ChangePasswordRequest,
    CreateAccountRequest,
    MailAccountsResponse,
    MailMessageResponse,
)
from plugins.mail.backend.service import MailService
from plugins.mail.backend.settings import get_mail_settings
from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import require_permission
from systutor.kernel.auth.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["mail"])


def _get_mail_service(db: Session = Depends(get_db_session)) -> MailService:
    settings = get_mail_settings()
    provider = DockerMailServerProvider(
        host=settings.mail_server_host,
        port=settings.mail_server_port,
        user=settings.mail_server_user,
        password=settings.mail_server_password,
        container=settings.mail_dms_container,
        use_ssh=settings.mail_use_ssh,
    )
    return MailService(provider=provider, db=db)


@router.get("/accounts", response_model=MailAccountsResponse)
def list_accounts(
    request: Request,
    current_user: User = Depends(require_permission("mail.accounts.read")),
    mail_service: MailService = Depends(_get_mail_service),
) -> MailAccountsResponse:
    tenant_id = request.state.current_tenant_id
    is_superadmin = getattr(request.state, "is_superadmin", False)
    return mail_service.list_accounts(tenant_id, is_superadmin=is_superadmin)


@router.post("/accounts", response_model=MailMessageResponse, status_code=201)
def create_account(
    request: Request,
    body: CreateAccountRequest,
    current_user: User = Depends(require_permission("mail.accounts.create")),
    mail_service: MailService = Depends(_get_mail_service),
) -> MailMessageResponse:
    tenant_id = request.state.current_tenant_id
    result = mail_service.create_account(
        tenant_id=tenant_id,
        user_id=current_user.id,
        username=body.username,
        password=body.password,
    )
    return result


@router.put("/accounts/{email}/password", response_model=MailMessageResponse)
def change_password(
    request: Request,
    email: str,
    body: ChangePasswordRequest,
    current_user: User = Depends(require_permission("mail.account.password")),
    mail_service: MailService = Depends(_get_mail_service),
) -> MailMessageResponse:
    tenant_id = request.state.current_tenant_id
    result = mail_service.change_password(
        tenant_id=tenant_id,
        user_id=current_user.id,
        email=email,
        password=body.password,
    )
    return result
