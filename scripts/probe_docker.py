#!/usr/bin/env python3
"""Script de prueba para A.SPEC 0007: ¿llegamos al Docker de la otra maquina?

Este es el primer test de verdad del plugin `hosting`. Todavia no hay plugin, no
hay base de datos y no hay endpoint. Solo se prueban tres cosas, en este orden:

    1. que el archivo .env exista y tenga lo necesario
    2. que la conexion SSH entre y que del otro lado haya un daemon de Docker
    3. que `docker ps` responda y quels contenedores hay

Y de paso, revisar si alguno de esos contenedores parece WordPress.

No se hardcodea ninguna credencial: todo sale del entorno.

Uso:
    python3 scripts/probe_docker.py
    python3 scripts/probe_docker.py --env-file otro.env
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / "vendor" / "systutor-core" / ".env"

# ---------------------------------------------------------------------------
# 1. Leer el .env
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    """Lee un .env simple: clave=valor, ignora comentarios y lineas vacias."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


# ---------------------------------------------------------------------------
# 2. Armar el comando. Esto es una funcion PURA: no toca la red, no ejecuta
#    nada. Por eso el test la puede verificar sin Docker y sin SSH.
#    El password se lee de MAIL_SERVER_PASSWORD a proposito: el Docker esta en
#    la misma maquina que el mailserver, y asi el secreto esta escrito una sola
#    vez en el .env.
# ---------------------------------------------------------------------------


def build_docker_argv(
    remote_command: str,
    *,
    env: dict[str, str],
) -> list[str]:
    """Arma el argv completo para correr un comando de Docker en la otra maquina.

    El remote_command viaja SIEMPRE como un solo elemento del argv, jamas
    concatenado dentro de un texto que la shell interprete. El socket de Docker
    equivale a root en la maquina, asi que un input del usuario pegado con `;`
    seria una ejecucion arbitraria.
    """
    transport = env.get("SPANEL_DOCKER_TRANSPORT", "ssh")

    if transport == "local":
        # El comando remoto YA empieza con "docker": los callers pasan
        # "docker ps ...", no "ps ...". Agregar otro "docker" delante armaba
        # ["docker", "docker", "ps"], que sale con codigo 0 y stdout vacio: el
        # peor fallo posible, porque parece que no hay ningun contenedor.
        return shlex.split(remote_command)

    if not env.get("MAIL_SERVER_PASSWORD"):
        raise ValueError("falta MAIL_SERVER_PASSWORD en el .env")

    host = env["SPANEL_DOCKER_SSH_HOST"]
    user = env["SPANEL_DOCKER_SSH_USER"]
    port = env.get("SPANEL_DOCKER_SSH_PORT", "22")
    strict = env.get("SPANEL_DOCKER_SSH_STRICT_HOST_KEY", "yes")

    # OJO: la contraseña NO va en el argv. `sshpass -e` la lee de la variable
    # SSHPASS del entorno. Si fuera con `-p`, quedaria escrita en el comando, y
    # desde ahi se escapa sola: en cualquier traceback de Python, en un `ps`, y
    # en los logs de error. Por eso `run` mete SSHPASS en el entorno del hijo.
    return [
        "sshpass",
        "-e",
        "ssh",
        "-p",
        port,
        "-o",
        f"StrictHostKeyChecking={strict}",
        "-o",
        "LogLevel=ERROR",
        f"{user}@{host}",
        remote_command,
    ]


# ---------------------------------------------------------------------------
# 3. Decidir si un contenedor es un sitio WordPress.
#    Todo lo de esta seccion son funciones PURAS: reciben lo que devolvio
#    `docker ps` (o `docker inspect`) y contestan. Sin red, sin Docker y sin
#    credenciales, por eso los 12 casos raros se prueban como tests.
#    No se devuelve un (bool, str) sino un `Candidato`, porque "es un sitio" y
#    "lo puedo operar" son dos preguntas distintas: un WordPress con
#    `spanel.managed=false` se sigue mostrando como sitio, pero queda fuera
#    del alcance de las acciones.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidato:
    """El veredicto sobre un contenedor. Es un dato, no comportamiento."""

    es_sitio: bool  # ¿es un WordPress?
    gestionable: bool  # ¿lo podemos arrancar/detener? Casi siempre sí
    senal: str  # la pista que lo identifico
    por_que: str  # frase para mostrar en la interfaz


SENAL_NINGUNA = ""
SENAL_IMAGEN = "imagen"
SENAL_ETIQUETA = "etiqueta"
SENAL_MONTAJE = "montaje"
SENAL_ENTORNO = "entorno"
SENAL_HERRAMIENTA = "herramienta"
SENAL_BASE_DATOS = "base_datos"
ETIQUETA_SITIO = "spanel.site"
ETIQUETA_GESTION = "spanel.managed"
RUTA_WEB = "/var/www/html"
# Imagenes que son la mitad de un sitio, no el sitio. Sin esta lista cada sitio
# sale dos veces en la pantalla (caso raro 4).
PREFIXOS_DE_BASE_DATOS = ("mariadb:", "mysql:", "percona:", "postgres:", "postgresql:", "redis:")
NADA = Candidato(False, False, SENAL_NINGUNA, "ninguna pista coincide")


def _texto(valor: object) -> str:
    """Como str() pero sin explotar: None y tipos raros se vuelven cadena."""
    if isinstance(valor, str):
        return valor
    return "" if valor is None else str(valor)


def _mapa(valor: object) -> dict:
    """Dict vacio ante cualquier cosa que no sea un dict (caso raro 11)."""
    return valor if isinstance(valor, dict) else {}


def _lista(valor: object) -> list:
    """Lista vacia ante cualquier cosa que no sea una lista (caso raro 12)."""
    return list(valor) if isinstance(valor, (list, tuple)) else []


def parse_labels(raw: object) -> dict[str, str]:
    """Convierte el texto "a=1,b=2" de Docker en un diccionario.

    La clave se separa con `partition("=")` y se busca EXACTA: por eso
    `xspanel.sitex` no cuenta como `spanel.site`. Un substring no es una clave.
    """
    if not isinstance(raw, str) or not raw:
        return {}
    labels: dict[str, str] = {}
    for pair in raw.split(","):
        clave, _, valor = pair.partition("=")
        if clave.strip():
            labels[clave.strip()] = valor.strip()
    return labels


def es_operable(container: object) -> bool:
    """¿Podemos arrancar, detener o borrar este contenedor?

    **DECISIÓN DEL USUARIO, 2026-09-27**: todo WordPress que encontremos es
    operable. No hay un estado intermedio de "este es tuyo pero con cuidado".

    El razonamiento del usuario: si el sistema administra el servidor, es el
    dueño de lo que hay en él. Un WordPress que aparece en la lista de
    sistemas es un WordPress del sistema, y que el usuario pueda operarlo sin
    un paso extra de "adoptar" es lo que hace que la herramienta sirva.

    Lo único que puede withdrawing un contenedor de esta regla es la etiqueta
    `spanel.managed` con un valor negativo explicito. Eso queda como válvula de
    escape: si algún dia hay que dejar un sitio fuera del alcance del sistema
    sin sacarlo de la lista, se marca y se respeta.

    OJO, y hay que decirlo: esta regla significa que el sistema se atribuye la
    propiedad de TODOS los WordPress de la maquina, incluidos los que el
    usuario creo a mano. Verificado contra el Docker real: los tres WordPress
    de agosto no tienen ninguna etiqueta, y con esta regla los tres son
    operables. Es lo pedido, y es coherente con que el sistema sea el
    administrador de la maquina; queda anotado para que sea una decision
    consciente y no un descuido.
    """
    labels = parse_labels(_mapa(container).get("Labels"))

    gestion = labels.get(ETIQUETA_GESTION, "").strip().lower()
    return gestion not in {"false", "0", "no", "off"}


def detectar_capa1(container: object) -> Candidato:
    """Capa 1: SOLO `Image` y `Labels`. Pura: no hace ninguna llamada de red.

    Devuelve SIEMPRE un Candidato, para que el que llama no tenga que
    distinguir "no hay dato" de "no es sitio". El estado no se mira: un
    WordPress apagado sigue siendo WordPress (caso raro 10).

    VERIFICADO contra Docker 29.1.3 el 2026-09-27: `Image` y `Labels` vienen
    completos, pero `Mounts` NO, Docker lo devuelve truncado con puntos
    ("/var/run/docke..."), o sea texto de pantalla, no el dato. Los montajes de
    verdad hay que pedirlos con `docker inspect`.
    """
    datos = _mapa(container)
    image = _texto(datos.get("Image")).strip()
    labels = parse_labels(datos.get("Labels"))
    operable = es_operable(datos)

    # wordpress:cli matchea el prefijo de imagen y es la herramienta de un solo
    # uso, no un sitio: sin esto cada provisioning deja una fila fantasma.
    if image.startswith("wordpress:cli") or image.endswith("/wp-cli"):
        return Candidato(False, False, SENAL_HERRAMIENTA, f"{image} es la herramienta wp-cli")
    if image.startswith(PREFIXOS_DE_BASE_DATOS):
        return Candidato(False, False, SENAL_BASE_DATOS, f"{image} es la base de datos del sitio de al lado")

    # La etiqueta va antes que la imagen: es la pista mas fuerte, porque la
    # pusimos nosotros al provisionar.
    if ETIQUETA_SITIO in labels:
        return Candidato(True, operable, SENAL_ETIQUETA, f"tiene la etiqueta {ETIQUETA_SITIO} nuestra")

    # "wordpress" en cualquier posicion: asi entra bitnami/wordpress, que no lo
    # tiene en la posicion 0 (caso raro 6).
    if "wordpress" in image.lower():
        return Candidato(True, operable, SENAL_IMAGEN, f"la imagen dice wordpress: {image}")

    return NADA


def _info_de_inspect(detalle: object) -> dict:
    """El dict del `docker inspect`. Tolera que venga envuelto en una lista."""
    if isinstance(detalle, list) and detalle:
        detalle = detalle[0]
    return _mapa(detalle)


def destinos_de_montaje(detalle_inspect: object) -> list[str]:
    """Los `Mounts` REALES, que en la lista venian truncados con puntos."""
    return [
        _texto(_mapa(m).get("Destination")).strip()
        for m in _lista(_info_de_inspect(detalle_inspect).get("Mounts"))
    ]


def entorno_de_inspect(detalle_inspect: object) -> dict[str, str]:
    """El entorno real del contenedor, como diccionario."""
    entorno: dict[str, str] = {}
    for par in _lista(_mapa(_info_de_inspect(detalle_inspect).get("Config")).get("Env")):
        clave, _, valor = _texto(par).partition("=")
        if clave.strip():
            entorno[clave.strip()] = valor.strip()
    return entorno


def detectar_capa2(container: object, detalle_inspect: object) -> Candidato:
    """Capa 2: la del `docker inspect`. Agrega los montajes y el entorno real.

    Los casos raros 3 y 5 se resuelven SOLO acá, porque la imagen no dice
    WordPress: `miempresa/wp-custom` y `nginx:alpine` sirven `/var/www/html`. Por
    eso no se pueden resolver con la lista, y por eso el inspect se reserva para
    los que ya pasaron el prefiltrado.
    """
    base = detectar_capa1(container)
    if base.es_sitio or base.senal in {SENAL_HERRAMIENTA, SENAL_BASE_DATOS}:
        return base  # la capa 1 ya lo resolvio, o lo descarto por el nombre

    operable = es_operable(container)
    for destino in destinos_de_montaje(detalle_inspect):
        if destino == RUTA_WEB or destino.startswith(f"{RUTA_WEB}/"):
            return Candidato(True, operable, SENAL_MONTAJE, f"monta {RUTA_WEB}, el sitio vive ahi")
    if "WORDPRESS_DB_HOST" in entorno_de_inspect(detalle_inspect):
        return Candidato(True, operable, SENAL_ENTORNO, "define WORDPRESS_DB_HOST en su entorno")
    return base


def es_prefiltrado(container: object) -> bool:
    """¿Vale la pena un `docker inspect` para este contenedor?

    Si la capa 1 lo descarta por el NOMBRE (la herramienta wp-cli, la base de
    datos del sitio de al lado), no. Todo lo demas pasa, porque una imagen que
    no dice WordPress puede igual estar sirviendo un sitio.
    """
    return detectar_capa1(container).senal in {SENAL_NINGUNA, SENAL_IMAGEN, SENAL_ETIQUETA}


def detectar_todos(
    contenedores: object,
    inspeccionar: Callable[[str], object] | None = None,
) -> list[tuple[str, Candidato]]:
    """Recorre la lista de `docker ps` y devuelve (nombre, veredicto) de los candidatos.

    `inspeccionar` es opcional: sin el no corre la capa 2 y se decide solo con
    la capa 1. Con el se llama SOLO para los que pasaron el prefiltrado, nunca
    para los 24: la lista entera sale de una sola llamada de `docker ps`. Una
    lista vacia devuelve una lista vacia, sin excepcion (caso raro 12).
    """
    candidatos: list[tuple[str, Candidato]] = []
    for container in _lista(contenedores):
        if not es_prefiltrado(container):
            continue
        nombre = _texto(_mapa(container).get("Names")).split(",")[0].strip()
        detalle = inspeccionar(nombre) if inspeccionar is not None and nombre else {}
        candidatos.append((nombre, detectar_capa2(container, detalle)))
    return candidatos

# ---------------------------------------------------------------------------
# 4. Correr
# ---------------------------------------------------------------------------


def run(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: int = 30,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta el comando. La contraseña viaja en el entorno del hijo, no en el argv.

    `input` es para los pocos casos donde hay que hacer escribir algo al host
    remoto por stdin, en vez de dejarlo escrito en el comando. Ver
    `reset_wp_password.password_a_archivo_remoto`.
    """
    child_env = dict(os.environ)
    # -e de sshpass lee de acá. Nunca se pasa por el argv.
    if "sshpass" in argv:
        child_env["SSHPASS"] = env.get("MAIL_SERVER_PASSWORD", "")
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
            input=input,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout="",
            stderr=f"no respondio en {timeout} segundos",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    if not args.env_file.is_file():
        print(f"FALLO: no existe el archivo de entorno {args.env_file}")
        return 1

    env = load_env_file(args.env_file)
    print(f"Entorno: {args.env_file}")
    print(f"Transporte: {env.get('SPANEL_DOCKER_TRANSPORT', 'ssh')}")

    faltan = [
        clave
        for clave in ("SPANEL_DOCKER_SSH_HOST", "SPANEL_DOCKER_SSH_USER", "MAIL_SERVER_PASSWORD")
        if not env.get(clave)
    ]
    if faltan:
        print(f"FALLO: faltan estos datos en el .env: {', '.join(faltan)}")
        return 1

    destino = f"{env['SPANEL_DOCKER_SSH_USER']}@{env['SPANEL_DOCKER_SSH_HOST']}"
    print(f"Destino:   {destino}\n")

    # Test 1: hay un daemon de Docker del otro lado.
    print("1) Docker del otro lado")
    resultado = run(build_docker_argv("docker version --format '{{.Server.Version}}'", env=env), env=env)
    if resultado.returncode != 0:
        print(f"   FALLO: {resultado.stderr.strip() or 'sin mensaje de error'}")
        if resultado.returncode == 124:
            print("   Si la maquina esta en Tailscale, la conexion no llego nunca.")
            print("   Fijate si Tailscale esta corriendo antes de culpar al script.")
        return 1
    print(f"   OK. Version del daemon: {resultado.stdout.strip()}\n")

    # Test 2: la lista de contenedores.
    print("2) Lista de contenedores")
    # '{{json .}}' en vez de 'json': la forma corta es nueva de Docker 27 y mas
    # fragile. La larga existe hace mucho y trae la misma informacion.
    resultado = run(build_docker_argv("docker ps -a --format '{{json .}}'", env=env), env=env)
    if resultado.returncode != 0:
        print(f"   FALLO: {resultado.stderr.strip() or 'sin mensaje de error'}")
        return 1

    contenedores: list[dict] = []
    for linea in resultado.stdout.splitlines():
        if not linea.strip():
            continue
        try:
            contenedores.append(json.loads(linea))
        except json.JSONDecodeError:
            print(f"   AVISO: linea que no es JSON, la salto: {linea[:80]}")

    if not contenedores:
        print("   No hay ningun contenedor. Docker anda, pero esta vacio.")
        return 0

    print(f"   OK. {len(contenedores)} contenedores.\n")

    # Test 3: cual de esos parece WordPress.
    print("3) Cuales parecen WordPress")
    print("-" * 72)
    for container in contenedores:
        nombre = (container.get("Names") or "(sin nombre)").split(",")[0]
        imagen = container.get("Image") or "(sin imagen)"
        estado = container.get("State") or "?"
        barato = detectar_capa1(container)
        marca = "WP  " if barato.es_sitio else "    "
        print(f"{marca}{nombre[:28]:<28} {estado:<9} {imagen[:24]:<24} {barato.por_que}")

    print("-" * 72)

    # Segunda pasada: SOLO los que pasaron el prefiltrado, ahora con
    # `docker inspect`, que si devuelve los montajes y las variables de verdad.
    # Una llamada por candidato, y son pocos: la lista entera ya salio de una
    # sola llamada de `docker ps` (invariante I3).
    detalles: dict[str, object] = {}

    def inspeccionar(nombre: str) -> object:
        detalle = run(
            build_docker_argv(f"docker inspect --format '{{{{json .}}}}' {nombre}", env=env),
            env=env,
        )
        if detalle.returncode != 0:
            print(f"   AVISO: {nombre}: no se pudo inspeccionar ({detalle.stderr.strip()[:60]})")
            return {}
        try:
            info = json.loads(detalle.stdout)
        except json.JSONDecodeError:
            print(f"   AVISO: {nombre}: inspect devolvio algo que no es JSON")
            return {}
        detalles[nombre] = info
        return info

    print("\n4) Detalle de los candidatos (docker inspect)")
    candidatos = detectar_todos(contenedores, inspeccionar)
    for nombre, candidato in candidatos:
        if not candidato.es_sitio:
            print(f"   {nombre}: descartado. {candidato.por_que}")
            continue
        gestion = "si" if candidato.gestionable else "no (spanel.managed lo excluye)"
        print(f"   {nombre} [{candidato.senal}] {candidato.por_que}. ¿Lo podemos tocar? {gestion}")
        montajes = ", ".join(destinos_de_montaje(detalles.get(nombre, {}))) or "(ninguno)"
        base = entorno_de_inspect(detalles.get(nombre, {})).get("WORDPRESS_DB_HOST")
        print(f"      montajes:   {montajes}")
        print(f"      base datos: {base or '(no esta WORDPRESS_DB_HOST)'}")

    sitios = [nombre for nombre, candidato in candidatos if candidato.es_sitio]
    print(f"\n{len(sitios)} de {len(contenedores)} contenedores son sitios WordPress.")
    if not sitios:
        print("Si esperabas ver WordPress aca, la lista de mas arriba dice donde mira.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
