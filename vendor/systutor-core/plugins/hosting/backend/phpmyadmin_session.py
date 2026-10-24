"""Opaque, site-scoped phpMyAdmin sessions and one-time iframe tickets."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.core.cache import KEY_PREFIX, RedisCacheBackend, cache
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import build_tenant_context
from systutor.kernel.tenants.service import validate_user_branch_scope

from .models import HostingSite

COOKIE = "spanel_pma"
MESSAGE = "La sesión de phpMyAdmin expiró; volvé a abrir el modal"
_ticket_lock = threading.Lock()


def proxy_base(site_id: str) -> str:
    return f"/api/v1/plugins/hosting/sites/{site_id}/phpmyadmin"


def session_key(identifier: str) -> str:
    return "hosting:pma:session:" + identifier


def ticket_key(identifier: str) -> str:
    return "hosting:pma:ticket:" + identifier


@dataclass
class PmaSession:
    id: str
    user_id: str
    tenant_id: str
    site_id: str
    cookies: dict[str, str]
    expires_at: float

    def save(self) -> None:
        remaining = int(self.expires_at - time.time())
        if remaining > 0:
            cache().set(session_key(self.id), json.dumps(self.__dict__), remaining)


def create_session(
    user_id: str, tenant_id: str, site_id: str, cookies: dict[str, str], ttl: int
) -> tuple[PmaSession, str]:
    record = PmaSession(
        secrets.token_urlsafe(32), user_id, tenant_id, site_id, cookies, time.time() + ttl
    )
    record.save()
    ticket = secrets.token_urlsafe(32)
    cache().set(ticket_key(ticket), record.id, 30)
    return record, ticket


def load_session(identifier: str | None) -> PmaSession | None:
    if not identifier:
        return None
    raw = cache().get(session_key(identifier))
    if raw is None:
        return None
    try:
        session = PmaSession(**json.loads(raw))
        if session.id == identifier and session.expires_at > time.time():
            return session
    except (TypeError, ValueError, KeyError):
        pass
    cache().delete(session_key(identifier))
    return None


def exchange_ticket(ticket: str, site_id: str) -> PmaSession | None:
    backend = cache()
    if isinstance(backend, RedisCacheBackend):
        identifier = backend._client.getdel(KEY_PREFIX + ticket_key(ticket))
    else:
        with _ticket_lock:
            identifier = backend.get(ticket_key(ticket))
            backend.delete(ticket_key(ticket))
    record = load_session(identifier)
    return record if record and record.site_id == site_id else None


def invalidate(session: PmaSession) -> None:
    cache().delete(session_key(session.id))


def secure_request(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    )


def get_phpmyadmin_session(
    request: Request, site_id: str, db: Session = Depends(get_db_session)
) -> PmaSession:
    ticket = request.query_params.get("ticket")
    exchange = bool(
        ticket and request.method == "GET" and request.url.path.rstrip("/") == proxy_base(site_id)
    )
    session = (
        exchange_ticket(ticket, site_id)
        if exchange and ticket
        else load_session(request.cookies.get(COOKIE))
    )
    if session is None or session.site_id != site_id:
        raise HTTPException(401, MESSAGE)
    user = db.get(User, session.user_id)
    if (
        user is None
        or not user.is_active
        or user.tenant_id != session.tenant_id
        or not validate_user_branch_scope(db, user)
    ):
        invalidate(session)
        raise HTTPException(401, MESSAGE)
    context = build_tenant_context(db, user)
    if context.current_tenant_id != session.tenant_id:
        invalidate(session)
        raise HTTPException(401, MESSAGE)
    if not context.has_permission("hosting.sites.manage"):
        raise HTTPException(403, "Permission denied")
    if (
        db.scalar(
            select(HostingSite.id).where(
                HostingSite.id == site_id, HostingSite.tenant_id == session.tenant_id
            )
        )
        is None
    ):
        raise HTTPException(404, "Site not found")
    request.state.current_user_id = user.id
    request.state.current_tenant_id = session.tenant_id
    request.state.pma_ticket_exchange = exchange
    return session
