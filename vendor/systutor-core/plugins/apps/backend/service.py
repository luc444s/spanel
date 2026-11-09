# ruff: noqa: E501
"""Database-only application and GitHub connection service."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.core.errors import AppError
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext

from .crypto import EncryptedValue, TokenCipher
from .models import Application, GithubConnection
from .oauth import GithubHttp, OAuthFlow, OAuthTokens
from .schemas import ApplicationCreate

_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
    if not result:
        raise AppError("Invalid application name", status_code=422, code="apps.invalid_name")
    return result


def audit(db: Session, action: str, *, tenant_id: str, actor: str | None,
          entity_id: str | None, result: str = "success", details: dict | None = None) -> None:
    record_audit(
        db, tenant_id=tenant_id, branch_id=None, actor_user_id=actor,
        actor_type="user" if actor else "system", module="apps", action=action,
        entity_type="application", entity_id=entity_id, result=result,
        correlation_id=None, request_id=None, details=details or {},
    )


class AppsService:
    def __init__(self, db: Session, cipher: TokenCipher) -> None:
        self.db = db
        self.cipher = cipher

    def connection(self, user_id: str) -> GithubConnection | None:
        return self.db.get(GithubConnection, user_id)

    def save_connection(
        self, user_id: str, tokens: OAuthTokens, identity: dict, tenant_id: str
    ) -> GithubConnection:
        access = self.cipher.encrypt(user_id, tokens.access_token)
        refresh = self.cipher.encrypt(user_id, tokens.refresh_token) if tokens.refresh_token else None
        now = datetime.now(UTC)
        row = self.db.get(GithubConnection, user_id) or GithubConnection(user_id=user_id)
        row.github_user_id = str(identity["id"])
        row.login = str(identity["login"])
        row.avatar_url = identity.get("avatar_url")
        row.scopes = list(tokens.scope)
        row.access_ciphertext, row.access_nonce = access.ciphertext, access.nonce
        row.refresh_ciphertext = refresh.ciphertext if refresh else None
        row.refresh_nonce = refresh.nonce if refresh else None
        row.key_version = access.key_version
        row.access_expires_at = now + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
        row.refresh_expires_at = (
            now + timedelta(seconds=tokens.refresh_token_expires_in)
            if tokens.refresh_token_expires_in else None
        )
        row.reconnect_required = False
        row.connected_at = now
        self.db.add(row)
        audit(self.db, "github.connect", tenant_id=tenant_id, actor=user_id, entity_id=user_id)
        self.db.commit()
        return row

    def disconnect(self, user: User, http: GithubHttp, client_id: str, secret: str) -> None:
        row = self.db.get(GithubConnection, user.id)
        if row is None:
            return
        try:
            token = self.cipher.decrypt(
                user.id, EncryptedValue(row.access_ciphertext, row.access_nonce, row.key_version)
            )
            http.revoke(client_id, secret, token)
        except Exception:
            pass
        self.db.delete(row)
        audit(self.db, "github.disconnect", tenant_id=user.tenant_id, actor=user.id, entity_id=user.id)
        self.db.commit()

    def refresh_connection(self, user_id: str, flow: OAuthFlow) -> GithubConnection:
        with _REFRESH_LOCKS_GUARD:
            lock = _REFRESH_LOCKS.setdefault(user_id, threading.Lock())
        with lock:
            row = self.db.get(GithubConnection, user_id)
            if row is None or row.refresh_ciphertext is None or row.refresh_nonce is None:
                raise ValueError("bad_refresh_token")
            refresh = self.cipher.decrypt(
                user_id, EncryptedValue(row.refresh_ciphertext, row.refresh_nonce, row.key_version)
            )
            self.db.commit()
            try:
                tokens = flow.refresh(refresh)
            except Exception:
                locked = self.db.scalar(select(GithubConnection).where(
                    GithubConnection.user_id == user_id
                ).with_for_update())
                if locked is not None:
                    locked.reconnect_required = True
                    self.db.commit()
                raise
            row = self.db.scalar(select(GithubConnection).where(
                GithubConnection.user_id == user_id
            ).with_for_update())
            if row is None:
                raise ValueError("bad_refresh_token")
            access_value = self.cipher.encrypt(user_id, tokens.access_token)
            refresh_value = self.cipher.encrypt(user_id, tokens.refresh_token or refresh)
            now = datetime.now(UTC)
            row.access_ciphertext, row.access_nonce = access_value.ciphertext, access_value.nonce
            row.refresh_ciphertext, row.refresh_nonce = refresh_value.ciphertext, refresh_value.nonce
            row.key_version = access_value.key_version
            row.scopes = list(tokens.scope)
            row.access_expires_at = now + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
            row.refresh_expires_at = (
                now + timedelta(seconds=tokens.refresh_token_expires_in)
                if tokens.refresh_token_expires_in else None
            )
            row.reconnect_required = False
            row.last_used_at = now
            self.db.commit()
            return row

    def create_application(
        self, context: TenantContext, user: User, body: ApplicationCreate
    ) -> Application:
        connection = self.db.get(GithubConnection, user.id)
        if connection is None or connection.reconnect_required:
            raise AppError("GitHub connection required", status_code=409, code="apps.github_required")
        slug = _slug(body.name)
        if self.db.scalar(select(Application.id).where(
            Application.tenant_id == context.current_tenant_id, Application.slug == slug,
        )):
            raise AppError("Application slug already exists", status_code=409, code="apps.slug_conflict")
        app = Application(
            tenant_id=context.current_tenant_id, name=body.name, slug=slug,
            repository=body.repository, branch=body.branch, compose_file=body.compose_file,
            source_connection_user_id=user.id,
            runtime_project=f"app-{uuid4().hex[:24]}", domain=body.domain,
            ingress_service=body.ingress_service, ingress_port=body.ingress_port,
        )
        self.db.add(app)
        self.db.flush()
        audit(self.db, "application.create", tenant_id=app.tenant_id, actor=user.id, entity_id=app.id)
        self.db.commit()
        self.db.refresh(app)
        return app

    def get_application(self, tenant_id: str, application_id: str) -> Application:
        row = self.db.scalar(select(Application).where(
            Application.id == application_id, Application.tenant_id == tenant_id,
            Application.deleted_at.is_(None),
        ))
        if row is None:
            raise AppError("Application not found", status_code=404, code="apps.not_found")
        return row

    def reconnect(self, app: Application, source_user_id: str, actor_user_id: str) -> None:
        user = self.db.get(User, source_user_id)
        connection = self.db.get(GithubConnection, source_user_id)
        if user is None or not user.is_active or user.tenant_id != app.tenant_id or connection is None:
            raise AppError("Source connection not found", status_code=404, code="apps.source_not_found")
        if connection.reconnect_required:
            raise AppError("Source must reconnect GitHub", status_code=409, code="apps.github_required")
        app.source_connection_user_id = source_user_id
        audit(
            self.db, "application.source_reconnect", tenant_id=app.tenant_id,
            actor=actor_user_id, entity_id=app.id,
            details={"source_connection_user_id": source_user_id},
        )
        self.db.commit()
