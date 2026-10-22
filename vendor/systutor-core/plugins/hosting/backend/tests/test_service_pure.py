"""Pure parsing and formatting. No Docker and no database fixtures."""

from __future__ import annotations

import pytest

from plugins.hosting.backend.inspection import candidato_de, database_container
from plugins.hosting.backend.service import (
    estado_de_usuario,
    estado_del_contenedor,
    generar_password,
    rol_mostrado,
)
from plugins.hosting.backend.transport import Fallo


def test_rol_vacio_cuando_no_hay_capacidades() -> None:
    assert rol_mostrado("") == ""


def test_rol_simple() -> None:
    assert rol_mostrado('a:1:{s:13:"administrator";b:1;}') == "administrator"


def test_rol_multiples_activos() -> None:
    serializado = 'a:2:{s:6:"editor";b:1;s:11:"contributor";b:1;}'
    assert rol_mostrado(serializado) == "editor,contributor"


def test_rol_deshabilitado_no_se_muestra() -> None:
    assert rol_mostrado('a:1:{s:13:"administrator";b:0;}') == ""


def test_estado_de_usuario_es_entero() -> None:
    assert estado_de_usuario("0") == 0
    assert estado_de_usuario("1") == 1
    assert estado_de_usuario("no-es-un-numero") == 0


def test_estado_del_contenedor() -> None:
    assert estado_del_contenedor({"State": {"Status": "running"}}) == "running"
    assert estado_del_contenedor({}) == "unknown"


def test_password_generada_tiene_largo_util() -> None:
    password = generar_password()
    assert len(password) >= 12
    assert password == password.strip()


def test_database_container_desde_wordpress_db_host() -> None:
    info = {"Config": {"Env": ["WORDPRESS_DB_HOST=spanel-demowp-db:3306"]}}
    assert database_container(info) == "spanel-demowp-db"


def test_database_container_rechaza_localhost() -> None:
    info = {"Config": {"Env": ["WORDPRESS_DB_HOST=localhost:3306"]}}
    with pytest.raises(Fallo, match="no apunta a un contenedor"):
        database_container(info)


def test_database_container_exige_la_variable() -> None:
    with pytest.raises(Fallo, match="WORDPRESS_DB_HOST"):
        database_container({"Config": {"Env": []}})


def test_managed_false_es_detectado_desde_inspect() -> None:
    info = {
        "Config": {
            "Image": "wordpress:php8.3-apache",
            "Labels": {"spanel.managed": "false"},
            "Env": [],
        },
        "Mounts": [],
    }
    candidato = candidato_de(info)
    assert candidato.es_sitio is True
    assert candidato.gestionable is False


def test_wordpress_por_montaje() -> None:
    info = {
        "Config": {"Image": "miempresa/wp-custom", "Labels": {}, "Env": []},
        "Mounts": [{"Destination": "/var/www/html"}],
    }
    assert candidato_de(info).es_sitio is True
