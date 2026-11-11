#!/usr/bin/env python3
"""Cambiar la contraseña de un usuario de un WordPress, o listar sus usuarios.

Dos operaciones sobre un sitio que YA está corriendo:

    --listar    muestra ID, login, email, estado y rol, y no escribe nada.
    (default)   cambia la contraseña de un usuario, sin apagar nada.

El reset es la respuesta a una pregunta concreta: si el sistema no guarda las
contraseñas, ¿cómo entra un usuario que perdió la suya? Regenerando.

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
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --listar
    python3 scripts/reset_wp_password.py spanel-pwtest-wp
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --password 'la-nueva'
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --password 'la-nueva' --dry-run
    python3 scripts/reset_wp_password.py spanel-pwtest-wp --id 7 --password 'la-nueva'

A.SPEC 0011: un login NO es unico en la base (WordPress lo mantiene unico en
el codigo, pero el esquema permite repetirlo). Por eso el script resuelve el
login a UNA fila antes de escribir: si aparece en mas de una, falla y lista
los IDs en vez de elegir. `--id` saltea esa resolucion porque el ID es unico
por definicion, y es la salida cuando el login esta repetido.

Si no se pasa `--password`, se genera una y se imprime. Ojo: se imprime UNA vez
y no se guarda en ningun lado. Si se pierde, se regenera otra vez con este
mismo script.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_ROOT = REPO_ROOT / "vendor" / "systutor-core"
DEFAULT_ENV_FILE = CORE_ROOT / ".env"
# `plugins` lives in the core root; `systutor` lives in its `src`. Both go
# first so a checkout does not silently import an editable install that points
# at another clone.
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))

from plugins.hosting.backend.reset import (  # pyright: ignore[reportMissingImports]
    PHP_HASH_AUTOCOMPROBADO,
    RE_HASH_PHPASS,
    RE_HASH_WP,
    RE_SQL_SEGURO,
    Fallo,
    actualizar_hash,
    borrar_archivo_remoto,
    conexion_de_la_base,
    ejecutar_sql,
    escribir_archivo_remoto,
    filas_de_usuarios,
    hash_de_password,
    hash_valido,
    imprimir_usuarios,
    leer_hash,
    leer_inspect,
    para_sql,
    pedir,
    pedir_palabras,
    prefijo_de_tablas,
    red_de,
    redactar,
    resolver_objetivo,
    roles_desde_capacidades,
    run,
    usuarios_con_rol,
    validar_prefijo,
    validar_sql_seguro,
    volumen_del_sitio,
)
from plugins.hosting.backend.transport import (  # pyright: ignore[reportMissingImports]
    build_docker_argv,
    load_env_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("contenedor", help="WordPress container name")
    parser.add_argument("--password", help="the new password. If omitted, one is generated")
    parser.add_argument(
        "--usuario",
        default=None,
        help="WordPress login (default: admin, if --id is also omitted)",
    )
    parser.add_argument(
        "--id",
        type=int,
        default=None,
        help="wp_users row ID. Mutually exclusive with --usuario",
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true", help="show what it would do and change nothing")
    parser.add_argument(
        "--listar",
        action="store_true",
        help="show users with their role and write nothing",
    )
    args = parser.parse_args()

    # Los dos selectores juntos no son una precedencia: son una ambiguedad del
    # operador. Se rechaza antes de leer la base, para no tener nada que deshacer.
    if args.id is not None and args.usuario is not None:
        print(
            "FAILED: --id and --usuario are mutually exclusive: choose one.",
            file=sys.stderr,
        )
        return 1

    # Un flag de solo lectura que ignora en silencio un flag de escritura es una
    # trampa: se rechaza antes de la base, no se ignora.
    if args.listar and (
        args.password is not None or args.usuario is not None or args.id is not None
    ):
        print(
            "FAILED: --listar is read-only and cannot be combined with --password, --usuario or --id.",
            file=sys.stderr,
        )
        return 1

    env = load_env_file(args.env_file)
    try:
        info = leer_inspect(env, args.contenedor)
        if not info.get("State", {}).get("Running"):
            raise Fallo(f"container {args.contenedor} is not running")

        conn = conexion_de_la_base(info)
        db, usuario, password, base = conn["host"], conn["usuario"], conn["password"], conn["base"]
        # El listado solo necesita la base. No exige un volumen en
        # /var/www/html, asi tambien lista sitios con bind mount.
        volumen = None if args.listar else volumen_del_sitio(info)

        # Que la base este viva es un requisito, no una casualidad: este metodo
        # no apaga nada para poder trabajar.
        estado = pedir_palabras(env, f"docker inspect --format '{{{{.State.Running}}}}' {db}")
        if not estado or estado[0] != "true":
            raise Fallo(
                f"database {db} is not running. If the site is down, start the database, "
                "change the row, and take it down again: there is no traffic to preserve."
            )

        prefijo = prefijo_de_tablas(env, db, usuario, password, base)

        if args.listar:
            imprimir_usuarios(usuarios_con_rol(env, db, usuario, password, base, prefijo))
            return 0

        # Con --id el login no participa: el ID es unico por definicion. Sin
        # --id se resuelve el login, y el default "admin" solo existe cuando no
        # se paso ninguno de los dos flags (I6 de A.SPEC 0011).
        login = args.usuario if args.usuario is not None else (None if args.id is not None else "admin")
        if login is not None and not RE_SQL_SEGURO.match(login):
            raise Fallo(f"user {login!r} is not safe to put in SQL")

        # Se resuelve ANTES del hash: un login ambiguo o inexistente no cuesta
        # ningun contenedor y no bootea WordPress (I3 de A.SPEC 0011).
        objetivo = resolver_objetivo(
            env,
            db,
            usuario,
            password,
            base,
            prefijo,
            id_usuario=args.id,
            login=login,
        )

        print(f"Site:      {args.contenedor}  (volume {volumen})")
        print(f"Database:  {db}/{base}  user={usuario}  prefix={prefijo}users")
        print(f"WordPress: {objetivo['login']} (ID {objetivo['id']})")

        if args.dry_run:
            print(f"\n--dry-run: nothing is changed. It would target ID {objetivo['id']}.")
            return 0

        nueva = args.password or ("p-" + secrets.token_urlsafe(15))
        hash_nuevo = hash_de_password(env, info, conn, db, nueva)
        # Ida y vuelta SIEMPRE por ID: el login ya se uso solo para resolver.
        antes = leer_hash(env, db, usuario, password, base, prefijo, objetivo["id"])
        if not antes:
            raise Fallo(f"user {objetivo['login']!r} does not exist in {prefijo}users")

        actualizar_hash(env, db, usuario, password, base, prefijo, objetivo["id"], hash_nuevo)
        despues = leer_hash(env, db, usuario, password, base, prefijo, objetivo["id"])

        if despues != hash_nuevo or despues == antes:
            raise Fallo("the UPDATE was not applied as expected")

        print(
            f"\nPassword of {objetivo['login']} (ID {objetivo['id']}) changed. "
            "The site was never stopped."
        )
        print(f"NEW PASSWORD: {nueva}")
        print("It is shown only once and is not stored anywhere.")
        return 0

    except Fallo as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "PHP_HASH_AUTOCOMPROBADO",
    "RE_HASH_PHPASS",
    "RE_HASH_WP",
    "RE_SQL_SEGURO",
    "Fallo",
    "actualizar_hash",
    "borrar_archivo_remoto",
    "build_docker_argv",
    "conexion_de_la_base",
    "ejecutar_sql",
    "escribir_archivo_remoto",
    "filas_de_usuarios",
    "hash_de_password",
    "hash_valido",
    "imprimir_usuarios",
    "leer_hash",
    "leer_inspect",
    "load_env_file",
    "para_sql",
    "pedir",
    "pedir_palabras",
    "prefijo_de_tablas",
    "red_de",
    "redactar",
    "resolver_objetivo",
    "roles_desde_capacidades",
    "run",
    "usuarios_con_rol",
    "validar_prefijo",
    "validar_sql_seguro",
    "volumen_del_sitio",
]


if __name__ == "__main__":
    sys.exit(main())
