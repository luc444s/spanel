"""A.SPEC 0033 — settings and the phpMyAdmin arbitrary-server configuration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plugins.hosting.backend import settings as settings_module
from plugins.hosting.backend.settings import HostingSettings, register_hosting_settings

REPO_ROOT = Path(__file__).resolve().parents[6]
ARBITRARY_CONFIG = REPO_ROOT / "scripts" / "phpmyadmin_arbitrary.php"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    # The checkout's .env files must not decide what a settings test reads.
    monkeypatch.setattr(settings_module, "_load_env_files", lambda: None)
    monkeypatch.delenv("SPANEL_PHPMYADMIN_ARBITRARY", raising=False)
    monkeypatch.delenv("SPANEL_PHPMYADMIN_DB_NETWORK", raising=False)


def test_defaults_keep_the_fixed_server_behavior() -> None:
    assert HostingSettings().phpmyadmin_arbitrary is False
    assert HostingSettings().phpmyadmin_db_network == ""
    settings = register_hosting_settings()
    assert settings.phpmyadmin_arbitrary is False
    assert settings.phpmyadmin_db_network == ""


def test_both_variables_are_read(monkeypatch) -> None:
    monkeypatch.setenv("SPANEL_PHPMYADMIN_ARBITRARY", "true")
    monkeypatch.setenv("SPANEL_PHPMYADMIN_DB_NETWORK", "spanel-db")
    settings = register_hosting_settings()
    assert settings.phpmyadmin_arbitrary is True
    assert settings.phpmyadmin_db_network == "spanel-db"


@pytest.mark.parametrize("value", ["0", "no", "off", "false", "NO"])
def test_false_values_are_false(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SPANEL_PHPMYADMIN_ARBITRARY", value)
    assert register_hosting_settings().phpmyadmin_arbitrary is False


@pytest.mark.parametrize("value", ["1", "yes", "on", "true"])
def test_true_values_are_true(monkeypatch, value: str) -> None:
    monkeypatch.setenv("SPANEL_PHPMYADMIN_ARBITRARY", value)
    assert register_hosting_settings().phpmyadmin_arbitrary is True


def test_the_phpmyadmin_config_restricts_servers_to_database_containers() -> None:
    text = ARBITRARY_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"ArbitraryServerRegexp'\]\s*=\s*'([^']+)'", text)
    assert match is not None
    regex = re.compile(match.group(1).strip("/"))
    for accepted in ("mariadb", "spanel-lab-db", "legalattorney-clean-db", "spanel-demo-db"):
        assert regex.fullmatch(accepted), accepted
    for rejected in (
        "localhost",
        "127.0.0.1",
        "mysql://db",
        "evil.example.com",
        "spanel-demo-wp",
        "spanel-demo-db:3306",
    ):
        assert not regex.fullmatch(rejected), rejected
