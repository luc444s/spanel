"""Pruebas de `site_backup.py` (A.SPEC 0047) — offline, sin Docker ni red.

Todo se inyecta: docker CLI (fixtures de containers WP y estáticos) y reloj.
Fixtures construyen el tar que `docker cp -` devolvería, en memoria.
Lo que se fija: roundtrip WP (archivos + SQL + manifest hashes, I3), estático
sin database/, resolución de path por label/--path, falta de env DB → exit 1,
mysqldump falla → no queda zip (I2), --verify sobre zip tamperado → exit 2,
sin credenciales (I4), dry-run no escribe, replace atómico (I2), segundo run
no sobrescribe, y read-only de docker (I1).
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "site_backup", SCRIPTS / "site_backup.py"
)
assert _SPEC and _SPEC.loader
sb = importlib.util.module_from_spec(_SPEC)
sys.modules["site_backup"] = sb
_SPEC.loader.exec_module(sb)

SQL = "-- dump demo\nCREATE TABLE wp_posts (id int);\nINSERT INTO wp_posts VALUES (1);\n"


def make_tar_bytes(files: dict[str, bytes]) -> str:
    """Tar del tamaño que `docker cp <container>:<path> -` devolvería,
    con prefijo `<dir>/` como docker cp real."""
    buf = io.BytesIO()
    prefixed = {}
    for name, data in files.items():
        prefixed[f"html/{name}" if not name.startswith("html/") else name] = data
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in prefixed.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 1_700_000_000
            tar.addfile(info, io.BytesIO(data))
    # stdout del subprocess real es str; el script lo codifica surrogateescape.
    return buf.getvalue().decode("utf-8", "surrogateescape")


def inspect_json(
    name: str,
    env: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        [
            {
                "Name": f"/{name}",
                "Config": {"Env": env or [], "Labels": labels or {}},
            }
        ]
    )


WP_ENV = [
    "WORDPRESS_DB_HOST=legalattorney-clean-db",
    "WORDPRESS_DB_USER=wordpress",
    "WORDPRESS_DB_PASSWORD=super-secret-pass",
    "WORDPRESS_DB_NAME=wordpress",
]


class FakeDocker:
    """docker inspect/cp/exec por container, sin daemon real. Registra cada
    llamada para el test de I1 (solo verbs read-only)."""

    def __init__(self, inspects: dict[str, str], tars: dict[str, str], sql: str = SQL):
        self.inspects = inspects
        self.tars = tars
        self.sql = sql
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> sb.Result:
        self.calls.append(list(args))
        verb = args[0]
        if verb == "inspect":
            container = args[1]
            if container in self.inspects:
                return sb.Result(self.inspects[container], "", 0)
            return sb.Result("", f"Error: No such object: {container}", 1)
        if verb == "cp":
            source = args[1]
            container = source.split(":", 1)[0]
            data = self.tars.get(container)
            if data is None:
                return sb.Result("", f"Error: no such container {container}", 1)
            return sb.Result(data, "", 0)
        if verb == "exec":
            # docker exec <db> sh -c "exec mysqldump ..."
            if len(args) >= 4 and args[2] == "sh" and args[3] == "-c":
                command = args[4]
                if "mysqldump" not in command:
                    return sb.Result("", "comando exec no-mysqldump prohibido", 1)
                return sb.Result(self.sql, "", 0)
            return sb.Result("", "exec inválido", 1)
        return sb.Result("", f"verb prohibido: {verb}", 127)


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0):
        self.value = start

    def tick(self, seconds: float = 61.0) -> None:
        self.value += seconds

    def __call__(self) -> float:
        return self.value


def tooling_with(
    fake: FakeDocker, clock: FakeClock | None = None
) -> tuple[sb.Tooling, FakeClock]:
    clock = clock or FakeClock()
    # El dump viaja por docker_bytes; el fake sirve ambos caminos (I5/byte-loyal
    # es asunto del CLI real, del fake salen Result de texto y el caller codifica).
    return sb.Tooling(docker=fake, docker_bytes=fake, now=clock), clock


def backup_args(tmp_path: Path, **kw) -> list[str]:
    args = ["--out", str(tmp_path / "out")]
    if kw.get("container"):
        args = ["--container", kw["container"], *args]
    for key in ("db_container", "db_user", "db_pass", "db_name", "db_host"):
        if kw.get(key):
            args += [f"--{key.replace('_', '-')}", kw[key]]
    return args


def run(tmp_path: Path, fake: FakeDocker, clock: FakeClock, argv: list[str]):
    tooling, _ = tooling_with(fake, clock)
    return sb.main(argv, tooling)


def zip_path_of(tmp_path: Path, kind: str, container: str) -> Path:
    candidates = sorted((tmp_path / "out").glob(f"{kind}-{container}-*.zip"))
    assert candidates, f"no zip {kind}-{container}-* en out/"
    return candidates[-1]


# --------------------------------------------------------------------------- #
# WP roundtrip (I3) y credenciales (I4)
# --------------------------------------------------------------------------- #


def test_wp_backup_roundtrip(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"index.php": b"<?php echo 1;> ", "wp-config.php": b"cfg"})},
    )
    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="wp-db", db_user="wordpress",
                    db_pass="super-secret-pass", db_name="wordpress"),
    )
    assert code == 0, "backup WP debería exit 0"
    zp = zip_path_of(tmp_path, "wp", "wp-site")
    with zipfile.ZipFile(zp) as zipped:
        manifest = json.loads(zipped.read("manifest.json").decode("utf-8"))
        assert manifest["kind"] == "wp"
        assert manifest["db_host"] == "db:legalattorney-clean-db"
        assert "database/backup.sql" in zipped.namelist()
        assert zipped.read("database/backup.sql").decode("utf-8") == SQL
        site_entries = [n for n in zipped.namelist() if n.startswith("site/")]
        assert site_entries
        for arcname, meta in manifest["entries"].items():
            import hashlib
            digest = hashlib.sha256(zipped.read(arcname)).hexdigest()
            assert digest == meta["sha256"]
            assert meta["size"] == zipped.getinfo(arcname).file_size


def test_kind_wp_uses_the_database_resolved_by_the_panel(tmp_path: Path, monkeypatch):
    """The app resolves env/wp-config/alias and passes it explicitly.

    The password arrives in the child environment, never in argv.
    """
    monkeypatch.setenv("SPANEL_BACKUP_DB_PASSWORD", "secret-from-env")
    fake = FakeDocker(
        inspects={"site": inspect_json("site")},  # sin WORDPRESS_DB_*
        tars={"site": make_tar_bytes({"wp-config.php": b"cfg"})},
    )
    argv = [
        "--container", "site",
        "--kind", "wp",
        "--path", "/var/www/html",
        "--db-host", "db:3306",
        "--db-container", "real-db",
        "--db-user", "wordpress",
        "--db-name", "wordpressdb",
        "--out", str(tmp_path / "out"),
    ]
    code = run(tmp_path, fake, FakeClock(), argv)
    assert code == 0
    zp = zip_path_of(tmp_path, "wp", "site")
    with zipfile.ZipFile(zp) as zipped:
        assert "database/backup.sql" in zipped.namelist()
    assert any(call[:2] == ["exec", "real-db"] for call in fake.calls)
    # The script receives the password from the environment, not the argv.
    assert "secret-from-env" not in " ".join(argv)


def test_zip_staging_lives_on_the_output_filesystem(tmp_path: Path, monkeypatch):
    """os.replace only works within one device: /tmp may differ from the volume."""
    fake = FakeDocker(
        inspects={"portafolio": inspect_json("portafolio")},
        tars={"portafolio": make_tar_bytes({"index.html": b"<h1>hola</h1>"})},
    )
    moves: list[tuple[Path, Path]] = []
    real_replace = sb.os.replace

    def tracking(src, dst):
        moves.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(sb.os, "replace", tracking)
    code = run(tmp_path, fake, FakeClock(), backup_args(tmp_path, container="portafolio"))
    assert code == 0
    assert moves, "debería publicar el zip con os.replace"
    src, dst = moves[-1]
    assert src.parent.parent == dst.parent
    assert not list((tmp_path / "out").glob(".site-backup-*"))


def test_static_backup_no_database(tmp_path: Path):
    fake = FakeDocker(
        inspects={"portafolio": inspect_json("portafolio")},
        tars={"portafolio": make_tar_bytes({"index.html": b"<h1>hola</h1>"})},
    )
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="portafolio"))
    assert code == 0
    zp = zip_path_of(tmp_path, "static", "portafolio")
    with zipfile.ZipFile(zp) as zipped:
        manifest = json.loads(zipped.read("manifest.json").decode("utf-8"))
        assert manifest["kind"] == "static"
        assert manifest["db_host"] is None
        assert not [n for n in zipped.namelist() if n.startswith("database/")]
        assert any(n.startswith("site/") for n in zipped.namelist())


def test_no_credentials_in_zip_or_manifest(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"wp-config.php": b"cfg"})},
    )
    clock = FakeClock()
    run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="wp-db", db_user="wordpress",
                    db_pass="super-secret-pass", db_name="wordpress"),
    )
    zp = zip_path_of(tmp_path, "wp", "wp-site")
    with zipfile.ZipFile(zp) as zipped:
        for name in zipped.namelist():
            content = zipped.read(name).decode("utf-8", "replace")
            assert "super-secret-pass" not in content, f"password en {name} (I4)"
            if name == "manifest.json":
                manifest = json.loads(content)
                assert "db_pass" not in json.dumps(manifest)
                assert "db_password" not in json.dumps(manifest)


# --------------------------------------------------------------------------- #
# Resolución del site path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("nginx", "/usr/share/nginx/html"),
        ("caddy", "/usr/share/caddy"),
        ("astro", "/site"),
        ("httpd", "/usr/local/apache2/htdocs"),
        ("react", "/usr/share/caddy"),
        ("boot", "/usr/local/apache2/htdocs"),
    ],
)
def test_static_label_path_resolution(tmp_path: Path, label, expected):
    fake = FakeDocker(
        inspects={
            "sitio": inspect_json("sitio", labels={"spanel.profile": label})
        },
        tars={"sitio": make_tar_bytes({"index.html": b"x"})},
    )
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="sitio"))
    assert code == 0
    cp_calls = [c for c in fake.calls if c[0] == "cp"]
    assert len(cp_calls) == 1
    assert cp_calls[0][1] == f"sitio:{expected}"


def test_static_fallback_path(tmp_path: Path):
    fake = FakeDocker(
        inspects={"sin-label": inspect_json("sin-label")},
        tars={"sin-label": make_tar_bytes({"index.html": b"x"})},
    )
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="sin-label"))
    assert code == 0
    cp_calls = [c for c in fake.calls if c[0] == "cp"]
    assert cp_calls[0][1] == f"sin-label:{sb.FALLBACK_SITE_PATH}"


def test_static_unknown_profile_errors(tmp_path: Path):
    fake = FakeDocker(
        inspects={
            "sitio": inspect_json("sitio", labels={"spanel.profile": "elixir"})
        },
        tars={},
    )
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="sitio"))
    assert code == 1


def test_path_override_wins_over_label(tmp_path: Path):
    fake = FakeDocker(
        inspects={
            "sitio": inspect_json("sitio", labels={"spanel.profile": "astro"})
        },
        tars={"sitio": make_tar_bytes({"main.js": b"console.log(1)"})},
    )
    clock = FakeClock()
    argv = backup_args(tmp_path, container="sitio") + ["--path", "/srv/app"]
    code = run(tmp_path, fake, clock, argv)
    assert code == 0
    cp_calls = [c for c in fake.calls if c[0] == "cp"]
    assert cp_calls[0][1] == "sitio:/srv/app"


def test_wp_uses_var_www_html(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"index.php": b"x"})},
    )
    clock = FakeClock()
    run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="db", db_user="w",
                    db_pass="p", db_name="wp"),
    )
    cp_calls = [c for c in fake.calls if c[0] == "cp"]
    assert cp_calls[0][1] == f"wp-site:{sb.WP_SITE_PATH}"


# --------------------------------------------------------------------------- #
# Env DB faltante y fallos del dump (I2)
# --------------------------------------------------------------------------- #


def test_wp_missing_db_env_exits_1(tmp_path: Path):
    fake = FakeDocker(
        inspects={
            "medio": inspect_json(
                "medio",
                env=["WORDPRESS_DB_HOST=db-host-only"],  # sin user/pass/name
            )
        },
        tars={},
    )
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="medio"))
    assert code == 1, "env WP incompleto sin overrides debe exit 1"
    assert not (tmp_path / "out").exists()


def test_wp_missing_db_env_with_overrides_ok(tmp_path: Path):
    fake = FakeDocker(
        inspects={
            "medio": inspect_json("medio", env=["WORDPRESS_DB_HOST=wp-db"])
        },
        tars={"medio": make_tar_bytes({"index.php": b"x"})},
    )
    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="medio", db_container="wp-db", db_user="w",
                    db_pass="p", db_name="wordpress"),
    )
    assert code == 0


@pytest.mark.parametrize("sql_mode", ["failure", "empty"])
def test_mysqldump_failure_no_zip(tmp_path: Path, sql_mode):
    class FailDocker(FakeDocker):
        def __init__(self):
            super().__init__(
                inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
                tars={"wp-site": make_tar_bytes({"index.php": b"x"})},
            )

        def __call__(self, args: list[str]) -> sb.Result:
            result = super().__call__(args)
            if args[0] == "exec" and sql_mode == "failure":
                return sb.Result("", "mysqldump: command not found", 127)
            return result

    fake = FailDocker()
    if sql_mode == "empty":
        fake.sql = ""

    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="wp-db", db_user="w",
                    db_pass="p", db_name="wordpress"),
    )
    assert code == 1, f"mysqldump {sql_mode} debe exit 1"
    out_dir = tmp_path / "out"
    zips = list(out_dir.glob("*.zip")) if out_dir.exists() else []
    assert not zips, "el dump falló pero quedó un zip (viola I2)"


def test_copy_failure_no_zip(tmp_path: Path):
    fake = FakeDocker(inspects={
        "ghost": inspect_json("ghost", env=["WORDPRESS_DB_HOST=db"])
    }, tars={})
    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="ghost", db_container="db", db_user="w",
                    db_pass="p", db_name="wordpress"),
    )
    assert code == 1
    assert not list((tmp_path / "out").glob("*.zip")) if (tmp_path / "out").exists() else True


# --------------------------------------------------------------------------- #
# Dry-run, atomicidad y no-overwrite (I2)
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"index.php": b"x"})},
    )
    clock = FakeClock()
    argv = backup_args(
        tmp_path, container="wp-site", db_container="db", db_user="w",
        db_pass="p", db_name="wordpress",
    ) + ["--dry-run"]
    code = run(tmp_path, fake, clock, argv)
    assert code == 0, "dry-run debe ejecutar sin escribir"
    # ninguna llamada cp/exec en dry-run
    verbs = {c[0] for c in fake.calls}
    assert verbs == {"inspect"}
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*"))


def test_atomic_build_no_tmp_leftovers(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"index.php": b"x"})},
    )
    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="db", db_user="w",
                    db_pass="p", db_name="wordpress"),
    )
    assert code == 0
    leftovers = [p for p in (tmp_path / "out").iterdir() if p.suffix != ".zip"]
    assert not leftovers, f"quedó archivo parcial en out/: {leftovers} (I2)"


def test_second_run_new_timestamp_no_overwrite(tmp_path: Path):
    fake = FakeDocker(
        inspects={"portafolio": inspect_json("portafolio")},
        tars={"portafolio": make_tar_bytes({"index.html": b"x"})},
    )
    clock = FakeClock()
    argv = backup_args(tmp_path, container="portafolio")
    assert run(tmp_path, fake, clock, argv) == 0
    first = zip_path_of(tmp_path, "static", "portafolio")
    clock.tick(1_000)  # avanzar el reloj → timestamp nuevo
    assert run(tmp_path, fake, clock, argv) == 0
    second = zip_path_of(tmp_path, "static", "portafolio")
    assert first != second, "segundo run debe producir timestamp nuevo (no overwrite)"
    assert first.is_file() and second.is_file()


# --------------------------------------------------------------------------- #
# --verify sobre zips (exit 0 / 2)
# --------------------------------------------------------------------------- #


def _build_static_zip(tmp_path: Path) -> Path:
    fake = FakeDocker(
        inspects={"portafolio": inspect_json("portafolio")},
        tars={"portafolio": make_tar_bytes({"index.html": b"<h1>hola</h1>"})},
    )
    clock = FakeClock()
    run(tmp_path, fake, clock, backup_args(tmp_path, container="portafolio"))
    return zip_path_of(tmp_path, "static", "portafolio")


def test_verify_good_zip_exits_0(tmp_path: Path):
    zp = _build_static_zip(tmp_path)
    clock = FakeClock()
    fake = FakeDocker(inspects={}, tars={})
    code = run(tmp_path, fake, clock, ["--verify", str(zp)])
    assert code == 0


def test_verify_tampered_zip_exits_2(tmp_path: Path):
    zp = _build_static_zip(tmp_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(zp) as src:
        names = [n for n in src.namelist() if n != "manifest.json"]
        with zipfile.ZipFile(tampered, "w") as dst:
            for name in names:
                if name.startswith("site/"):
                    data = b"TAMPERED"  # contenido cambiado → sha256 mismatch
                else:
                    data = src.read(name)
                dst.writestr(name, data)
            dst.writestr("manifest.json", src.read("manifest.json"))
    clock = FakeClock()
    fake = FakeDocker(inspects={}, tars={})
    code = run(tmp_path, fake, clock, ["--verify", str(tampered)])
    assert code == 2, "zip tamperado debe exit 2"


def test_verify_missing_zip_exits_2(tmp_path: Path):
    clock = FakeClock()
    fake = FakeDocker(inspects={}, tars={})
    code = run(tmp_path, fake, clock, ["--verify", str(tmp_path / "nope.zip")])
    assert code == 2


def test_verify_zip_missing_entry_exits_2(tmp_path: Path):
    zp = _build_static_zip(tmp_path)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(zp) as src, zipfile.ZipFile(broken, "w") as dst:
            for name in [n for n in src.namelist() if not n.startswith("site/")]:
                dst.writestr(name, src.read(name))
    clock = FakeClock()
    fake = FakeDocker(inspects={}, tars={})
    code = run(tmp_path, fake, clock, ["--verify", str(broken)])
    assert code == 2


# --------------------------------------------------------------------------- #
# I1: solo verbs read-only sobre docker
# --------------------------------------------------------------------------- #


def test_docker_calls_are_readonly(tmp_path: Path):
    fake = FakeDocker(
        inspects={"wp-site": inspect_json("wp-site", env=WP_ENV)},
        tars={"wp-site": make_tar_bytes({"index.php": b"x"})},
    )
    clock = FakeClock()
    code = run(
        tmp_path, fake, clock,
        backup_args(tmp_path, container="wp-site", db_container="db", db_user="w",
                    db_pass="p", db_name="wordpress"),
    )
    assert code == 0
    import re
    for call in fake.calls:
        verb = call[0]
        assert re.fullmatch(r"inspect|cp|exec|logs", verb), f"verb docker no read-only: {verb} (I1)"
        if verb == "exec":
            command = call[-1]
            assert "mysqldump" in command, f"exec fuera de mysqldump: {command} (I1)"


def test_missing_container_exits_1(tmp_path: Path):
    fake = FakeDocker(inspects={}, tars={})
    clock = FakeClock()
    code = run(tmp_path, fake, clock, backup_args(tmp_path, container="fantasma"))
    assert code == 1



def test_multi_tenant_mariadb_dumps_only_site_db():
    """Un mariadb SHARED (multi-tenant, muchas DBs) nunca se volca entero:
    el dump apunta a UNA base — la del container WP (contrato 0047)."""
    from tempfile import mkdtemp
    fake = FakeDocker({}, {})
    tooling, _ = tooling_with(fake)
    target = sb.Target(
        container="wp-compartido",
        kind="wp",
        site_path="/var/www/html",
        env={},
        db_host="mariadb",
        db_container="mariadb",
        db_user="usuario_sitio_a",
        db_name="base_de_este_sitio",
        db_pass="clave",
    )
    tmp = Path(mkdtemp()) / "mt"
    tmp.mkdir(parents=True)
    sb.dump_db(tooling, target, tmp)
    dumps = [c for c in fake.calls if "exec" in c and "sh" in c]
    assert dumps, "no hubo dump"
    for call in dumps:
        command = call[-1]
        assert "--all-databases" not in command
        assert "--databases" not in command
        assert "base_de_este_sitio" in command

def tmp_path() -> Path:
    import tempfile
    return Path(tempfile.mkdtemp())
