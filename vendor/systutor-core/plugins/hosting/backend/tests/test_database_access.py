"""The database binding, the credential extraction and the phpMyAdmin URL.

Pure functions only: these tests never touch Docker.
"""

from __future__ import annotations

import pytest

from plugins.hosting.backend.service import database_credentials, phpmyadmin_url
from systutor.core.errors import AppError


def _info(**env: str) -> dict:
    return {"Config": {"Env": [f"{key}={value}" for key, value in env.items()]}}


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
