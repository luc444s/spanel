"""Credential-safe database access through the DB container.

There is ONE helper that builds every database command (`build_db_command`)
and ONE helper that runs SQL (`ejecutar_sql`). No caller builds
`mariadb -p...` by hand.

Primary path: the DB container already exposes `MARIADB_USER` and
`MARIADB_PASSWORD`, so the client reads them from `MYSQL_PWD` inside the
container. Neither value is interpolated on the host.

Fallback: when the container does not expose those variables, the user and
password arrive over stdin and become a `--defaults-extra-file` inside the
container with mode 600, removed after the query. The password never becomes
an argv element, not on the laptop and not inside the container.

SQL also travels over stdin. A shell never parses it, so a hash with `$` does
not need shell escaping; `para_sql` still validates it because the statement
is built by string interpolation.
"""

from __future__ import annotations

import json
import re

from .transport import Fallo, pedir

# Lo que si o si va dentro del SQL: el usuario y el prefijo de tablas. Solo
# letras, digitos y guion bajo. Cualquier otra cosa se rechaza, en vez de
# escaparse: escapar bien es dificil de auditar, rechazar es trivial.
RE_SQL_SEGURO = re.compile(r"^[A-Za-z0-9_]{1,32}$")


def validar_sql_seguro(valor: str, *, etiqueta: str) -> str:
    """Reject a value outside `[A-Za-z0-9_]` before any SQL is built."""
    if not RE_SQL_SEGURO.match(valor):
        raise Fallo(
            f"{etiqueta} {valor!r} fue rechazado, no escapado: "
            "solo se aceptan letras, digitos y guion bajo (maximo 32)"
        )
    return valor


def validar_prefijo(prefijo: str) -> str:
    """The table prefix is validated with the same rule as the login."""
    return validar_sql_seguro(prefijo, etiqueta="el prefijo de tablas")


def build_db_command(db: str, base: str, *, batch: bool = True) -> str:
    """Build the command that runs `mariadb` inside the DB container.

    It does NOT take the password as text. Either the container has it in its
    environment, or it is read from stdin and written to a mode-600 defaults
    file inside the container.
    """
    flags = " -N -B" if batch else ""
    return (
        f"docker exec -i {db} sh -c '"
        'if [ -n "${MARIADB_USER:-}" ] && [ -n "${MARIADB_PASSWORD:-}" ]; then '
        f'MYSQL_PWD="$MARIADB_PASSWORD" exec mariadb -u "$MARIADB_USER" {base}{flags}; '
        "fi; "
        "IFS= read -r spanel_u; IFS= read -r spanel_p; "
        "spanel_f=$(mktemp); "
        "chmod 600 \"$spanel_f\"; "
        "printf '[client]\\nuser=%s\\npassword=%s\\n' \"$spanel_u\" \"$spanel_p\" "
        "> \"$spanel_f\"; "
        f"mariadb --defaults-extra-file=\"$spanel_f\" {base}{flags}; "
        "spanel_rc=$?; rm -f \"$spanel_f\"; exit $spanel_rc"
        "'"
    )


def _env_del_contenedor(env: dict[str, str], contenedor: str) -> dict[str, str]:
    """The container environment, or an empty dict if it cannot be read."""
    try:
        salida = pedir(env, f"docker inspect --format '{{{{json .Config.Env}}}}' {contenedor}")
        datos = json.loads(salida)
    except (Fallo, json.JSONDecodeError):
        return {}
    if not isinstance(datos, list):
        return {}
    entorno: dict[str, str] = {}
    for par in datos:
        if isinstance(par, str):
            clave, _, valor = par.partition("=")
            if clave.strip():
                entorno[clave.strip()] = valor
    return entorno


def credenciales_en_el_contenedor(env: dict[str, str], db: str) -> bool:
    """Does the DB container expose MARIADB_USER and MARIADB_PASSWORD?"""
    entorno = _env_del_contenedor(env, db)
    return bool(entorno.get("MARIADB_USER") and entorno.get("MARIADB_PASSWORD"))


def ejecutar_sql(
    env: dict[str, str],
    db: str,
    base: str,
    sentencia: str,
    *,
    usuario: str,
    password: str,
    batch: bool = True,
    secretos: tuple[str, ...] = (),
) -> str:
    """Run one statement over the credential-safe channel and return stdout."""
    comando = build_db_command(db, base, batch=batch)
    tapar = (password, *secretos)
    if credenciales_en_el_contenedor(env, db):
        return pedir(env, comando, secretos=tapar, entrada=sentencia)
    # Fallback: user and password go first, over stdin, never in argv.
    return pedir(env, comando, secretos=tapar, entrada=f"{usuario}\n{password}\n{sentencia}")


# ---------------------------------------------------------------------------
# Site inspection: where the database is, its network and its volume.
# ---------------------------------------------------------------------------


def leer_inspect(env: dict[str, str], contenedor: str) -> dict:
    return json.loads(pedir(env, f"docker inspect --format '{{{{json .}}}}' {contenedor}").strip())


def conexion_de_la_base(info: dict) -> dict[str, str]:
    """The database variables the WordPress container itself declares."""
    variables = dict(p.partition("=")[::2] for p in (info.get("Config", {}).get("Env") or []))
    claves = (
        "WORDPRESS_DB_HOST",
        "WORDPRESS_DB_USER",
        "WORDPRESS_DB_PASSWORD",
        "WORDPRESS_DB_NAME",
    )
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
    """The volume mounted at /var/www/html. If it is not a volume, no case."""
    for montaje in info.get("Mounts") or []:
        if montaje.get("Destination") == "/var/www/html":
            if montaje.get("Type") != "volume" or not montaje.get("Name"):
                raise Fallo(
                    "el sitio no tiene un volumen de Docker en /var/www/html "
                    f"(es de tipo {montaje.get('Type')}). Este script solo sabe leer un volumen."
                )
            return str(montaje["Name"])
    raise Fallo(
        "el contenedor no tiene nada montado en /var/www/html, no parece un sitio WordPress"
    )


def red_de(info: dict, db: str) -> str:
    """The network needed to reach the database.

    It comes from the WordPress container's own networks: the database is on
    the same network by definition, otherwise the site would have no database.
    """
    redes = list((info.get("NetworkSettings", {}).get("Networks") or {}).keys())
    if not redes:
        raise Fallo(
            "el contenedor de WordPress no esta en ninguna red, no se puede llegar a la base"
        )
    return redes[0]


# ---------------------------------------------------------------------------
# Queries. Same SQL as A.SPEC 0011; only the channel changed.
# ---------------------------------------------------------------------------


def prefijo_de_tablas(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
) -> str:
    """Discover the real table prefix instead of assuming 'wp_'.

    A site can have its tables in wp_2_options and still be perfectly valid.
    Assuming the prefix is how you write to the wrong table without noticing.

    The whole table list is requested and filtered here, instead of using
    `SHOW TABLES LIKE '%_users'`. Two reasons: the `_` in a LIKE is a
    one-character wildcard, so `%_users` also matches `xxusers`; and the
    escape `\\` has to cross three shell layers to arrive whole, and it is
    easy for it not to. Filtering in Python has neither problem.

    OJO, and this is the part that already broke once: the suffix removed is
    `users` (5 letters), NOT `_users` (6). "wp_users" has 8 characters;
    removing the last 6 leaves "wp", and the real prefix is "wp_". An off-by-
    one here produces a statement against a table that does not exist.
    """
    tablas = ejecutar_sql(
        env,
        db,
        base,
        "SHOW TABLES",
        usuario=usuario,
        password=password,
    )
    prefijos = sorted(
        {
            t[: -len("users")]
            for t in (line.strip() for line in tablas.splitlines())
            if t.endswith("users")
        }
    )
    if not prefijos:
        raise Fallo(
            "no se encontro ninguna tabla que termine en 'users': el sitio no esta instalado"
        )
    if len(prefijos) > 1:
        raise Fallo(
            f"hay mas de un prefijo de tablas ({', '.join(prefijos)}); hay que elegir a mano"
        )
    return validar_prefijo(prefijos[0])


def filas_de_usuarios(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
    prefijo: str,
    condicion: str,
) -> list[dict[str, str]]:
    """The rows of `prefijo`users that match `condicion`, already as data.

    `mariadb -N -B` returns text with tabs and no header. Turning it into rows
    is what allows deciding whether the login resolved to ONE row or several
    BEFORE writing, instead of sending a WHERE that may touch several.
    """
    salida = ejecutar_sql(
        env,
        db,
        base,
        f"SELECT ID, user_login, user_email FROM {prefijo}users WHERE {condicion}",
        usuario=usuario,
        password=password,
    )
    filas: list[dict[str, str]] = []
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        partes = linea.split("\t")
        if len(partes) < 3:
            raise Fallo(f"la base devolvio una fila que no se entiende: {linea[:80]!r}")
        filas.append(
            {
                "id": partes[0].strip(),
                "login": partes[1].strip(),
                "email": partes[2].strip(),
            }
        )
    return filas


# WordPress stores roles serialized by PHP inside usermeta:
#     a:1:{s:13:"administrator";b:1;}
# Every active one is extracted: a site can have more than one in the same
# array, and `b:0` means disabled.
RE_ROL = re.compile(r's:\d+:"([^"]+)";b:1;')


def roles_desde_capacidades(serializado: str) -> str:
    """Turn the serialized `capabilities` value into "administrator,editor".

    Without a capabilities row the string arrives empty, and that is NOT an
    error: it is a user without a role. It is shown as `-` so the output remains
    space-separable.
    """
    roles = RE_ROL.findall(serializado)
    return ",".join(roles) if roles else "-"


def usuarios_con_rol(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
    prefijo: str,
) -> list[dict[str, str]]:
    """The rows of `prefijo`users with their role, in a single query.

    The `LEFT JOIN` is on purpose: a user without a capabilities row has to
    appear anyway. The prefix is used in BOTH places that need it —the table
    and the metadata key— from the same discovered value: hardcoding `wp_` in
    the second works on the demo site and fails on any other.
    """
    salida = ejecutar_sql(
        env,
        db,
        base,
        f"SELECT u.ID, u.user_login, u.user_email, u.user_status, COALESCE(m.meta_value, '') "
        f"FROM {prefijo}users u "
        f"LEFT JOIN {prefijo}usermeta m "
        f"ON m.user_id = u.ID AND m.meta_key = '{prefijo}capabilities' "
        "ORDER BY u.ID",
        usuario=usuario,
        password=password,
    )
    filas: list[dict[str, str]] = []
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        partes = linea.split("\t")
        if len(partes) < 5:
            raise Fallo(f"la base devolvio una fila que no se entiende: {linea[:80]!r}")
        filas.append(
            {
                "id": partes[0].strip(),
                "login": partes[1].strip(),
                "email": partes[2].strip(),
                "estado": partes[3].strip(),
                "rol": roles_desde_capacidades(partes[4]),
            }
        )
    return filas
