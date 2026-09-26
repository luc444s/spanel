"""Tests for the read-through account cache in MailService.

These fakes are intentionally local to this module: the conftest MockMailProvider
raises bare Exception and does not count calls, which is not enough to falsify the
central claim of A.SPEC 0001 (two reads => one provider call).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from plugins.mail.backend.provider import MailProvider
from plugins.mail.backend.service import ACCOUNTS_CACHE_KEY, MailService
from plugins.mail.backend.settings import MailSettings
from systutor.core.cache import CacheBackend
from systutor.core.errors import AppError, NotFoundError

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class FakeSession:
    """Minimal stand-in for the SQLAlchemy session: only `get(Tenant, id)` is used."""

    def __init__(self, domains: dict[str, str]) -> None:
        self._domains = domains

    def get(self, model: Any, tenant_id: str) -> Any:
        if tenant_id not in self._domains:
            return None
        return SimpleNamespace(domain=self._domains[tenant_id])


class CountingMailProvider(MailProvider):
    """Real MailProvider subclass that counts calls and raises real AppErrors."""

    def __init__(self, accounts: list[str] | None = None) -> None:
        self.accounts: list[str] = list(accounts or [])
        self.list_calls = 0
        self.create_calls = 0
        self.password_calls = 0
        self.list_error: AppError | None = None

    def list_accounts(self) -> list[str]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return list(self.accounts)

    def create_account(self, email: str, password: str) -> None:
        self.create_calls += 1
        if email in self.accounts:
            raise AppError(
                f"Account already exists: {email}", status_code=409, code="conflict"
            )
        self.accounts.append(email)

    def change_password(self, email: str, password: str) -> None:
        self.password_calls += 1
        if email not in self.accounts:
            raise NotFoundError(f"Account not found: {email}")


class FakeCache(CacheBackend):
    """In-memory CacheBackend with call counters and injectable failures."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets = 0
        self.sets = 0
        self.deletes = 0
        self.last_ttl: int | None = None
        self.fail_on: str | None = None

    def get(self, key: str) -> str | None:
        self.gets += 1
        if self.fail_on == "get":
            raise RuntimeError("cache backend is down")
        return self.store.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self.sets += 1
        if self.fail_on == "set":
            raise RuntimeError("cache backend is down")
        self.last_ttl = ttl
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.deletes += 1
        if self.fail_on == "delete":
            raise RuntimeError("cache backend is down")
        self.store.pop(key, None)

    def clear(self) -> None:
        self.store.clear()

    def close(self) -> None:
        self.store.clear()


def make_service(
    provider: MailProvider | None = None,
    cache: FakeCache | None = None,
    enabled: bool = True,
    ttl: int = 60,
    domains: dict[str, str] | None = None,
) -> MailService:
    if domains is None:
        domains = {TENANT_A: "acme.com", TENANT_B: "globex.com"}
    return MailService(
        provider=provider or CountingMailProvider(),
        db=FakeSession(domains),  # type: ignore[arg-type]
        cache_backend=cache if cache is not None else FakeCache(),
        settings=MailSettings(mail_accounts_cache_enabled=enabled, mail_accounts_cache_ttl=ttl),
    )


# --- I1: response contract unchanged -----------------------------------------


def test_response_contract_unchanged():
    provider = CountingMailProvider(["a@acme.com", "b@globex.com"])
    service = make_service(provider)

    tenant_response = service.list_accounts(TENANT_A)
    superadmin_response = service.list_accounts(TENANT_A, can_read_all=True)

    for response in (tenant_response, superadmin_response):
        payload = response.model_dump()
        assert set(payload) == {"domain", "accounts"}
        assert isinstance(payload["domain"], str)
        assert isinstance(payload["accounts"], list)
        assert all(isinstance(item, str) for item in payload["accounts"])

    assert tenant_response.domain == "acme.com"
    assert tenant_response.accounts == ["a@acme.com"]
    assert superadmin_response.domain == "all"
    assert superadmin_response.accounts == ["a@acme.com", "b@globex.com"]


# --- I2: tenant isolation ----------------------------------------------------


def test_list_accounts_filters_by_tenant_domain():
    provider = CountingMailProvider(["a@acme.com", "b@globex.com", "c@acme.com"])
    service = make_service(provider, enabled=False)

    assert service.list_accounts(TENANT_A).accounts == ["a@acme.com", "c@acme.com"]
    assert service.list_accounts(TENANT_B).accounts == ["b@globex.com"]


def test_cache_hit_preserves_tenant_isolation():
    provider = CountingMailProvider(["a@acme.com", "b@globex.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    first = service.list_accounts(TENANT_A)
    # The cache now holds the RAW list, so a second tenant still gets its own subset.
    assert json.loads(cache.store[ACCOUNTS_CACHE_KEY]) == ["a@acme.com", "b@globex.com"]

    second_a = service.list_accounts(TENANT_A)
    second_b = service.list_accounts(TENANT_B)
    second_super = service.list_accounts(TENANT_A, can_read_all=True)

    assert provider.list_calls == 1
    assert second_a.accounts == first.accounts == ["a@acme.com"]
    assert second_b.accounts == ["b@globex.com"]
    assert second_super.accounts == ["a@acme.com", "b@globex.com"]


# --- the new falsifiable truth ----------------------------------------------


def test_list_accounts_populates_cache_on_miss():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    service.list_accounts(TENANT_A)

    assert cache.sets == 1
    assert cache.last_ttl == 60
    assert json.loads(cache.store[ACCOUNTS_CACHE_KEY]) == ["a@acme.com"]


def test_second_list_does_not_call_provider():
    provider = CountingMailProvider(["a@acme.com"])
    service = make_service(provider)

    first = service.list_accounts(TENANT_A)
    second = service.list_accounts(TENANT_A)

    assert provider.list_calls == 1
    assert first == second


def test_single_cache_key_shared_across_tenants():
    provider = CountingMailProvider(["a@acme.com", "b@globex.com"])
    cache = FakeCache()
    service_a = make_service(provider, cache=cache)
    service_b = make_service(provider, cache=cache)

    service_a.list_accounts(TENANT_A)
    result_b = service_b.list_accounts(TENANT_B)

    assert list(cache.store) == [ACCOUNTS_CACHE_KEY]
    assert provider.list_calls == 1
    assert result_b.accounts == ["b@globex.com"]


# --- I7 / writes -------------------------------------------------------------


def test_cache_hit_does_not_short_circuit_writes():
    provider = CountingMailProvider(["a@acme.com"])
    service = make_service(provider)

    service.list_accounts(TENANT_A)
    service.create_account(TENANT_A, "user-1", "nuevo", "password123")

    assert provider.create_calls == 1
    assert "nuevo@acme.com" in provider.accounts


def test_create_account_still_calls_provider():
    provider = CountingMailProvider()
    service = make_service(provider)

    service.create_account(TENANT_A, "user-1", "nuevo", "password123")

    assert provider.create_calls == 1
    assert provider.accounts == ["nuevo@acme.com"]


def test_create_account_invalidates_cache():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    service.list_accounts(TENANT_A)
    assert cache.store  # warm
    service.create_account(TENANT_A, "user-1", "nuevo", "password123")

    assert cache.deletes == 1
    assert cache.store == {}

    after = service.list_accounts(TENANT_A)
    assert provider.list_calls == 2
    assert after.accounts == ["a@acme.com", "nuevo@acme.com"]


# --- I6: change_password does not invalidate ---------------------------------


def test_change_password_does_not_invalidate_cache():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    service.list_accounts(TENANT_A)
    service.change_password(TENANT_A, "user-1", "a@acme.com", "newpassword123")

    assert provider.password_calls == 1
    assert cache.deletes == 0
    assert ACCOUNTS_CACHE_KEY in cache.store

    service.list_accounts(TENANT_A)
    assert provider.list_calls == 1


# --- change_password domain scope matches what list_accounts exposes ---------


def test_change_password_other_domain_is_403_without_manage_all():
    provider = CountingMailProvider(["a@acme.com", "t2@other.com"])
    service = make_service(provider)

    with pytest.raises(AppError) as excinfo:
        service.change_password(TENANT_A, "user-1", "t2@other.com", "newpassword123")

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "domain_mismatch"
    assert provider.password_calls == 0


def test_change_password_other_domain_allowed_with_manage_all():
    provider = CountingMailProvider(["a@acme.com", "t2@other.com"])
    service = make_service(provider)

    result = service.change_password(
        TENANT_A, "user-1", "t2@other.com", "newpassword123", can_manage_all=True
    )

    assert result.email == "t2@other.com"
    assert provider.password_calls == 1


def test_change_password_still_validates_email_format_with_manage_all():
    provider = CountingMailProvider(["a@acme.com"])
    service = make_service(provider)

    with pytest.raises(AppError) as excinfo:
        service.change_password(
            TENANT_A, "user-1", "not-an-email", "newpassword123", can_manage_all=True
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "invalid_email"
    assert provider.password_calls == 0


# --- I4: conflicts are not swallowed ----------------------------------------


def test_create_account_conflict_still_409():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    service.list_accounts(TENANT_A)

    with pytest.raises(AppError) as excinfo:
        service.create_account(TENANT_A, "user-1", "a", "password123")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "conflict"
    # The failed write must not have dropped the cache entry.
    assert cache.deletes == 0


# --- I3: error mapping preserved --------------------------------------------


def test_provider_error_still_maps_to_503():
    provider = CountingMailProvider()
    provider.list_error = AppError("dms down", status_code=500, code="dms_error")
    cache = FakeCache()
    service = make_service(provider, cache=cache)

    with pytest.raises(AppError) as excinfo:
        service.list_accounts(TENANT_A)

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "service_unavailable"
    assert cache.store == {}


def test_provider_error_still_maps_to_503_for_superadmin():
    provider = CountingMailProvider()
    provider.list_error = AppError("dms down", status_code=500, code="dms_error")
    service = make_service(provider)

    with pytest.raises(AppError) as excinfo:
        service.list_accounts(TENANT_A, can_read_all=True)

    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "service_unavailable"


# --- I5: cache failures degrade performance, never availability --------------


def test_cache_read_failure_falls_back_to_provider():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    cache.fail_on = "get"
    service = make_service(provider, cache=cache)

    first = service.list_accounts(TENANT_A)
    second = service.list_accounts(TENANT_A)

    assert first.accounts == ["a@acme.com"]
    assert second.accounts == ["a@acme.com"]
    assert provider.list_calls == 2


def test_cache_write_failure_does_not_break_request():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    cache.fail_on = "set"
    service = make_service(provider, cache=cache)

    result = service.list_accounts(TENANT_A)

    assert result.accounts == ["a@acme.com"]
    assert cache.store == {}


def test_cache_delete_failure_does_not_break_create():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    cache.fail_on = "delete"
    service = make_service(provider, cache=cache)

    result = service.create_account(TENANT_A, "user-1", "nuevo", "password123")

    assert result.email == "nuevo@acme.com"
    assert provider.accounts == ["a@acme.com", "nuevo@acme.com"]


def test_corrupt_cache_value_treated_as_miss():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    cache.store[ACCOUNTS_CACHE_KEY] = "not-json{{"
    service = make_service(provider, cache=cache)

    result = service.list_accounts(TENANT_A)

    assert provider.list_calls == 1
    assert result.accounts == ["a@acme.com"]
    assert json.loads(cache.store[ACCOUNTS_CACHE_KEY]) == ["a@acme.com"]


def test_cache_value_with_wrong_shape_treated_as_miss():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    cache.store[ACCOUNTS_CACHE_KEY] = json.dumps({"accounts": ["evil@evil.com"]})
    service = make_service(provider, cache=cache)

    result = service.list_accounts(TENANT_A)

    assert provider.list_calls == 1
    assert result.accounts == ["a@acme.com"]


# --- I9: kill switch ---------------------------------------------------------


def test_cache_disabled_by_setting():
    provider = CountingMailProvider(["a@acme.com"])
    cache = FakeCache()
    service = make_service(provider, cache=cache, enabled=False)

    first = service.list_accounts(TENANT_A)
    second = service.list_accounts(TENANT_A)

    assert cache.gets == 0
    assert cache.sets == 0
    assert cache.store == {}
    assert provider.list_calls == 2
    assert first.accounts == second.accounts == ["a@acme.com"]

    service.create_account(TENANT_A, "user-1", "nuevo", "password123")
    assert cache.deletes == 0
    assert cache.store == {}
