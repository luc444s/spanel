from __future__ import annotations

from typing import Generator
from unittest.mock import MagicMock

import pytest

from plugins.mail.backend.provider import MailProvider


class MockMailProvider(MailProvider):
    def __init__(self) -> None:
        self._accounts: list[str] = []

    def list_accounts(self) -> list[str]:
        return self._accounts.copy()

    def create_account(self, email: str, password: str) -> None:
        if email in self._accounts:
            raise Exception("Account already exists")
        self._accounts.append(email)

    def change_password(self, email: str, password: str) -> None:
        if email not in self._accounts:
            raise Exception("Account not found")


@pytest.fixture
def mock_provider() -> Generator[MockMailProvider, None, None]:
    provider = MockMailProvider()
    yield provider
