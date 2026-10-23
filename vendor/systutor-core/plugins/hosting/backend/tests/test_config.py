"""Pure tests for the canonical config model and the raw adapter's argv builder.

No Docker and no database fixtures: the destructive recreate is a thin call,
and everything that decides WHAT to run is pure and tested here.
"""

from __future__ import annotations

import pytest

from plugins.hosting.backend.config import (
    ENV_WHITELIST,
    build_run_args,
    editable_env,
    env_of,
    env_path,
    parse_env,
    render_env,
    secret_names,
    site_dir,
    validate_env_updates,
)
from plugins.hosting.backend.settings import HostingSettings
from plugins.hosting.backend.transport import Fallo
from systutor.core.errors import AppError


def _inspect() -> dict:
    return {
        "Config": {
            "Image": "wordpress:latest",
            "Cmd": ["apache2-foreground"],
            "Labels": {
                "spanel.site": "demo",
                "spanel.tenant": "tenant-1",
                "traefik.http.routers.demo.rule": "Host(`demo.example.com`)",
            },
            "Env": [
                "WORDPRESS_DB_HOST=spanel-demo-db",
                "WORDPRESS_DB_USER=wp_abc",
                "WORDPRESS_DB_PASSWORD=supersecret",
                "WORDPRESS_DB_NAME=wp_abc",
                "MYSQL_PWD=supersecret",
                "PHP_MEMORY_LIMIT=256M",
            ],
        },
        "HostConfig": {
            "NetworkMode": "proxy",
            "RestartPolicy": {"Name": "unless-stopped"},
            "PortBindings": {},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "spanel-demo-wp",
                "Destination": "/var/www/html",
                "RW": True,
            }
        ],
    }


# --- whitelist -------------------------------------------------------------


def test_acepta_claves_de_la_whitelist() -> None:
    assert validate_env_updates({"PHP_MEMORY_LIMIT": "512M", "WP_DEBUG": "true"}) == {
        "PHP_MEMORY_LIMIT": "512M",
        "WP_DEBUG": "true",
    }


def test_rechaza_clave_fuera_de_la_whitelist() -> None:
    with pytest.raises(AppError, match="WORDPRESS_DB_HOST"):
        validate_env_updates({"WORDPRESS_DB_HOST": "otro-tenant-db"})


def test_rechaza_secreto_como_si_fuera_config() -> None:
    with pytest.raises(AppError):
        validate_env_updates({"WORDPRESS_DB_PASSWORD": "x"})


def test_rechaza_valor_invalido() -> None:
    with pytest.raises(AppError, match="no es valido"):
        validate_env_updates({"PHP_MEMORY_LIMIT": "mucho"})
    with pytest.raises(AppError):
        validate_env_updates({"WP_DEBUG": "quizas"})


def test_rechaza_saltos_de_linea() -> None:
    with pytest.raises(AppError, match="saltos de linea"):
        validate_env_updates({"TZ": "America/Bogota\nWORDPRESS_DB_HOST=x"})


def test_whitelist_no_incluye_claves_de_conexion() -> None:
    for key in ("WORDPRESS_DB_HOST", "WORDPRESS_DB_USER", "WORDPRESS_DB_PASSWORD", "MYSQL_PWD"):
        assert key not in ENV_WHITELIST


# --- env file --------------------------------------------------------------


def test_parse_env_ignora_comentarios_y_vacios() -> None:
    assert parse_env("# comentario\n\nA=1\nB=x=y\n") == {"A": "1", "B": "x=y"}


def test_render_env_es_estable_y_ordenado() -> None:
    text = render_env({"B": "2", "A": "1"})
    assert text == "A=1\nB=2\n"
    assert parse_env(text) == {"A": "1", "B": "2"}


def test_editable_y_secretos_salen_del_inspect() -> None:
    env = env_of(_inspect())
    assert editable_env(env) == {"PHP_MEMORY_LIMIT": "256M"}
    assert secret_names(env) == ("MYSQL_PWD", "WORDPRESS_DB_PASSWORD")


# --- stable path -----------------------------------------------------------


def test_site_dir_sanitiza_el_nombre() -> None:
    settings = HostingSettings(sites_dir="/opt/spanel/sites")
    assert site_dir(settings, "spanel-demo-wp") == "/opt/spanel/sites/spanel-demo-wp"
    assert site_dir(settings, "Spanel Demo/WP") == "/opt/spanel/sites/spanel-demo-wp"
    assert env_path(settings, "spanel-demo-wp").endswith("/spanel-demo-wp/wp.env")


# --- recreate argv ---------------------------------------------------------


def test_build_run_args_reutiliza_volumen_y_labels() -> None:
    args = build_run_args(
        _inspect(), name="spanel-demo-wp", env_file="/opt/spanel/sites/x/wp.env"
    )
    assert args[:3] == ["-d", "--name", "spanel-demo-wp"]
    assert "--network" in args and "proxy" in args
    assert "--label" in args and "spanel.tenant=tenant-1" in args
    assert "--label" in args and "traefik.http.routers.demo.rule=Host(`demo.example.com`)" in args
    assert "-v" in args and "spanel-demo-wp:/var/www/html" in args
    assert "--restart" in args and "unless-stopped" in args
    assert "--env-file" in args and "/opt/spanel/sites/x/wp.env" in args
    assert args[-2:] == ["wordpress:latest", "apache2-foreground"]


def test_build_run_args_marca_bind_de_solo_lectura() -> None:
    info = _inspect()
    info["Mounts"] = [
        {"Type": "bind", "Source": "/etc/localtime", "Destination": "/etc/localtime", "RW": False}
    ]
    args = build_run_args(info, name="spanel-demo-wp", env_file="/x/wp.env")
    assert "/etc/localtime:/etc/localtime:ro" in args


def test_build_run_args_no_inventa_imagen() -> None:
    info = _inspect()
    del info["Config"]["Image"]
    info["Image"] = ""
    with pytest.raises(Fallo, match="imagen"):
        build_run_args(info, name="spanel-demo-wp", env_file="/x/wp.env")
