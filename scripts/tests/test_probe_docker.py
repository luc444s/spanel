"""Los 12 casos raros de deteccion de WordPress, escritos como tests.

A.SPEC 0007. Ninguno de estos tests necesita Docker, ni red, ni credenciales,
ni el `.env`: todos los fixtures son diccionarios literales con la forma que
devuelven `docker ps --format '{{json .}}'` y `docker inspect`. La funcion bajo
prueba es pura, y por eso se puede testear en serio.

Lo que estos tests FALSAN, uno por uno:

    1  wordpress:php8.3-apache corriendo   el caso obvio deja de funcionar
    2  wordpress:cli                      la herramienta sale como sitio
    3  miempresa/wp-custom + montaje       las imagenes propias no se detectan
    4  mariadb:11                         salen 2 filas por sitio
    5  nginx:alpine + montaje             el sitio servido por nginx desaparece
    6  bitnami/wordpress:6                el prefijo que no esta en la posicion 0
    7  etiqueta spanel.site               lo nuestro no se reconoce como nuestro
    8  etiqueta spanel.managed=false      se puede tocar un sitio ajeno
    9  etiqueta spanel.managed=true       lo nuestro no se puede tocar
    10 State=exited                       un WP parado desaparece de la lista
    11 {}                                 un dato raro tumba la pantalla con 500
    12 []                                 una maquina sin contenedores revienta
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# El modulo bajo prueba vive un nivel arriba y no es un paquete, asi que lo
# sumamos al path. Esto no lee ningun .env ni abre ninguna conexion.
RAIZ_DE_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_DE_SCRIPTS))

import probe_docker  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures. Son filas literales de `docker ps --format '{{json .}}'`: por eso
# el campo `Mounts` viene truncado con puntos suspensivos, igual que en la
# vida real, y por eso los montajes de verdad hay que buscarlos en el inspect.
# ---------------------------------------------------------------------------


def fila_de_ps(**campos: str) -> dict[str, str]:
    """Una fila de la lista de `docker ps`, tal cual la devuelve Docker."""
    fila = {
        "Names": "wp-acme",
        "Image": "wordpress:php8.3-apache",
        "State": "running",
        "Status": "Up 3 days",
        "Labels": "",
        "Mounts": "wp_acme",  # texto decorativo, truncado. NO se puede filtrar por esto.
    }
    fila.update(campos)
    return fila


def inspect_con_montaje(destino: str = "/var/www/html") -> dict:
    """El dict de `docker inspect --format '{{json .}}'`, con sus montajes reales."""
    return {
        "Name": "/wp-acme",
        "Mounts": [
            {"Type": "volume", "Source": "acme_html", "Destination": destino},
            {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
        ],
        "Config": {
            "Env": [
                "WORDPRESS_DB_HOST=mariadb",
                "WORDPRESS_DB_USER=acme",
                "WORDPRESS_TABLE_PREFIX=wp_",
            ]
        },
    }


def inspect_sin_nada() -> dict:
    """Un inspect que no dice absolutamente nada."""
    return {"Name": "/algo", "Mounts": [], "Config": {}}


# ---------------------------------------------------------------------------
# Los 12 casos
# ---------------------------------------------------------------------------


def test_1_imagen_wordpress_running() -> None:
    """Caso 1: el caso obvio. WordPress corriendo, sin ninguna etiqueta.

    Se detecta por la imagen y es operable, porque el sistema administra la
    maquina y todo lo que hay en ella es suyo (decision del usuario,
    2026-09-27). No hace falta un paso extra de "adoptar" para poder operarlo.
    """
    contenedor = fila_de_ps(Image="wordpress:php8.3-apache", State="running")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.gestionable is True
    assert candidato.senal == "imagen"
    assert candidato.por_que  # siempre hay una frase para la interfaz


def test_2_wordpress_cli_no_es_sitio() -> None:
    """Caso 2: `wordpress:cli` matchea el prefijo y es una herramienta de un solo uso.

    Si no se descarta aca, cada provisioning deja una fila fantasma en la
    pantalla, y el usuario cree que tiene un sitio que en realidad no existe.
    """
    contenedor = fila_de_ps(Names="wp-cli-acme", Image="wordpress:cli")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is False
    assert candidato.gestionable is False
    assert "wp-cli" in candidato.por_que
    # Y ni se le gasta un `docker inspect`: ya se sabe que no es un sitio.
    assert probe_docker.es_prefiltrado(contenedor) is False


def test_3_imagen_custom_con_montaje_es_sitio() -> None:
    """Caso 3: la imagen no dice WordPress, pero monta `/var/www/html`."""
    contenedor = fila_de_ps(Names="acme", Image="miempresa/wp-custom:1.2")

    # La capa 1, sola, no puede saberlo: el nombre de la imagen no dice nada.
    assert probe_docker.detectar_capa1(contenedor).es_sitio is False
    # Pero el contenedor entra al prefiltrado, asi que llega al inspect.
    assert probe_docker.es_prefiltrado(contenedor) is True

    candidato = probe_docker.detectar_capa2(contenedor, inspect_con_montaje())

    assert candidato.es_sitio is True
    assert candidato.senal == "montaje"
    assert "/var/www/html" in candidato.por_que


def test_4_mariadb_no_es_sitio() -> None:
    """Caso 4: la base de datos comparte red con el sitio y es la mitad de el.

    Sin este caso cada sitio aparece dos veces en la pantalla.
    """
    contenedor = fila_de_ps(Names="acme-db", Image="mariadb:11")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is False
    assert candidato.gestionable is False
    assert "base de datos" in candidato.por_que
    # Y no se le gasta un `docker inspect`.
    assert probe_docker.es_prefiltrado(contenedor) is False


def test_5_nginx_con_montaje_es_sitio() -> None:
    """Caso 5: `nginx:alpine` no dice WordPress en ningun lado y aun asi sirve el sitio."""
    contenedor = fila_de_ps(Names="acme-nginx", Image="nginx:alpine")

    assert probe_docker.detectar_capa1(contenedor).es_sitio is False
    assert probe_docker.es_prefiltrado(contenedor) is True

    candidato = probe_docker.detectar_capa2(contenedor, inspect_con_montaje())

    assert candidato.es_sitio is True
    assert candidato.senal == "montaje"


def test_6_bitnami_es_sitio() -> None:
    """Caso 6: el prefijo no esta en la posicion 0, pero sigue siendo WordPress."""
    contenedor = fila_de_ps(Names="acme-bitnami", Image="bitnami/wordpress:6")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.senal == "imagen"


def test_7_etiqueta_spanel_site() -> None:
    """Caso 7: lo pusimos nosotros al provisionar, asi que es nuestro."""
    contenedor = fila_de_ps(Names="acme", Image="miempresa/app:3", Labels="spanel.site=acme")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.gestionable is True
    assert candidato.senal == "etiqueta"


def test_8_managed_false_saca_el_contenedor_del_alcance() -> None:
    """Caso 8: `spanel.managed=false` es la valvula de escape.

    Este caso NO es "un sitio de otro que no toco". Es la unica forma de
    declarar que un contenedor, aunque sea un WordPress perfecto, queda fuera
    del alcance del sistema. Sigue apareciendo en la lista —que es lo que
    pediste, encontrar todo— pero las acciones no lo tocan.
    """
    contenedor = fila_de_ps(
        Names="sitio-fuera-de-alcance",
        Image="wordpress:6.5-apache",
        Labels="spanel.site=otro,spanel.managed=false",
    )

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True  # se sigue mostrando, es un sitio
    assert candidato.gestionable is False  # pero queda fuera del alcance
    assert candidato.senal == "etiqueta"


def test_8b_sin_etiqueta_managed_si_es_gestionable() -> None:
    """Caso 8b: sin `spanel.managed`, el contenedor ES operable.

    **Decision del usuario, 2026-09-27.** Todo WordPress que encontremos es del
    sistema, porque el sistema administra la maquina. No hay un paso extra de
    "adoptar" para poder operarlo.

    Este test dice exactamente lo contrario del que decia antes, y el cambio es
    deliberado: la primera version de esta A.SPEC hacía fail-closed, y con la
    regla nueva los tres WordPress de agosto —sin ninguna etiqueta, creados a
    mano— pasan a ser operables, que es lo pedido.
    """
    contenedor = fila_de_ps(
        Names=" wordpress-creado-a-mano",
        Image="wordpress:php8.3-apache",
        Labels="",  # ni una sola etiqueta
    )

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.gestionable is True


def test_8c_solo_los_negativos_explicitos_sacan_de_alcance() -> None:
    """Cualquier valor que no sea un NO explicito deja el contenedor operable."""
    for valor in ("false", "FALSE", "0", "no", "off"):
        contenedor = fila_de_ps(
            Names="wp", Image="wordpress:php8.3-apache", Labels=f"spanel.managed={valor}"
        )
        assert probe_docker.detectar_capa1(contenedor).gestionable is False, valor

    # Y todo lo demas es operable, incluido lo que no tiene sentido.
    for valor in ("true", "1", "yes", "on", "", "quizás", "lo-que-sea"):
        contenedor = fila_de_ps(
            Names="wp", Image="wordpress:php8.3-apache", Labels=f"spanel.managed={valor}"
        )
        assert probe_docker.detectar_capa1(contenedor).gestionable is True, valor


def test_transporte_local_no_duplica_el_docker() -> None:
    """Con transporte local el argv no puede quedar ["docker", "docker", ...].

    Los callers pasan el comando completo, "docker ps ...", asi que agregar un
    "docker" delante lo duplicaba. El comando duplicado sale con codigo 0 y
    stdout vacio, o sea que la lista de contenedores COMPILA VACIA sin decir
    nada: el script "__no encontro ningun WordPress__" en vez de "__no hay__".
    """
    env = {"SPANEL_DOCKER_TRANSPORT": "local", "MAIL_SERVER_PASSWORD": "x"}

    argv = probe_docker.build_docker_argv("docker ps -a --format '{{json .}}'", env=env)

    assert argv == ["docker", "ps", "-a", "--format", "{{json .}}"]
    assert argv.count("docker") == 1


def test_9_provisioned_es_gestionable() -> None:
    """Caso 9: el caso 8 al reves, con la imagen de WordPress y `managed=true`."""
    contenedor = fila_de_ps(
        Names="wp-nuestro",
        Image="wordpress:php8.3-apache",
        Labels="spanel.managed=true,spanel.site=acme",
    )

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.gestionable is True


def test_10_apagado_sigue_siendo_sitio() -> None:
    """Caso 10: un WordPress apagado sigue siendo WordPress.

    El estado no participa de la decision de "es un sitio": si participara, un
    contenedor parado desapareceria de la lista y el usuario perderia de vista
    un sitio que tiene.
    """
    contenedor = fila_de_ps(Names="wp-parado", Image="wordpress:php8.3-apache", State="exited")

    candidato = probe_docker.detectar_capa1(contenedor)

    assert candidato.es_sitio is True
    assert candidato.senal == "imagen"


def test_11_diccionario_vacio_no_lanza() -> None:
    """Caso 11: `{}`, sin ninguna clave. La diferencia entre un `[]` y un 500."""
    assert probe_docker.detectar_capa1({}).es_sitio is False
    assert probe_docker.detectar_capa1({}).senal == ""
    assert probe_docker.detectar_capa2({}, inspect_sin_nada()).es_sitio is False

    # Tampoco con datos de tipos raros, que es lo que de verdad rompe pantallas.
    for raro in (None, [], "wordpress", 42, {"Image": None}, {"Image": ["lista"]}):
        candidato = probe_docker.detectar_capa2(raro, inspect_sin_nada())
        assert candidato.es_sitio is False
        assert isinstance(candidato.por_que, str)


def test_12_lista_vacia_no_lanza() -> None:
    """Caso 12: una maquina sin contenedores es una maquina valida."""
    assert probe_docker.detectar_todos([]) == []
    assert probe_docker.detectar_todos(None) == []
    assert probe_docker.detectar_todos("no soy una lista") == []


# ---------------------------------------------------------------------------
# Invariantes
# ---------------------------------------------------------------------------


def test_labels_exact_key_not_substring() -> None:
    """I5: las etiquetas se comparan por clave exacta. Un substring no es una clave.

    `xspanel.sitex=1` NO es `spanel.site`. Si lo fuera, cualquier contenedor
    que chance tenga una etiqueta parecida pasaria por nuestro.
    """
    negativo = fila_de_ps(Names=" impostor", Image="miempresa/app:3", Labels="xspanel.sitex=1")

    assert "spanel.site" not in probe_docker.parse_labels("xspanel.sitex=1")
    assert "spanel.site" not in probe_docker.parse_labels("myspanel.site=1")
    assert probe_docker.detectar_capa1(negativo).es_sitio is False

    positivo = fila_de_ps(Names="nuestro", Image="miempresa/app:3", Labels="spanel.site=acme")

    assert probe_docker.parse_labels("spanel.site=acme") == {"spanel.site": "acme"}
    assert probe_docker.detectar_capa1(positivo).es_sitio is True


def test_password_never_in_argv() -> None:
    """I1: la contraseña nunca aparece en el argv.

    Va por `sshpass -e`, que la lee de `SSHPASS` en el entorno del hijo. En el
    argv se escapa sola a cualquier traceback y a cualquier `ps`.
    """
    clave = "clave-de-prueba-que-no-debe-volar"
    entorno = {
        "SPANEL_DOCKER_TRANSPORT": "ssh",
        "SPANEL_DOCKER_SSH_HOST": "10.0.0.9",
        "SPANEL_DOCKER_SSH_USER": "spanel",
        "MAIL_SERVER_PASSWORD": clave,
    }

    argv = probe_docker.build_docker_argv("docker ps -a --format '{{json .}}'", env=entorno)

    assert clave not in " ".join(argv)
    assert all(clave not in elemento for elemento in argv)
    # La forma tiene que ser `sshpass -e`, no `sshpass -p`.
    assert argv[:3] == ["sshpass", "-e", "ssh"]
    assert "-p" in argv  # el puerto de SSH, no el flag de la contraseña
    assert argv[-1] == "docker ps -a --format '{{json .}}'"  # el comando viaja entero


def test_probe_only_runs_read_only_commands() -> None:
    """I2: el probe es de solo lectura.

    Se recorre el codigo fuente entero del probe y se busca TODO comando de
    Docker que aparezca. Los unicos permitidos son `docker version`, `docker ps`
    y `docker inspect`. Si alguno mas aparece, el probe empezo a modificar algo
    y este test se cae.
    """
    fuente = (RAIZ_DE_SCRIPTS / "probe_docker.py").read_text(encoding="utf-8")

    subcomandos = {m.group(1) for m in re.finditer(r"\bdocker\s+([a-z][a-z-]*)", fuente)}
    assert subcomandos, "el regex tiene que encontrar los comandos de verdad"
    assert subcomandos <= {"version", "ps", "inspect"}

    # Y ademas, por si el patron se escapara de un comando con guion o compuesto.
    prohibidos = (
        "docker-compose",
        "docker compose",
        "docker run",
        "docker start",
        "docker stop",
        "docker restart",
        "docker rm",
        "docker rmi",
        "docker kill",
        "docker exec",
        "docker create",
        "docker cp",
        "docker commit",
        "docker pull",
        "docker login",
        "docker update",
        "docker volume",
        "docker network",
        "docker system",
        "docker prune",
    )
    assert [p for p in prohibidos if p in fuente] == []


def test_inspect_solo_para_los_prefiltrados() -> None:
    """I3: la lista entera sale de UNA llamada de `docker ps`.

    El `docker inspect` se hace solo para los que pasaron el prefiltrado, nunca
    para los 24. Por eso el costo no depende de cuantos contenedores hay.
    """
    contenedores = [
        fila_de_ps(Names="wp-acme", Image="wordpress:php8.3-apache"),
        fila_de_ps(Names="wp-cli", Image="wordpress:cli"),
        fila_de_ps(Names="acme-db", Image="mariadb:11"),
        fila_de_ps(Names="acme-nginx", Image="nginx:alpine"),
        fila_de_ps(Names="mailadmin-postgres-1", Image="postgres:16-alpine"),
    ]

    pedidos: list[str] = []

    def inspeccionar(nombre: str) -> dict:
        pedidos.append(nombre)
        return inspect_con_montaje()

    candidatos = probe_docker.detectar_todos(contenedores, inspeccionar)

    # Ni la herramienta ni las dos bases de datos: 2 pedidos por 5 contenedores.
    assert pedidos == ["wp-acme", "acme-nginx"]
    sitios = {nombre for nombre, candidato in candidatos if candidato.es_sitio}
    assert sitios == {"wp-acme", "acme-nginx"}


# ---------------------------------------------------------------------------
# parse_labels
# ---------------------------------------------------------------------------


def test_parse_labels_separa_por_coma() -> None:
    assert probe_docker.parse_labels("a=1,b=2,spanel.site=acme") == {
        "a": "1",
        "b": "2",
        "spanel.site": "acme",
    }


def test_parse_labels_parte_por_el_primer_igual() -> None:
    """El valor puede contener un `=`: lo que importa es la clave."""
    assert probe_docker.parse_labels("url=https://acme.test/?a=1") == {
        "url": "https://acme.test/?a=1"
    }


def test_parse_labels_no_explota_con_basura() -> None:
    """Un `Labels` que no es texto, o vacio, no puede tirar el script abajo."""
    for basura in (None, "", 42, ["a=1"], {"a": "1"}, "sin_igual", "=sin_clave"):
        etiquetas = probe_docker.parse_labels(basura)
        assert isinstance(etiquetas, dict)
    # Un token sin "=" es una etiqueta sin valor, y una sin clave se descarta.
    assert probe_docker.parse_labels("sin_igual") == {"sin_igual": ""}
    assert probe_docker.parse_labels("=sin_clave") == {}
    assert probe_docker.parse_labels("a=1,,b=2") == {"a": "1", "b": "2"}
