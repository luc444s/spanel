"""The database command has to survive every shell layer it crosses.

The body of `sh -c` is single-quoted on purpose: the remote SSH shell removes
that layer and the container shell receives the body untouched. A single quote
inside the body closes it early, which is exactly how the fallback credentials
file ended up as one malformed line and `mariadb` fell back to root without a
password.
"""

from __future__ import annotations

import pytest

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


def test_el_fallback_de_entorno_no_lee_stdin() -> None:
    """The environment variant is explicit and never consumes site credentials."""
    comando = build_db_command("spanel-db", "wp_demo", from_environment=True)
    assert 'MYSQL_PWD="$MARIADB_PASSWORD" exec mariadb -u "$MARIADB_USER"' in comando
    assert "IFS= read" not in comando
    assert comando.count("'") == 2


def test_las_credenciales_del_sitio_van_primero(monkeypatch) -> None:
    from plugins.hosting.backend import database

    calls: list[tuple[str, str | None]] = []

    def fake_pedir(_env, comando, *, secretos=(), entrada=None):
        calls.append((comando, entrada))
        return "ok"

    monkeypatch.setattr(database, "pedir", fake_pedir)
    monkeypatch.setattr(
        database, "credenciales_en_el_contenedor", lambda _env, _db: True
    )
    assert database.ejecutar_sql(
        {}, "db", "base", "SHOW TABLES", usuario="u", password="p"
    ) == "ok"
    assert calls == [
        (build_db_command("db", "base"), "u\np\nSHOW TABLES"),
    ]


def test_un_1045_reintenta_con_el_entorno_del_contenedor(monkeypatch) -> None:
    from plugins.hosting.backend import database

    calls: list[tuple[str, str | None]] = []

    def fake_pedir(_env, comando, *, secretos=(), entrada=None):
        calls.append((comando, entrada))
        if len(calls) == 1:
            raise database.Fallo("ERROR 1045 (28000): Access denied for user 'x'")
        return "ok"

    monkeypatch.setattr(database, "pedir", fake_pedir)
    monkeypatch.setattr(
        database, "credenciales_en_el_contenedor", lambda _env, _db: True
    )
    assert database.ejecutar_sql(
        {}, "db", "base", "SHOW TABLES", usuario="u", password="p"
    ) == "ok"
    assert calls[0][1] == "u\np\nSHOW TABLES"
    assert calls[1] == (
        build_db_command("db", "base", from_environment=True),
        "SHOW TABLES",
    )


def test_un_error_que_no_es_de_credenciales_no_se_reintenta(monkeypatch) -> None:
    from plugins.hosting.backend import database

    calls: list[str] = []

    def fake_pedir(_env, comando, *, secretos=(), entrada=None):
        calls.append(comando)
        raise database.Fallo("ERROR 1064: syntax error")

    monkeypatch.setattr(database, "pedir", fake_pedir)
    monkeypatch.setattr(
        database, "credenciales_en_el_contenedor", lambda _env, _db: True
    )
    with pytest.raises(database.Fallo):
        database.ejecutar_sql(
            {}, "db", "base", "SHOW TABLES", usuario="u", password="p"
        )
    assert len(calls) == 1
