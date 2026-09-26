"""End-to-end test of the cached account list through the HTTP layer.

The router itself is out of the A.SPEC change surface; it is exercised as-is with
dependency overrides, which is also the proof that the caching lives in the service
layer and not in the entrypoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from plugins.mail.backend.router import _get_mail_service
from plugins.mail.backend.router import router as mail_router
from plugins.mail.backend.service import MailService
from plugins.mail.backend.settings import MailSettings
from plugins.mail.backend.tests.test_service import (
    TENANT_A,
    CountingMailProvider,
    FakeCache,
    FakeSession,
)
from systutor.api.deps import get_db_session
from systutor.core.errors import register_exception_handlers
from systutor.kernel.auth.dependencies import get_current_tenant_context, get_current_user
from systutor.kernel.tenants.context import TenantContext

MAIL_ACCOUNTS = ["a@acme.com", "b@acme.com", "c@globex.com"]


def build_client(service: MailService, tenant_id: str = TENANT_A) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mail_router, prefix="/api/v1/plugins/mail")

    user = SimpleNamespace(id="user-1", tenant_id=tenant_id, branch_id=None, is_superadmin=False)
    context = TenantContext(
        current_tenant_id=tenant_id,
        current_branch_id=None,
        current_user_id="user-1",
        current_permissions=("mail.accounts.read",),
        current_warehouse_ids=None,
        is_superadmin=False,
    )

    def fake_session() -> Any:
        return None

    app.dependency_overrides[get_db_session] = fake_session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_tenant_context] = lambda: context
    app.dependency_overrides[_get_mail_service] = lambda: service

    @app.middleware("http")
    async def set_request_state(request: Request, call_next: Any) -> Any:
        request.state.current_tenant_id = tenant_id
        request.state.is_superadmin = False
        return await call_next(request)

    return TestClient(app)


def build_service(provider: CountingMailProvider, cache: FakeCache) -> MailService:
    return MailService(
        provider=provider,
        db=FakeSession({TENANT_A: "acme.com"}),  # type: ignore[arg-type]
        cache_backend=cache,
        settings=MailSettings(mail_accounts_cache_enabled=True, mail_accounts_cache_ttl=60),
    )


def test_two_requests_one_provider_call():
    provider = CountingMailProvider(MAIL_ACCOUNTS)
    cache = FakeCache()
    client = build_client(build_service(provider, cache))

    first = client.get("/api/v1/plugins/mail/mail/accounts")
    second = client.get("/api/v1/plugins/mail/mail/accounts")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert provider.list_calls == 1


def test_response_contract_unchanged():
    provider = CountingMailProvider(MAIL_ACCOUNTS)
    client = build_client(build_service(provider, FakeCache()))

    response = client.get("/api/v1/plugins/mail/mail/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"domain", "accounts"}
    assert payload["domain"] == "acme.com"
    assert payload["accounts"] == ["a@acme.com", "b@acme.com"]


def test_two_requests_one_provider_call_survives_cache_outage():
    provider = CountingMailProvider(MAIL_ACCOUNTS)
    cache = FakeCache()
    cache.fail_on = "get"
    client = build_client(build_service(provider, cache))

    first = client.get("/api/v1/plugins/mail/mail/accounts")
    second = client.get("/api/v1/plugins/mail/mail/accounts")

    assert first.status_code == 200
    assert second.status_code == 200
    assert provider.list_calls == 2
