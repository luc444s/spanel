"""El reset de contraseña, probado contra la laptop de verdad.

A.SPEC 0011. Este archivo NO tiene fixtures ni mocks: corre el script real
contra el sitio real y lee la base real. Un test offline de este script no
prueba nada, porque los dos bugs que tuvo vivian justo en la parte que un
fixture no toca.

Por eso escribe, y por eso los rails son parte del contrato:

1. Opt-in: sin SPANEL_LIVE_TESTS=1 no corre nada.
2. Allowlist: SPANEL_LIVE_SITE tiene que matchear ^spanel-(demowp|lab)-.
3. Respaldo antes de escribir: un dump de wp_users y wp_usermeta queda en un
   archivo temporal local antes del primer INSERT.
4. Restauracion incondicional: el finalizer vuelca el respaldo de nuevo aunque
   un test falle en el medio. Borrar por login es el camino rapido; el dump es
   la garantia.
5. Solo logins descartables: lab_a1, lab_a2 y dos filas de lab_dup. El admin
   real nunca se duplica ni se resetea; el camino por defecto se mira con
   --dry-run, que no escribe.

Uso:
    SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

RAIZ_DE_SCRIPTS = Path(__file__).resolve().parent.parent
RUTA_DEL_SCRIPT = RAIZ_DE_SCRIPTS / "reset_wp_password.py"

sys.path.insert(0, str(RAIZ_DE_SCRIPTS))

import reset_wp_password as reset

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
SITIO = os.environ.get("SPANEL_LIVE_SITE", "spanel-demowp-wp")
RE_SITIO_PERMITIDO = re.compile(r"^spanel-(demowp|lab)-")
LOGINS_DESCARTABLES = ("lab_a1", "lab_a2", "lab_dup")
PASSWORD_NUEVA = "lab-nueva-0010"

# El PHP que verifica una contraseña contra un hash que ya esta en la base.
# Entra por STDIN, igual que el hash del script, para no pasar la contraseña
# por el argv. `wp eval` bootea WordPress entero, asi que `wp_check_password`
# es la misma funcion que usa un login real.
PHP_VERIFICA_HASH = (
    '$l = explode("\\n", stream_get_contents(STDIN), 2);'
    '$p = trim($l[0]);'
    '$h = trim($l[1] ?? "");'
    'echo wp_check_password($p, $h) ? "si" : "no";'
)

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="test de integracion: requiere SPANEL_LIVE_TESTS=1 y la laptop encendida",
)


@dataclass(slots=True)
class Sitio:
    """Lo que el test necesita saber del sitio real, resuelto una sola vez."""

    nombre: str
    env: dict[str, str]
    db: str
    usuario: str
    password: str
    base: str
    prefijo: str
    volumen: str
    red: str
    credenciales: str
    originales: dict[int, tuple[str, str, str]]


# ---------------------------------------------------------------------------
# Hablar con la base. La contraseña NO va en el argv: el contenedor de la base
# ya la tiene en MARIADB_PASSWORD, y el cliente la lee de MYSQL_PWD. Asi este
# test no agrega un lugar nuevo donde el secreto viaje por la linea de comandos
# (eso es A.SPEC 0012, no este archivo).
# ---------------------------------------------------------------------------


def _comando_sql(sitio: Sitio) -> str:
    return (
        f"docker exec -i {sitio.db} sh -c "
        f"'MYSQL_PWD=\"$MARIADB_PASSWORD\" mariadb -u \"$MARIADB_USER\" {sitio.base} -N -B'"
    )


def _sql(sitio: Sitio, sentencia: str) -> list[list[str]]:
    salida = reset.pedir(sitio.env, _comando_sql(sitio), entrada=sentencia)
    return [linea.split("\t") for linea in salida.splitlines() if linea]


def _ids_de(sitio: Sitio, login: str) -> list[int]:
    filas = _sql(
        sitio,
        f"SELECT ID FROM {sitio.prefijo}users WHERE user_login='{login}' ORDER BY ID",
    )
    return [int(fila[0]) for fila in filas]


def _hashes(sitio: Sitio, ids: list[int]) -> dict[int, str]:
    lista = ",".join(str(id_usuario) for id_usuario in ids)
    filas = _sql(
        sitio,
        f"SELECT ID, user_pass FROM {sitio.prefijo}users WHERE ID IN ({lista})",
    )
    return {int(fila[0]): fila[1] for fila in filas}


def _ahora_remoto(sitio: Sitio) -> int:
    """El reloj de la laptop, que es el que usan los eventos de Docker."""
    filas = _sql(sitio, "SELECT UNIX_TIMESTAMP()")
    return int(filas[0][0])


def _eventos_wordpress_cli(sitio: Sitio, desde: int, hasta: int) -> list[str]:
    """Los `create` de contenedores wordpress:cli en una ventana de tiempo.

    Es la prueba de I3: si el login es ambiguo, el script no llega a calcular
    el hash, y sin hash no hay contenedor de wp-cli. El test que SI resetea usa
    este mismo detector como control positivo, para que un detector roto no
    haga pasar el test por la razon equivocada.
    """
    comando = (
        f"docker events --since {desde} --until {hasta} "
        "--filter type=container --filter event=create "
        "--format '{{.Actor.Attributes.image}}'"
    )
    resultado = reset.run(reset.build_docker_argv(comando, env=sitio.env), env=sitio.env, timeout=30)
    if resultado.returncode != 0:
        pytest.fail(f"no se pudieron leer los eventos de Docker: {resultado.stderr.strip()[:200]}")
    return [linea for linea in resultado.stdout.splitlines() if linea.startswith("wordpress:cli")]


# ---------------------------------------------------------------------------
# El respaldo y la restauracion. `mariadb-dump` es el `mysqldump` de MariaDB.
# ---------------------------------------------------------------------------


def _volcar(sitio: Sitio, destino: Path) -> None:
    comando = (
        f"docker exec {sitio.db} sh -c "
        f"'MYSQL_PWD=\"$MARIADB_PASSWORD\" mariadb-dump -u \"$MARIADB_USER\" "
        f"{sitio.base} {sitio.prefijo}users {sitio.prefijo}usermeta'"
    )
    destino.write_text(reset.pedir(sitio.env, comando), encoding="utf-8")


def _restaurar(sitio: Sitio, respaldo: Path) -> None:
    comando = (
        f"docker exec -i {sitio.db} sh -c "
        f"'MYSQL_PWD=\"$MARIADB_PASSWORD\" mariadb -u \"$MARIADB_USER\" {sitio.base}'"
    )
    reset.pedir(sitio.env, comando, entrada=respaldo.read_text(encoding="utf-8"))


def _borrar_descartables(sitio: Sitio) -> None:
    """Camino rapido de limpieza: borra SOLO los logins del test, por login.

    No es la garantia (esa es el dump), es para que una corrida normal no
    dependa de un DROP TABLE para dejar el sitio limpio.
    """
    logins = ",".join(f"'{login}'" for login in LOGINS_DESCARTABLES)
    filas = _sql(sitio, f"SELECT ID FROM {sitio.prefijo}users WHERE user_login IN ({logins})")
    if filas:
        ids = ",".join(fila[0] for fila in filas)
        _sql(sitio, f"DELETE FROM {sitio.prefijo}usermeta WHERE user_id IN ({ids})")
    _sql(sitio, f"DELETE FROM {sitio.prefijo}users WHERE user_login IN ({logins})")


def _crear_descartables(sitio: Sitio) -> None:
    """Crea los usuarios del test, con un hash real copiado de una fila vieja.

    El hash inicial no se usa para entrar: los tests que resetean lo pisan. Se
    copia uno real para que las filas tengan la misma forma que una de verdad
    y para que "el hash no cambio" compare algo con forma de hash.
    """
    fila_base = _sql(sitio, f"SELECT user_pass FROM {sitio.prefijo}users ORDER BY ID LIMIT 1")
    if not fila_base:
        pytest.fail("el sitio no tiene ningun usuario del cual sacar un hash inicial")
    hash_inicial = fila_base[0][0].replace("'", "''")
    valores = (
        ("lab_a1", "lab_a1@example.invalid"),
        ("lab_a2", "lab_a2@example.invalid"),
        ("lab_dup", "lab_dup_uno@example.invalid"),
        ("lab_dup", "lab_dup_dos@example.invalid"),
    )
    sentencias = [
        f"INSERT INTO {sitio.prefijo}users "
        "(user_login, user_pass, user_email, user_registered, user_status, display_name) "
        f"VALUES ('{login}', '{hash_inicial}', '{email}', '2026-01-01 00:00:00', 0, '{login}');"
        for login, email in valores
    ]
    _sql(sitio, "\n".join(sentencias))

    # Sin esto serian usuarios sin rol: pueden tener contraseña pero no entran
    # al panel. El rol NO vive en wp_users, vive en wp_usermeta, con el prefijo
    # repetido en la clave del metadato. Es el mismo detalle que A.SPEC 0008
    # necesita, y por eso el test lo escribe de verdad en vez de suponerlo.
    logins = ",".join(f"'{login}'" for login, _ in valores)
    _sql(
        sitio,
        f"INSERT INTO {sitio.prefijo}usermeta (user_id, meta_key, meta_value) "
        f"SELECT ID, '{sitio.prefijo}capabilities', 'a:1:{{s:13:\"administrator\";b:1;}}' "
        f"FROM {sitio.prefijo}users WHERE user_login IN ({logins});"
        f"INSERT INTO {sitio.prefijo}usermeta (user_id, meta_key, meta_value) "
        f"SELECT ID, '{sitio.prefijo}user_level', '10' "
        f"FROM {sitio.prefijo}users WHERE user_login IN ({logins});",
    )


def _dejar_como_estaba(sitio: Sitio, respaldo: Path, carpeta: Path) -> None:
    """El finalizer. Borra por login y despues restaura el dump, pase lo que pase."""
    try:
        _borrar_descartables(sitio)
    except reset.Fallo as exc:
        print(f"AVISO: no se pudieron borrar las filas de prueba: {exc}", file=sys.stderr)

    try:
        _restaurar(sitio, respaldo)
    except (reset.Fallo, OSError):
        # Si la restauracion falla, el archivo es la unica forma de arreglarlo
        # a mano: se dice donde quedo y no se borra.
        print(f"\nRESPALDO CONSERVADO PARA RESTAURAR A MANO: {respaldo}", file=sys.stderr)
        raise

    # Un dump viejo puede traer filas descartables de una corrida anterior.
    # La restauracion las devuelve; se borran otra vez para no dejarlas.
    _borrar_descartables(sitio)
    shutil.rmtree(carpeta, ignore_errors=True)


@pytest.fixture(scope="session")
def sitio(request: pytest.FixtureRequest) -> Iterator[Sitio]:
    """Resuelve el sitio real, respalda las tablas y crea los usuarios del test."""
    if not RE_SITIO_PERMITIDO.match(SITIO):
        pytest.fail(
            f"SPANEL_LIVE_SITE={SITIO!r} no matchea {RE_SITIO_PERMITIDO.pattern!r}: "
            "este test solo puede escribir en un sitio demowp o lab"
        )

    env = reset.load_env_file(reset.DEFAULT_ENV_FILE)
    info = reset.leer_inspect(env, SITIO)
    if not info.get("State", {}).get("Running"):
        pytest.fail(f"el sitio {SITIO} no esta corriendo")

    conexion = reset.conexion_de_la_base(info)
    db = conexion["host"]
    estado = reset.pedir_palabras(env, f"docker inspect --format '{{{{.State.Running}}}}' {db}")
    if not estado or estado[0] != "true":
        pytest.fail(f"la base {db} no esta corriendo")

    sitio = Sitio(
        nombre=SITIO,
        env=env,
        db=db,
        usuario=conexion["usuario"],
        password=conexion["password"],
        base=conexion["base"],
        prefijo=reset.prefijo_de_tablas(
            env,
            db,
            conexion["usuario"],
            conexion["password"],
            conexion["base"],
        ),
        volumen=reset.volumen_del_sitio(info),
        red=reset.red_de(info, db),
        credenciales=conexion["_contenido"],
        originales={},
    )

    carpeta = Path(tempfile.mkdtemp(prefix="spanel-reset-live-"))
    respaldo = carpeta / "usuarios-antes.sql"
    # El respaldo va ANTES del primer INSERT (rail 3), y el finalizer se
    # registra ya mismo para que tambien cubra un fallo creando los usuarios
    # (rail 4).
    _volcar(sitio, respaldo)
    sitio.originales = {
        int(fila[0]): (fila[1], fila[2], fila[3])
        for fila in _sql(
            sitio,
            f"SELECT ID, user_login, user_email, user_pass FROM {sitio.prefijo}users",
        )
        if fila[1] not in LOGINS_DESCARTABLES
    }
    request.addfinalizer(lambda: _dejar_como_estaba(sitio, respaldo, carpeta))

    _borrar_descartables(sitio)
    _crear_descartables(sitio)
    if len(_ids_de(sitio, "lab_dup")) != 2:
        pytest.fail("no se pudieron crear dos filas con el mismo login lab_dup")

    yield sitio


def _correr_el_script(sitio: Sitio, *argumentos: str) -> subprocess.CompletedProcess[str]:
    return reset.run(
        [sys.executable, str(RUTA_DEL_SCRIPT), sitio.nombre, *argumentos],
        env=sitio.env,
        timeout=180,
    )


def _wp_check_password(sitio: Sitio, password: str, hash_actual: str) -> bool:
    """Pregunta a WordPress si la contraseña entra con ESE hash de la base.

    No es comparar strings: es `wp_check_password`, la funcion del login real,
    contra el hash que quedo escrito.
    """
    archivo = reset.escribir_archivo_remoto(sitio.env, sitio.credenciales, "dbenv")
    try:
        comando = (
            f"docker run -i --rm --network {sitio.red} --env-file {archivo} "
            f"-v {sitio.volumen}:/var/www/html:ro wordpress:cli "
            f"wp eval '{PHP_VERIFICA_HASH}' --path=/var/www/html"
        )
        salida = reset.pedir(
            sitio.env,
            comando,
            secretos=(password, sitio.password),
            entrada=f"{password}\n{hash_actual}",
        ).strip()
    finally:
        reset.borrar_archivo_remoto(sitio.env, archivo)
    return salida == "si"


# ---------------------------------------------------------------------------
# Caso A: logins distintos. Resetear uno no toca al otro.
# ---------------------------------------------------------------------------


def test_caso_a_dos_logins_distintos(sitio: Sitio) -> None:
    id_a1 = _ids_de(sitio, "lab_a1")[0]
    id_a2 = _ids_de(sitio, "lab_a2")[0]
    antes = _hashes(sitio, [id_a1, id_a2])

    resultado = _correr_el_script(sitio, "--usuario", "lab_a1", "--password", PASSWORD_NUEVA)

    assert resultado.returncode == 0, resultado.stderr
    despues = _hashes(sitio, [id_a1, id_a2])
    assert despues[id_a1] != antes[id_a1], "el login elegido no cambio"
    assert despues[id_a2] == antes[id_a2], "resetear lab_a1 cambio tambien a lab_a2"


def test_caso_a_resetea_y_la_password_nueva_entra(sitio: Sitio) -> None:
    id_a1 = _ids_de(sitio, "lab_a1")[0]
    desde = _ahora_remoto(sitio) - 1

    resultado = _correr_el_script(sitio, "--usuario", "lab_a1", "--password", PASSWORD_NUEVA)

    hasta = _ahora_remoto(sitio) + 1
    assert resultado.returncode == 0, resultado.stderr
    hash_actual = _hashes(sitio, [id_a1])[id_a1]
    assert _wp_check_password(sitio, PASSWORD_NUEVA, hash_actual), (
        "la contraseña nueva no valida contra el hash real de la base"
    )
    # Control positivo de I3: un reset que SI llega a hashear crea wordpress:cli.
    assert _eventos_wordpress_cli(sitio, desde, hasta), (
        "el detector de eventos no vio el contenedor de wp-cli de un reset exitoso"
    )


def test_los_dos_admins_tienen_rol_administrator(sitio: Sitio) -> None:
    """Los usuarios de prueba son administradores de verdad, no filas sueltas."""
    ids = _ids_de(sitio, "lab_a1") + _ids_de(sitio, "lab_a2")
    lista = ",".join(str(id_usuario) for id_usuario in ids)
    filas = _sql(
        sitio,
        f"SELECT user_id, meta_value FROM {sitio.prefijo}usermeta "
        f"WHERE user_id IN ({lista}) AND meta_key='{sitio.prefijo}capabilities'",
    )
    roles = {int(fila[0]): fila[1] for fila in filas}
    assert set(roles) == set(ids), "falta la fila de capabilities de algun admin"
    for id_usuario in ids:
        assert "administrator" in roles[id_usuario], f"ID {id_usuario} no quedo como administrator"


# ---------------------------------------------------------------------------
# Caso B: dos filas con el mismo login.
# ---------------------------------------------------------------------------

def test_caso_b_login_duplicado_falla(sitio: Sitio) -> None:
    ids = _ids_de(sitio, "lab_dup")
    assert len(ids) == 2, "el fixture tiene que dejar exactamente dos lab_dup"
    antes = _hashes(sitio, ids)
    desde = _ahora_remoto(sitio) - 1

    resultado = _correr_el_script(sitio, "--usuario", "lab_dup", "--password", PASSWORD_NUEVA)

    hasta = _ahora_remoto(sitio) + 1
    assert resultado.returncode != 0, "un login repetido no puede reportar exito"
    # OJO: el codigo viejo TAMBIEN salia con rc=1 (con el mensaje "el UPDATE no
    # quedo aplicado"), asi que esta linea sola no distingue el bug. Lo que lo
    # distingue es la de abajo: el codigo viejo cambiaba las dos filas.
    assert _hashes(sitio, ids) == antes, "el intento ambiguo escribio en alguna fila"
    assert not _eventos_wordpress_cli(sitio, desde, hasta), (
        "el intento ambiguo creo un contenedor wordpress:cli: la resolucion "
        "tiene que pasar antes del hash"
    )


def test_el_error_de_ambiguedad_lista_todos_los_ids(sitio: Sitio) -> None:
    ids = _ids_de(sitio, "lab_dup")

    resultado = _correr_el_script(sitio, "--usuario", "lab_dup", "--password", PASSWORD_NUEVA)

    salida = resultado.stdout + resultado.stderr
    assert resultado.returncode != 0
    for id_usuario in ids:
        assert f"ID {id_usuario} lab_dup" in salida, (
            f"el error no nombra al candidato ID {id_usuario}; el operador no puede actuar"
        )


def test_caso_b_por_id_resetea_uno(sitio: Sitio) -> None:
    ids = _ids_de(sitio, "lab_dup")
    elegido, otro = ids[0], ids[1]
    antes = _hashes(sitio, [elegido, otro])

    resultado = _correr_el_script(sitio, "--id", str(elegido), "--password", PASSWORD_NUEVA)

    assert resultado.returncode == 0, resultado.stderr
    despues = _hashes(sitio, [elegido, otro])
    assert despues[elegido] != antes[elegido], "--id no cambio la fila elegida"
    assert despues[otro] == antes[otro], "--id cambio tambien la otra fila con el mismo login"


def test_dry_run_detecta_la_ambiguedad(sitio: Sitio) -> None:
    ids = _ids_de(sitio, "lab_dup")
    antes = _hashes(sitio, ids)

    resultado = _correr_el_script(sitio, "--usuario", "lab_dup", "--password", PASSWORD_NUEVA, "--dry-run")

    salida = resultado.stdout + resultado.stderr
    assert resultado.returncode != 0, "--dry-run dejo pasar un login ambiguo"
    for id_usuario in ids:
        assert f"ID {id_usuario}" in salida
    assert _hashes(sitio, ids) == antes, "--dry-run escribio"


# ---------------------------------------------------------------------------
# Compatibilidad y argumentos.
# ---------------------------------------------------------------------------


def test_sin_flags_apunta_a_admin_sin_escribir(sitio: Sitio) -> None:
    ids = [int(fila[0]) for fila in _sql(
        sitio,
        f"SELECT ID FROM {sitio.prefijo}users WHERE user_login='admin' ORDER BY ID",
    )]
    assert len(ids) == 1, "el sitio de prueba cambio: ahora hay mas de un admin"
    antes = _hashes(sitio, ids)

    resultado = _correr_el_script(sitio, "--dry-run")

    assert resultado.returncode == 0, resultado.stderr
    assert "WordPress: admin" in resultado.stdout, "sin flags el script dejo de apuntar a admin"
    assert f"ID {ids[0]}" in resultado.stdout, "--dry-run no muestra el ID elegido"
    assert _hashes(sitio, ids) == antes, "--dry-run escribio en el admin real"


def test_id_y_usuario_juntos_fallan(sitio: Sitio) -> None:
    ids = [int(fila[0]) for fila in _sql(
        sitio,
        f"SELECT ID FROM {sitio.prefijo}users WHERE user_login='admin' ORDER BY ID",
    )]
    antes = _hashes(sitio, ids)

    resultado = _correr_el_script(
        sitio,
        "--id",
        str(ids[0]),
        "--usuario",
        "admin",
        "--password",
        PASSWORD_NUEVA,
    )

    assert resultado.returncode != 0, "los dos selectores juntos no pueden tener precedencia"
    assert "--id" in resultado.stderr
    assert "--usuario" in resultado.stderr
    assert _hashes(sitio, ids) == antes, "el rechazo de argumentos toco la base"


# ---------------------------------------------------------------------------
# Chequeo estatico y limpieza.
# ---------------------------------------------------------------------------


def test_el_update_va_por_id() -> None:
    lineas = RUTA_DEL_SCRIPT.read_text(encoding="utf-8").splitlines()

    updates = [linea for linea in lineas if "UPDATE " in linea and "user_pass" in linea]
    assert len(updates) == 1, f"se esperaba un solo UPDATE de user_pass, hay {len(updates)}"
    assert "WHERE ID=" in updates[0], "el UPDATE no filtra por primary key"
    assert "user_login" not in updates[0], "el UPDATE sigue filtrando por login"

    lecturas = [linea for linea in lineas if "SELECT user_pass" in linea]
    assert lecturas, "no se encontro la lectura del hash"
    for linea in lecturas:
        assert "WHERE ID=" in linea, "la lectura del hash no va por ID"
        assert "user_login" not in linea, "la lectura del hash sigue filtrando por login"


def test_limpieza_deja_el_sitio_como_estaba(sitio: Sitio) -> None:
    _borrar_descartables(sitio)

    for login in LOGINS_DESCARTABLES:
        assert _ids_de(sitio, login) == [], f"quedo una fila descartable: {login}"

    actuales = {
        int(fila[0]): (fila[1], fila[2], fila[3])
        for fila in _sql(
            sitio,
            f"SELECT ID, user_login, user_email, user_pass FROM {sitio.prefijo}users",
        )
    }
    assert actuales == sitio.originales, "alguna fila que ya existia cambio de login, email o hash"
