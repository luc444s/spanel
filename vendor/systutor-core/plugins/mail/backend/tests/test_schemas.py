from __future__ import annotations

import pytest

from plugins.mail.backend.schemas import CreateAccountRequest, ChangePasswordRequest
from plugins.mail.backend.service import MailService


def test_create_account_request_valid():
    req = CreateAccountRequest(username="ventas", password="password123")
    assert req.username == "ventas"
    assert req.password == "password123"


def test_create_account_request_invalid_username():
    with pytest.raises(ValueError):
        CreateAccountRequest(username="ventas@", password="password123")


def test_create_account_request_invalid_password():
    with pytest.raises(ValueError):
        CreateAccountRequest(username="ventas", password="short")


def test_change_password_request_valid():
    req = ChangePasswordRequest(password="newpassword123")
    assert req.password == "newpassword123"


def test_change_password_request_invalid():
    with pytest.raises(ValueError):
        ChangePasswordRequest(password="short")
