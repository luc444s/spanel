"""The `wp-config.php` parser and the read-only command, without Docker.

The parser is pure on purpose: it can be proven against text before it is
trusted against a real site. No test here executes PHP or writes a file.
"""

from __future__ import annotations

import subprocess

from plugins.hosting.backend import wpconfig
from plugins.hosting.backend.wpconfig import (
    credentials_from_wp_config,
    parse_wp_config,
    read_wp_config,
)


def _config(
    *,
    host: str = "mariadb:3306",
    database: str = "wp_demo",
    user: str = "wp_demo",
    password: str = "secreto",
) -> str:
    return (
        "<?php\n"
        f"define( 'DB_NAME', '{database}' );\n"
        f"define( 'DB_USER', '{user}' );\n"
        f"define( 'DB_PASSWORD', '{password}' );\n"
        f"define( 'DB_HOST', '{host}' );\n"
        "define( 'DB_CHARSET', 'utf8mb4' );\n"
    )


def test_single_quoted_defines_are_extracted() -> None:
    assert parse_wp_config(_config()) == {
        "DB_NAME": "wp_demo",
        "DB_USER": "wp_demo",
        "DB_PASSWORD": "secreto",
        "DB_HOST": "mariadb:3306",
    }


def test_double_quoted_defines_are_extracted() -> None:
    text = (
        "<?php\n"
        'define("DB_NAME", "wp_0123456789");\n'
        'define("DB_USER", "wp_abcdef0123");\n'
        'define("DB_PASSWORD", "secreto");\n'
        'define("DB_HOST", "spanel-lab-mariadb");\n'
    )
    assert credentials_from_wp_config(text) == (
        "spanel-lab-mariadb",
        "wp_0123456789",
        "wp_abcdef0123",
        "secreto",
    )


def test_comments_and_commented_defines_are_ignored() -> None:
    text = (
        "<?php\n"
        "// define('DB_NAME', 'comentado');\n"
        "# define('DB_USER', 'tambien_comentado');\n"
        "/*\n"
        "define('DB_PASSWORD', 'bloque');\n"
        "*/\n"
        f"{_config()}"
    )
    assert credentials_from_wp_config(text) == (
        "mariadb:3306",
        "wp_demo",
        "wp_demo",
        "secreto",
    )


def test_comment_markers_inside_a_password_are_data() -> None:
    password = "pa#ss//word /* no es comentario */"
    values = credentials_from_wp_config(_config(password=password))
    assert values is not None
    assert values[3] == password


def test_escaped_quotes_and_backslashes_survive() -> None:
    password = r"pa\'ss\\word"
    values = credentials_from_wp_config(_config(password=password))
    assert values is not None
    assert values[3] == "pa'ss\\word"


def test_the_first_definition_wins() -> None:
    text = (
        "<?php\n"
        "define('DB_NAME', 'primera');\n"
        "define('DB_NAME', 'segunda');\n"
        "define('DB_USER', 'wp_demo');\n"
        "define('DB_PASSWORD', 'secreto');\n"
        "define('DB_HOST', 'mariadb');\n"
    )
    values = credentials_from_wp_config(text)
    assert values is not None
    assert values[1] == "primera"


def test_a_non_literal_value_is_not_guessed() -> None:
    text = (
        "<?php\n"
        "define('DB_NAME', 'wp_demo');\n"
        "define('DB_USER', 'wp_demo');\n"
        "define('DB_PASSWORD', getenv('DB_PASSWORD'));\n"
        "define('DB_HOST', 'mariadb');\n"
    )
    assert credentials_from_wp_config(text) is None


def test_one_missing_constant_makes_the_file_incomplete() -> None:
    text = (
        "<?php\n"
        "define('DB_NAME', 'wp_demo');\n"
        "define('DB_USER', 'wp_demo');\n"
        "define('DB_HOST', 'mariadb');\n"
    )
    assert credentials_from_wp_config(text) is None


def test_external_mariadb_and_custom_port_are_kept_for_the_caller() -> None:
    values = credentials_from_wp_config(_config(host="mariadb:3307"))
    assert values is not None
    assert values[0] == "mariadb:3307"


def test_read_wp_config_uses_cat_and_quotes_the_container(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(argv, *, env, timeout=30, input=None):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=_config(), stderr="")

    monkeypatch.setattr(wpconfig, "run", fake_run)
    text = read_wp_config(
        {"SPANEL_DOCKER_TRANSPORT": "local"},
        "spanel-demo-wp",
        timeout=5,
    )

    assert text == _config()
    command = captured["argv"][-1]
    assert command == "docker exec spanel-demo-wp cat /var/www/html/wp-config.php"
    assert "php" not in command.split()


def test_a_failed_read_returns_none_without_raising(monkeypatch) -> None:
    def fake_run(argv, *, env, timeout=30, input=None):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="define('DB_PASSWORD', 'no debe salir');\n",
            stderr="no such container",
        )

    monkeypatch.setattr(wpconfig, "run", fake_run)
    assert read_wp_config({"SPANEL_DOCKER_TRANSPORT": "local"}, "missing") is None
