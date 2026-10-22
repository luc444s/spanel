"""Hash handling: the same functions WordPress uses, never a reimplementation.

`PHP_HASH_AUTOCOMPROBADO` runs inside `wordpress:cli`, calls
`wp_hash_password()` and then checks the result with `wp_check_password()`.
If the password did not arrive, or the hash does not validate it, the PHP
exits with code 1 instead of returning something that looks like a hash.
"""

from __future__ import annotations

import re

from .database import red_de, volumen_del_sitio
from .transport import (
    Fallo,
    borrar_archivo_remoto,
    escribir_archivo_remoto,
    pedir,
)

# How WordPress stores passwords, DEPENDING ON THE VERSION.
#
# Verified against WordPress 7.0.4 on 2026-09-27: `wp_hash_password()` returns
# a bcrypt with the `$wp$` identifier, 63 characters:
#     $wp$2y$10$fZJO6EwucQ9itU7lKtWLJuCVJX27uy1fxdXKgo/wcr7IRXRmyfmqy
#
# So it is NOT phpass. This regex accepts both families, in case there is an
# old WordPress with php 5:
#     6.8 and later -> $wp$  + bcrypt ($2y$10$...)
#     up to 6.7     -> $P$B  + portable phpass
RE_HASH_WP = re.compile(r"^\$wp\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
RE_HASH_PHPASS = re.compile(r"^\$P\$[./A-Za-z0-9]{31}$")


def hash_valido(valor: str) -> bool:
    return bool(RE_HASH_WP.match(valor) or RE_HASH_PHPASS.match(valor))


# The PHP that runs INSIDE wordpress:cli. It reads the password from its
# standard input, computes the hash with the SAME function WordPress uses, and
# then self-checks. If what it read does not correspond to the hash it
# computed, it warns and exits with code 1 instead of returning something that
# looks like a hash and is not.
#
# `STDIN` and not `php://stdin` because wp-cli rewrites the stream constant and
# `stream_get_contents("php://stdin")` receives a string where it expects a
# resource.
PHP_HASH_AUTOCOMPROBADO = (
    '$p = trim(stream_get_contents(STDIN));'
    'if ($p === "") { fwrite(STDERR, "FALLO: la contraseña llego vacia"); exit(1); }'
    "$h = wp_hash_password($p);"
    'if (!wp_check_password($p, $h)) { fwrite(STDERR, '
    '"FALLO: el hash no valida la contraseña"); exit(1); }'
    "echo $h;"
)


def para_sql(valor: str) -> str:
    """Leave a value ready to go inside a double-quoted statement.

    A WordPress hash starts with a dollar sign, and a shell would expand it as
    a variable: `$wp` and `$2y` are empty, and the database receives a mangled
    hash. That is why `$` is escaped.

    Why escaping only `$` is enough: the regex above already guarantees the
    hash only has dots, slashes, alphanumerics and dollar signs. No other
    character with meaning for a shell can arrive. The previous validation is
    what makes this escaping safe, and that is why the two go together.
    """
    if not hash_valido(valor):
        raise Fallo(
            f"esto no parece un hash de WordPress y no se manda a la base: {valor[:40]!r}"
        )
    return valor.replace("$", r"\$")


def hash_de_password(
    env: dict[str, str],
    info: dict,
    credenciales: dict[str, str],
    db: str,
    password: str,
) -> str:
    """Ask `wp_hash_password()` for the hash, which is what WordPress really uses.

    A throwaway container, with the site volume and the container network,
    running `wp eval`.

    Two details that were expensive to find, and both are the difference
    between working and not:

    1) The password enters through STDIN, not through a mounted file. It was
       tried with a 600 file and it did NOT work, silently: the container runs
       as www-data (uid 82), not root, so it could not read it;
       `file_get_contents` returned false, `trim(false)` was the empty string,
       and the hash that came out was the hash of the EMPTY string. A hash with
       the right shape that was good for nothing, and the script accepted it.

    2) That is why the PHP self-verifies: it computes the hash, then checks
       with `wp_check_password` that the hash corresponds to the password it
       read. If not, it exits with code 1 and a message. A silent error becomes
       noise.

    The database enters through `--env-file`, not `-e` on the command: the
    `wp-config.php` of the official image reads the database variables from the
    ENVIRONMENT at runtime, so without them WordPress looks for a database
    called `mysql` and fails.

    The site volume is READ-ONLY. `wp eval` loads all of WordPress, so with a
    writable volume this command could touch the site while it is serving it.
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
