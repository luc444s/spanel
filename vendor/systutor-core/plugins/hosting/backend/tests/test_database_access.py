"""The database binding, the credential extraction and the phpMyAdmin URL.

Pure functions only: these tests never touch Docker.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.hosting.backend import service as service_module
from plugins.hosting.backend.service import (
    HostingService,
    database_credentials,
    phpmyadmin_url,
)
from plugins.hosting.backend.settings import HostingSettings
from systutor.core.errors import AppError


def _info(**env: str) -> dict:
    return {"Config": {"Env": [f"{key}={value}" for key, value in env.items()]}}


WP_CONFIG = (
    "<?php\n"
    "define( 'DB_NAME', 'wp_config' );\n"
    "define( 'DB_USER', 'wp_config' );\n"
    "define( 'DB_PASSWORD', 'desde-wp-config' );\n"
    "define( 'DB_HOST', 'mariadb:3306' );\n"
)


def test_credentials_come_from_the_site_environment() -> None:
    info = _info(
        WORDPRESS_DB_HOST="spanel-demowp-db:3306",
        WORDPRESS_DB_NAME="wp_0123456789",
        WORDPRESS_DB_USER="wp_abcdef0123",
        WORDPRESS_DB_PASSWORD="secreto",
    )
    assert database_credentials(info) == (
        "spanel-demowp-db",
        "wp_0123456789",
        "wp_abcdef0123",
        "secreto",
    )


def test_a_shared_database_container_is_accepted() -> None:
    """A site may share a MariaDB with other sites; the user is still its own."""
    info = _info(
        WORDPRESS_DB_HOST="spanel-lab-mariadb",
        WORDPRESS_DB_NAME="wp_demowp",
        WORDPRESS_DB_USER="wp_demowp",
        WORDPRESS_DB_PASSWORD="secreto",
    )
    assert database_credentials(info)[0] == "spanel-lab-mariadb"


def test_a_missing_variable_returns_no_credential() -> None:
    info = _info(
        WORDPRESS_DB_HOST="spanel-demowp-db",
        WORDPRESS_DB_NAME="wp_0123456789",
        WORDPRESS_DB_USER="wp_abcdef0123",
    )
    with pytest.raises(AppError) as error:
        database_credentials(info)
    assert error.value.status_code == 409
    assert error.value.code == "hosting_database_unavailable"
    assert "secreto" not in error.value.message


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "::1", "http://x", "mysql://db", "db.example/../x"],
)
def test_a_host_that_is_not_a_container_is_rejected(host: str) -> None:
    info = _info(
        WORDPRESS_DB_HOST=host,
        WORDPRESS_DB_NAME="wp_0123456789",
        WORDPRESS_DB_USER="wp_abcdef0123",
        WORDPRESS_DB_PASSWORD="secreto",
    )
    with pytest.raises(AppError) as error:
        database_credentials(info)
    assert error.value.status_code == 409
    assert error.value.code == "hosting_database_unavailable"
    assert "secreto" not in error.value.message


def test_no_phpmyadmin_url_means_no_link() -> None:
    assert phpmyadmin_url("", "spanel-demowp-db") is None
    assert phpmyadmin_url("   ", "spanel-demowp-db") is None


def test_a_non_http_url_is_not_a_link() -> None:
    assert phpmyadmin_url("ftp://db.example", "spanel-demowp-db") is None
    assert phpmyadmin_url("db.example", "spanel-demowp-db") is None


def test_a_fixed_server_deployment_uses_the_url_as_is() -> None:
    """`PMA_HOST` is fixed in the container, so the panel adds nothing."""
    assert phpmyadmin_url("http://127.0.0.1:8081/", "spanel-lab-mariadb") == (
        "http://127.0.0.1:8081/"
    )


def test_the_server_placeholder_is_replaced() -> None:
    assert (
        phpmyadmin_url("https://pma.example/?server={server}", "spanel-lab-mariadb")
        == "https://pma.example/?server=spanel-lab-mariadb"
    )
    assert (
        phpmyadmin_url("https://{server}.db.example/", "spanel-demowp-db")
        == "https://spanel-demowp-db.db.example/"
    )


def test_the_server_is_url_encoded() -> None:
    assert phpmyadmin_url("https://pma.example/{server}", "a b") == (
        "https://pma.example/a%20b"
    )


def test_credentials_come_from_wp_config_when_the_environment_is_silent() -> None:
    assert database_credentials({"Config": {"Env": []}}, wp_config=WP_CONFIG) == (
        "mariadb",
        "wp_config",
        "wp_config",
        "desde-wp-config",
    )


def test_the_environment_wins_over_wp_config() -> None:
    info = _info(
        WORDPRESS_DB_HOST="spanel-lab-mariadb:3307",
        WORDPRESS_DB_NAME="wp_entorno",
        WORDPRESS_DB_USER="wp_entorno",
        WORDPRESS_DB_PASSWORD="desde-entorno",
    )
    assert database_credentials(info, wp_config=WP_CONFIG) == (
        "spanel-lab-mariadb",
        "wp_entorno",
        "wp_entorno",
        "desde-entorno",
    )


@pytest.mark.parametrize("host", ["mariadb:3306", "spanel-lab-mariadb", "db.example.com"])
def test_wp_config_accepts_an_external_database_container(host: str) -> None:
    config = WP_CONFIG.replace("mariadb:3306", host)
    assert database_credentials({"Config": {"Env": []}}, wp_config=config)[0] == (
        host.split(":", 1)[0]
    )


def test_missing_in_environment_and_wp_config_returns_no_credential() -> None:
    incomplete = (
        "<?php\n"
        "define( 'DB_NAME', 'wp_config' );\n"
        "define( 'DB_USER', 'wp_config' );\n"
        "define( 'DB_PASSWORD', 'no-debe-salir' );\n"
    )
    with pytest.raises(AppError) as error:
        database_credentials({"Config": {"Env": []}}, wp_config=incomplete)
    assert error.value.status_code == 409
    assert error.value.code == "hosting_database_unavailable"
    assert "no-debe-salir" not in error.value.message


def test_the_credentials_consumed_by_users_and_reset_use_wp_config(monkeypatch) -> None:
    service = HostingService(
        db=None,  # type: ignore[arg-type]
        settings=HostingSettings(docker_transport="local"),
    )
    monkeypatch.setattr(service_module, "read_wp_config", lambda *args, **kwargs: WP_CONFIG)

    captured: dict[str, str] = {}

    def fake_prefix(
        env: dict[str, str],
        db: str,
        user: str,
        password: str,
        database: str,
    ) -> str:
        captured.update(
            db=db,
            user=user,
            password=password,
            database=database,
        )
        return "wp_"

    monkeypatch.setattr(service_module, "prefijo_de_tablas", fake_prefix)
    site = SimpleNamespace(container_name="spanel-demo-wp")

    credentials, db_container, prefix = service._credentials(
        site,  # type: ignore[arg-type]
        {"Config": {"Env": []}},
    )

    assert db_container == "mariadb"
    assert prefix == "wp_"
    assert captured == {
        "db": "mariadb",
        "user": "wp_config",
        "password": "desde-wp-config",
        "database": "wp_config",
    }
    assert credentials["_contenido"] == (
        "WORDPRESS_DB_HOST=mariadb:3306\n"
        "WORDPRESS_DB_USER=wp_config\n"
        "WORDPRESS_DB_PASSWORD=desde-wp-config\n"
        "WORDPRESS_DB_NAME=wp_config\n"
    )
