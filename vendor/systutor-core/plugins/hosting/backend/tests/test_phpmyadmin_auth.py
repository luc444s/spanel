from __future__ import annotations

import json
import time
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

from plugins.hosting.backend import phpmyadmin_session as module
from plugins.hosting.backend.phpmyadmin_session import (
    create_session,
    exchange_ticket,
    get_phpmyadmin_session,
    invalidate,
    load_session,
    session_key,
)
from systutor.core.cache import cache


def test_session_is_opaque_expiring_and_ticket_is_one_time() -> None:
    record, ticket = create_session("user", "tenant", "site", {"pma": "opaque"}, 120)
    raw = cache().get(session_key(record.id))
    assert raw is not None
    assert json.loads(raw)["site_id"] == "site"
    assert "db-password" not in raw
    assert exchange_ticket(ticket, "other") is None
    assert exchange_ticket(ticket, "site") is None
    assert load_session(record.id) is not None
    record.expires_at = time.time() - 1
    record.save()
    invalidate(record)
    assert load_session(record.id) is None


def test_missing_cookie_is_unauthorized() -> None:
    request = Request(
        {"type": "http", "method": "GET", "path": "/proxy", "query_string": b"", "headers": []}
    )
    try:
        get_phpmyadmin_session(request, "site", None)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("missing cookie accepted")


def test_every_request_checks_user_permission_and_site(monkeypatch) -> None:
    record, _ = create_session("u", "t", "site-a", {"pma": "opaque"}, 120)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/proxy",
            "query_string": b"",
            "headers": [(b"cookie", f"spanel_pma={record.id}".encode())],
        }
    )
    user = SimpleNamespace(id="u", tenant_id="t", is_active=True)

    class DB:
        exists = True

        def get(self, *_):
            return user

        def scalar(self, *_):
            return "site-a" if self.exists else None

    db = DB()
    permitted = [True]
    monkeypatch.setattr(module, "validate_user_branch_scope", lambda *_: True)
    monkeypatch.setattr(
        module,
        "build_tenant_context",
        lambda *_: SimpleNamespace(current_tenant_id="t", has_permission=lambda _: permitted[0]),
    )
    assert get_phpmyadmin_session(request, "site-a", db).id == record.id  # type: ignore[arg-type]
    try:
        get_phpmyadmin_session(request, "site-b", db)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 401
    permitted[0] = False
    try:
        get_phpmyadmin_session(request, "site-a", db)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 403
    permitted[0] = True
    db.exists = False
    try:
        get_phpmyadmin_session(request, "site-a", db)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 404
    user.is_active = False
    try:
        get_phpmyadmin_session(request, "site-a", db)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 401
