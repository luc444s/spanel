#!/usr/bin/env python3
"""Cambiar la contraseña de un usuario de un WordPress que YA está corriendo.

Es la A.SPEC 0008, y es la respuesta a una pregunta concreta: si el sistema no
guarda las contraseñas, ¿cómo entra un usuario que perdió la suya? Regenerando.

Y se regenera SIN APAGAR NADA. La idea tiene dos pasos:

1. Calcular el hash con el código de WordPress. Se monta el volumen del sitio en
   SOLO LECTURA dentro de un contenedor de usar y tirar, y se llama a la misma
   clase `PasswordHash` que usa WordPress. Por eso no reimplementamos phpass a
   mano, que es el error que despues nadie encuentra.

2. Un UPDATE de UNA FILA en la base de datos, que esta corriendo. El sitio
   sigue sirviendo durante todo el proceso, y WordPress invalida las sesiones
   abiertas solo, porque las cookies se validan contra un hash de la contraseña.

Lo que este script NO hace, y por que:

- No toca el filesystem del sitio. El volumen va montado en `:ro`.
- No lee ni escribe wp-config.php, ni la base, mas alla de esa una fila.
- No necesita wp-cli con permisos de escritura, que es lo unico que podria
  corromper un sitio vivo.

Y lo que NO necesita guardar el sistema para que esto funcione: el contenedor de
WordPress declara su propia conexion a la base en sus variables de entorno
(`WORDPRESS_DB_HOST`, `_USER`, `_PASSWORD`, `_NAME`). Todo lo que hace falta
esta en Docker.

Uso:
    python3 scripts/reset_wp_password.py spanel-pwtest-wp
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --password 'la-nueva'
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --password 'la-nueva' --dry-run

Si no se pasa `--password`, se genera una y se imprime. Ojo: se imprime UNA vez
y no se guarda en ningun lado. Si se pierde, se regenera otra vez con este
mismo script.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_docker import build_docker_argv, load_env_file, run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / "vendor" / "systutor-core" / ".env"

# Como guarda WordPress las contraseñas, SEGUN LA VERSION.
#
# Verificado contra WordPress 7.0.4 el 2026-09-27: `wp_hash_password()` devuelve
# un bcrypt con identificador `$wp$`, de 63 caracteres:
#     $wp$2y$10$fZJO6EwucQ9itU7lKtWLJuCVJX27uy1fxdXKgo/wcr7IRXRmyfmqy
#
# O sea que NO es phpass. La primera version de este script asumia phpass
# portable (`$P$B`, 34 caracteres) y por lo tanto escribia un hash que
# WordPress 7 no acepta: la fila quedabaUpdated y ninguna contraseña entraba.
# El sintoma era desconcertante, porque el script moria en "el UPDATE no quedo
# aplicado" cuando en realidad el UPDATE estaba perfecto.
#
# Este regex acepta las dos familias, por si hay un WordPress viejo con php 5:
#     6.8 en adelante -> $wp$  + bcrypt ($2y$10$...)
#     hasta 6.7       -> $P$B  + phpass portable
RE_HASH_WP = re.compile(r"^\$wp\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
RE_HASH_PHPASS = re.compile(r"^\$P\$[./A-Za-z0-9]{31}$")


def hash_valido(valor: str) -> bool:
    return bool(RE_HASH_WP.match(valor) or RE_HASH_PHPASS.match(valor))

# Lo que si o si va dentro del SQL: el usuario y el prefijo de tablas. Solo
# letras, digitos y guion bajo. Cualquier otra cosa se rechaza, en vez de
# escaparse: escapar bien es dificil de auditar, rechazar es trivial.
RE_SQL_SEGURO = re.compile(r"^[A-Za-z0-9_]{1,32}$")

# El hash lo produce `wp_hash_password()` de WordPress, no esta constante. Va
# aca solo para dejar escrito que NO se reimplementa phpass ni bcrypt: el
# formato cambio entre versiones de WordPress y adivinarlo es exactamente el
# error que rompio la primera version de este script.


class Fallo(Exception):
    """Un error que hay que explicarle a la persona, no un traceback."""


def pedir(
    env: dict[str, str],
    comando: str,
    *,
    secretos: tuple[str, ...] = (),
    entrada: str | None = None,
) -> str:
    """Corre un comando y devuelve stdout. Falla con un mensaje para la persona.

    `secretos` se tapan antes de armar el mensaje: el comando lleva la
    contraseña de la base adentro, y un error que la imprime es una contraseña
    en el log. `entrada` va por stdin, para lo que no debe aparecer en el
    comando.
    """
    r = run(build_docker_argv(comando, env=env), env=env, input=entrada)
    if r.returncode != 0:
        raise Fallo(redactar(f"el comando fallo:\n  {comando}\n  {(r.stderr or r.stdout).strip()[:400]}", secretos))
    return r.stdout


def redactar(texto: str, secretos: tuple[str, ...] = ()) -> str:
    for secreto in secretos:
        if secreto:
            texto = texto.replace(secreto, "<oculto>")
    return texto


def pedir_palabras(env: dict[str, str], comando: str) -> list[str]:
    return [line.strip() for line in pedir(env, comando).splitlines() if line.strip()]


def escribir_archivo_remoto(env: dict[str, str], contenido: str, etiqueta: str) -> str:
    """Deja contenido en un archivo 600 del host remoto, fuera del argv.

    Ojo con quien lee este archivo: lo lee el DAEMON de docker, que corre como
    root, asi que 600 esta bien. Un archivo montado DENTRO de un contenedor lo
    lee el usuario del contenedor, que en `wordpress:cli` es www-data (uid 82),
    y ahi 600 no lo puede leer. Por eso la contraseña NO va por archivo.
    """
    ruta = f"/tmp/spanel-{etiqueta}-{secrets.token_hex(6)}"
    r = run(
        build_docker_argv(f"umask 077 && cat > {ruta} && chmod 600 {ruta}", env=env),
        env=env,
        input=contenido,
    )
    if r.returncode != 0:
        raise Fallo(f"no se pudo escribir {etiqueta} en el host remoto: {(r.stderr or '').strip()[:200]}")
    return ruta


def borrar_archivo_remoto(env: dict[str, str], *rutas: str) -> None:
    for ruta in rutas:
        run(build_docker_argv(f"rm -f {ruta}", env=env), env=env)


# El PHP que corre DENTRO de wordpress:cli. Lee la contraseña de su entrada
# estandar, calcula el hash con la MISMA funcion que usa WordPress, y despues
# se autocomprueba. Si lo que leyo no corresponde al hash que calculo, avisa y
# sale con codigo 1 en vez de devolver algo que parece un hash y no lo es.
#
# El `STDIN` y no `php://stdin` porque wp-cli reescribe la constante de flujo y
# `stream_get_contents("php://stdin")` recibe un string donde espera un recurso.
PHP_HASH_AUTOCOMPROBADO = (
    '$p = trim(stream_get_contents(STDIN));'
    'if ($p === "") { fwrite(STDERR, "FALLO: la contraseña llego vacia"); exit(1); }'
    '$h = wp_hash_password($p);'
    'if (!wp_check_password($p, $h)) { fwrite(STDERR, "FALLO: el hash no valida la contraseña"); exit(1); }'
    'echo $h;'
)


def para_sql(valor: str) -> str:
    """Deja un valor listo para ir dentro de una sentencia en comillas dobles.

    Un hash de WordPress arranca con simbolo de pesos, y la shell del host
    remoto los ve y los expande como variables: `$wp` y `$2y` valen vacio, y a
    la base llega un hash mutilado. Por eso el `$` se escapa.

    Why alcanza con escapar el `$` y no hay que Appointarse los demas: el regex
    de arriba ya garantiza que el hash solo tiene puntos, barras, alfanumericos
    y signos de pesos. No puede llegar ningun otro caracter con significado
    para la shell. O sea, la validacion previa es la que hace seguro este
    escapado, y por eso las dos cosas van juntas.
    """
    if not hash_valido(valor):
        raise Fallo(f"esto no parece un hash de WordPress y no se manda a la base: {valor[:40]!r}")
    return valor.replace("$", r"\$")


def hash_de_password(
    env: dict[str, str],
    info: dict,
    credenciales: dict[str, str],
    db: str,
    password: str,
) -> str:
    """Le pide el hash a `wp_hash_password()`, que es lo que WordPress usa de verdad.

    Un contenedor de usar y tirar, con el volumen del sitio y la red del
    contenedor, ejecutando `wp eval`.

    Dos detalles que costaron encontrar, y los dos son la diferencia entre que
    esto funcione y que no:

    1) La contraseña entra por STDIN, no por un archivo montado. Se probo con
       un archivo en 600 y NO funcionaba, en silencio: el contenedor corre como
       www-data (uid 82), no como root, asi que no podia leerlo;
       `file_get_contents` devolvia false, `trim(false)` era la cadena vacia, y
       el hash que salia era el hash de la VACIA. Un hash con la forma
       correcta y que no servia para nada, y el script lo daba por bueno.

    2) Por eso el PHP se autoverifica: calcula el hash, y despues comprueba con
       `wp_check_password` que ese hash corresponda a la contraseña que leyo. Si
       no, sale con codigo 1 y un mensaje. Un error silencioso se convierte en
       un ruido.

    Y la base entra por `--env-file`, no por `-e` en el comando: el
    `wp-config.php` de la imagen oficial lee las variables de la base del
    ENTORNO en runtime, asi que sin ellas WordPress busca una base llamada
    `mysql` y falla.

    El volumen del sitio va de SOLO LECTURA. `wp eval` carga WordPress entero,
    asi que con el volumen de escritura este comando podria tocar el sitio
    mientras lo esta sirviendo.
    """
    volumen = volumen_del_sitio(info)
    archivo_db = escribir_archivo_remoto(env, credenciales["_contenido"], "dbenv")
    try:
        salida = pedir(
            env,
            f"docker run -i --rm --network {red_de(info, db)} --env-file {archivo_db} "
            f"-v {volumen}:/var/www/html:ro wordpress:cli "
            f"wp eval '{PHP_HASH_AUTOCOMPROBADO}' --path=/var/www/html",
            secretos=(password, credenciales["_password"]),
            entrada=password,
        ).strip()
    finally:
        borrar_archivo_remoto(env, archivo_db)

    if not hash_valido(salida):
        raise Fallo(
            "wp_hash_password no devolvio un hash reconocible "
            f"({salida[:60]!r}). Si el sitio es de otra version, el formato puede ser otro."
        )
    return salida


def red_de(info: dict, db: str) -> str:
    """La red a la que hay que conectarse para alcanzar la base.

    Sale de las redes del propio contenedor de WordPress: la base esta en la
    misma red por definicion, si no el sitio no tendria base.
    """
    redes = list((info.get("NetworkSettings", {}).get("Networks") or {}).keys())
    if not redes:
        raise Fallo("el contenedor de WordPress no esta en ninguna red, no se puede llegar a la base")
    return redes[0]


def leer_inspect(env: dict[str, str], contenedor: str) -> dict:
    return json.loads(pedir(env, f"docker inspect --format '{{{{json .}}}}' {contenedor}").strip())


def conexion_de_la_base(info: dict) -> dict[str, str]:
    """Las variables de la base que declara el propio contenedor de WordPress."""
    variables = dict(p.partition("=")[::2] for p in (info.get("Config", {}).get("Env") or []))
    claves = ("WORDPRESS_DB_HOST", "WORDPRESS_DB_USER", "WORDPRESS_DB_PASSWORD", "WORDPRESS_DB_NAME")
    faltan = [k for k in claves if not variables.get(k)]
    if faltan:
        raise Fallo(f"el contenedor no declara {', '.join(faltan)} en su entorno")
    # Se devuelven con las claves y el contenido de un --env-file, porque de eso
    # es como las necesita el contenedor que calcula el hash.
    return {
        "host": variables["WORDPRESS_DB_HOST"],
        "usuario": variables["WORDPRESS_DB_USER"],
        "password": variables["WORDPRESS_DB_PASSWORD"],
        "base": variables["WORDPRESS_DB_NAME"],
        "_password": variables["WORDPRESS_DB_PASSWORD"],
        "_contenido": "".join(f"{k}={variables[k]}\n" for k in claves),
    }


def volumen_del_sitio(info: dict) -> str:
    """El volumen montado en /var/www/html. Si no es un volumen, no hay caso."""
    for montaje in info.get("Mounts") or []:
        if montaje.get("Destination") == "/var/www/html":
            if montaje.get("Type") != "volume" or not montaje.get("Name"):
                raise Fallo(
                    "el sitio no tiene un volumen de Docker en /var/www/html "
                    f"(es de tipo {montaje.get('Type')}). Este script solo sabe leer un volumen."
                )
            return str(montaje["Name"])
    raise Fallo("el contenedor no tiene nada montado en /var/www/html, no parece un sitio WordPress")


def prefijo_de_tablas(env: dict[str, str], db: str, usuario: str, password: str, base: str) -> str:
    """Descubre el prefijo real de tablas en vez de suponer 'wp_'.

    Un sitio puede tener sus tablas en wp_2_options y seguir siendo perfectly
    valido. Suponer el prefijo es la forma de escribir en la tabla equivocada
    sin enterarse.

    Se pide la lista entera de tablas y se filtra aca, en vez de usar
    `SHOW TABLES LIKE '%_users'`. Dos razones: el `_` de un LIKE es comodin
    de un caracter, asi que `%_users` tambien matchea `xxusers`; y el escape
    `\\` tiene que atravesar tres capas de shell para llegar entero, y es
    facil que no llegue. Filtrar en Python no tiene ninguno de los dos
    problemas.

    OJO, y esta es la parte que ya se rompio una vez: el sufijo que se saca es
    `users` (5 letras), NO `_users` (6). "wp_users" tiene 8 caracteres; quitar
    los ultimos 6 deja "wp", y el prefijo real es "wp_". Un error de uno aca
    produce una sentencia contra una tabla que no existe.
    """
    tablas = pedir(
        env,
        f"docker exec {db} mariadb -u{usuario} -p{password} {base} -N -B -e 'SHOW TABLES'",
        secretos=(password,),
    )
    prefijos = sorted({t[: -len("users")] for t in (line.strip() for line in tablas.splitlines()) if t.endswith("users")})
    if not prefijos:
        raise Fallo("no se encontro ninguna tabla que termine en 'users': el sitio no esta instalado")
    if len(prefijos) > 1:
        raise Fallo(f"hay mas de un prefijo de tablas ({', '.join(prefijos)}); hay que elegir a mano")
    return prefijos[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("contenedor", help="nombre del contenedor de WordPress")
    parser.add_argument("--password", help="la nueva contraseña. Si no se pasa, se genera una")
    parser.add_argument("--usuario", default="admin", help="login de WordPress (default: admin)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true", help="muestra que haria y no cambia nada")
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    try:
        info = leer_inspect(env, args.contenedor)
        if not info.get("State", {}).get("Running"):
            raise Fallo(f"el contenedor {args.contenedor} no esta corriendo")

        conn = conexion_de_la_base(info)
        db, usuario, password, base = conn["host"], conn["usuario"], conn["password"], conn["base"]
        volumen = volumen_del_sitio(info)

        # Que la base este viva es un requisito, no una casualidad: este metodo
        # no apaga nada para poder trabajar.
        estado = pedir_palabras(env, f"docker inspect --format '{{{{.State.Running}}}}' {db}")
        if not estado or estado[0] != "true":
            raise Fallo(
                f"la base {db} no esta corriendo. Si el sitio esta caido, levanta la base, "
                "cambiamos la fila y la volves a bajar: no hay trafico que Preserve."
            )

        prefijo = prefijo_de_tablas(env, db, usuario, password, base)
        tapar = (password,)

        if not RE_SQL_SEGURO.match(args.usuario):
            raise Fallo(f"el usuario {args.usuario!r} no es seguro para meter en SQL")

        nueva = args.password or ("p-" + secrets.token_urlsafe(15))
        print(f"Sitio:     {args.contenedor}  (volumen {volumen})")
        print(f"Base:      {db}/{base}  usuario={usuario}  prefijo={prefijo}users")
        print(f"WordPress: {args.usuario}")

        if args.dry_run:
            print("\n--dry-run: no se cambia nada.")
            return 0

        hash_nuevo = hash_de_password(env, info, conn, db, nueva)
        sql = f"SELECT user_pass FROM {prefijo}users WHERE user_login='{args.usuario}'"
        antes = pedir(env, f"docker exec {db} mariadb -u{usuario} -p{password} {base} -N -B -e \"{sql}\"", secretos=tapar).strip()
        if not antes:
            raise Fallo(f"no existe el usuario {args.usuario!r} en {prefijo}users")

        pedir(
            env,
            f"docker exec {db} mariadb -u{usuario} -p{password} {base} -e "
            f"\"UPDATE {prefijo}users SET user_pass='{para_sql(hash_nuevo)}' WHERE user_login='{args.usuario}'\"",
            secretos=tapar,
        )
        despues = pedir(env, f"docker exec {db} mariadb -u{usuario} -p{password} {base} -N -B -e \"{sql}\"", secretos=tapar).strip()

        if despues != hash_nuevo or despues == antes:
            raise Fallo("el UPDATE no quedo aplicado como se esperaba")

        print(f"\nContraseña de {args.usuario} cambiada. El sitio nunca se detuvo.")
        print(f"NUEVA CONTRASEÑA: {nueva}")
        print("Se muestra una sola vez y no se guarda en ningun lado.")
        return 0

    except Fallo as exc:
        print(f"\nFALLO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
