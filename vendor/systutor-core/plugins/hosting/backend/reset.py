"""Target resolution and the single-row UPDATE.

`reset.py` may call `database.py` and `passwords.py`; no module imports
`scripts.*`. The CLI remains in `scripts/reset_wp_password.py` as a thin
wrapper: argparse, help text, output format and exit codes live there.
"""

from __future__ import annotations

from .database import (
    RE_ROL,
    RE_SQL_SEGURO,
    conexion_de_la_base,
    ejecutar_sql,
    filas_de_usuarios,
    leer_inspect,
    prefijo_de_tablas,
    red_de,
    roles_desde_capacidades,
    usuarios_con_rol,
    validar_prefijo,
    validar_sql_seguro,
    volumen_del_sitio,
)
from .passwords import (
    PHP_HASH_AUTOCOMPROBADO,
    RE_HASH_PHPASS,
    RE_HASH_WP,
    hash_de_password,
    hash_valido,
    para_sql,
)
from .transport import (
    Fallo,
    borrar_archivo_remoto,
    escribir_archivo_remoto,
    pedir,
    pedir_palabras,
    redactar,
    run,
)


def resolver_objetivo(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
    prefijo: str,
    *,
    id_usuario: int | None,
    login: str | None,
) -> dict[str, str]:
    """Turn the operator's selector into EXACTLY one row, or fail.

    It does not choose when there are several: the database allows duplicate
    logins, and guessing which one the operator meant is precisely the bug
    this script had when it updated every row with that login. The candidates
    are listed and the operator decides with `--id`.

    `login` must already be validated against `RE_SQL_SEGURO`: it is
    interpolated in the WHERE. `id_usuario` is an integer, so it cannot inject
    anything.
    """
    if id_usuario is not None:
        filas = filas_de_usuarios(
            env, db, usuario, password, base, prefijo, f"ID={int(id_usuario)}"
        )
        if not filas:
            raise Fallo(f"no existe ningun usuario con ID {id_usuario} en {prefijo}users")
        return filas[0]

    if login is None:
        raise Fallo("no se indico ni --id ni --usuario: no se puede elegir la fila")

    validar_sql_seguro(login, etiqueta="el usuario")
    filas = filas_de_usuarios(
        env, db, usuario, password, base, prefijo, f"user_login='{login}'"
    )
    if not filas:
        # El mismo mensaje que daba el script antes de A.SPEC 0011.
        raise Fallo(f"no existe el usuario {login!r} en {prefijo}users")
    if len(filas) > 1:
        candidatos = "\n".join(f"ID {fila['id']} {fila['login']} {fila['email']}" for fila in filas)
        raise Fallo(
            f"el login {login!r} aparece en {len(filas)} filas de {prefijo}users; "
            "no se elige ninguna. Elegi una con --id:\n"
            f"{candidatos}"
        )
    return filas[0]


def leer_hash(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
    prefijo: str,
    id_usuario: int | str,
) -> str:
    """Read the current hash for one row, ALWAYS by primary key."""
    return ejecutar_sql(
        env,
        db,
        base,
        f"SELECT user_pass FROM {prefijo}users WHERE ID={int(id_usuario)}",
        usuario=usuario,
        password=password,
    ).strip()


def actualizar_hash(
    env: dict[str, str],
    db: str,
    usuario: str,
    password: str,
    base: str,
    prefijo: str,
    id_usuario: int | str,
    hash_nuevo: str,
) -> None:
    """Update exactly one row, ALWAYS by primary key. Never by login."""
    ejecutar_sql(
        env,
        db,
        base,
        f"UPDATE {prefijo}users SET user_pass='{para_sql(hash_nuevo)}' WHERE ID={int(id_usuario)}",
        usuario=usuario,
        password=password,
        batch=False,
    )


def imprimir_usuarios(filas: list[dict[str, str]]) -> None:
    """An aligned table. The output does not carry a password column."""
    cabecera = ("ID", "LOGIN", "EMAIL", "ESTADO", "ROL")
    claves = ("id", "login", "email", "estado", "rol")
    cuerpo = [tuple(fila[clave] for clave in claves) for fila in filas]
    anchos = [
        max([len(cabecera[i])] + [len(fila[i]) for fila in cuerpo]) for i in range(len(cabecera))
    ]

    print("  ".join(valor.ljust(anchos[i]) for i, valor in enumerate(cabecera)).rstrip())
    for fila in cuerpo:
        print("  ".join(valor.ljust(anchos[i]) for i, valor in enumerate(fila)).rstrip())


__all__ = [
    "PHP_HASH_AUTOCOMPROBADO",
    "RE_HASH_PHPASS",
    "RE_HASH_WP",
    "RE_ROL",
    "RE_SQL_SEGURO",
    "Fallo",
    "actualizar_hash",
    "borrar_archivo_remoto",
    "conexion_de_la_base",
    "ejecutar_sql",
    "escribir_archivo_remoto",
    "filas_de_usuarios",
    "hash_de_password",
    "hash_valido",
    "imprimir_usuarios",
    "leer_hash",
    "leer_inspect",
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
