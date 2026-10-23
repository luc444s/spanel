"""The database command has to survive every shell layer it crosses.

The body of `sh -c` is single-quoted on purpose: the remote SSH shell removes
that layer and the container shell receives the body untouched. A single quote
inside the body closes it early, which is exactly how the fallback credentials
file ended up as one malformed line and `mariadb` fell back to root without a
password.
"""

from __future__ import annotations

from plugins.hosting.backend.database import build_db_command


def _cuerpo(comando: str) -> str:
    """What the container shell receives: the inside of the outer quotes."""
    return comando.split("sh -c '", 1)[1].rsplit("'", 1)[0]


def test_el_comando_no_anida_comillas_simples() -> None:
    comando = build_db_command("spanel-db", "wp_demo")
    assert comando.count("'") == 2, (
        "solo las dos comillas que envuelven el sh -c; cualquier otra cierra "
        "el bloque antes de tiempo"
    )


def test_el_printf_conserva_los_saltos_de_linea() -> None:
    cuerpo = _cuerpo(build_db_command("spanel-db", "wp_demo"))
    assert 'printf "[client]\\nuser=%s\\npassword=%s\\n"' in cuerpo


def test_la_password_llega_por_stdin() -> None:
    comando = build_db_command("spanel-db", "wp_demo")
    assert "IFS= read -r spanel_p" in comando
    assert "--defaults-extra-file=\"$spanel_f\"" in comando
