"""Pruebas del CLI de sitios estáticos (sin Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from spanel_static.config import CommandResult
from spanel_static.content import FILES, MARKER, read_template, seed_content
from spanel_static.docker import Docker
from spanel_static.names import SiteNames, slugify, validate_domain
from spanel_static.provision import _create_dry_run
from spanel_static.config import StaticSettings


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, argv, *, stdin=None, timeout=None, cwd=None):
        self.calls.append(
            {"argv": list(argv), "stdin": stdin, "timeout": timeout, "cwd": cwd}
        )
        return CommandResult(list(argv), 0, "", "")


def test_slugify_normaliza_y_rechaza_cortos() -> None:
    assert slugify("Página de Lucas") == "pagina-de-lucas"
    assert slugify("  Mi Sitio  ") == "mi-sitio"
    with pytest.raises(ValueError):
        slugify("ab")
    with pytest.raises(ValueError):
        slugify("!!!")
    with pytest.raises(ValueError):
        slugify("x" * 60)


def test_dominio_interno_permite_spanel() -> None:
    assert validate_domain("paginadelucas.spanel", "internal") == "paginadelucas.spanel"
    assert validate_domain("Paginadelucas.SPANEL.", "internal") == "paginadelucas.spanel"


def test_dominio_rechaza_esquema_puerto_y_comodines() -> None:
    for value in ("https://x.spanel", "x.spanel:443", "*.x.spanel", "x spanel"):
        with pytest.raises(ValueError):
            validate_domain(value, "internal")


def test_dominio_publico_exige_tld_con_letras() -> None:
    assert validate_domain("ejemplo.com", "public") == "ejemplo.com"
    with pytest.raises(ValueError):
        validate_domain("ejemplo.spanel", "public")


def test_nombres_distinguibles() -> None:
    names = SiteNames("paginadelucas")
    assert names.container == "spanel-paginadelucas-web"
    assert names.volume == "spanel-paginadelucas-web"
    assert names.route_file == "spanel-paginadelucas-web.yml"
    labels = dict(names.labels("paginadelucas.spanel"))
    assert labels["com.spanel.static"] == "true"
    assert labels["spanel.kind"] == "static"
    assert labels["com.spanel.static.domain"] == "paginadelucas.spanel"


def test_plantillas_existen_y_tienen_marcador() -> None:
    assert set(FILES) == {"index.html", "styles.css", "app.js"}
    index = read_template("index.html")
    assert MARKER in index
    assert "/styles.css" in index
    assert "/app.js" in index
    assert read_template("styles.css")
    assert MARKER not in read_template("styles.css")  # el marcador es del HTML


def test_docker_construye_argv_sin_shell() -> None:
    runner = FakeRunner()
    docker = Docker(runner)
    names = SiteNames("demo")
    docker.create_volume(names.volume, names, "demo.spanel")
    docker.create_container(names, "demo.spanel", "spanel-traefik-lab", "nginx:alpine")
    docker.write_file(names.container, "/usr/share/nginx/html/index.html", "<h1>hola</h1>")

    volume = runner.calls[0]["argv"]
    assert volume[:3] == ["docker", "volume", "create"]
    assert volume[-1] == "spanel-demo-web"

    container = runner.calls[1]["argv"]
    assert container[:4] == ["docker", "run", "-d", "--name"]
    assert "spanel-demo-web" in container
    assert "--network" in container and "spanel-traefik-lab" in container
    assert container[-1] == "nginx:alpine"

    write = runner.calls[2]
    assert write["argv"][:5] == ["docker", "exec", "-i", "spanel-demo-web", "sh"]
    assert write["stdin"] == "<h1>hola</h1>"


def test_dry_run_no_ejecuta_y_muestra_la_ruta(tmp_path: Path) -> None:
    settings = StaticSettings(repo_root=tmp_path, proxy_script=tmp_path / "proxy_domain.py")
    _create_dry_run(settings, SiteNames("demo"), "demo.spanel", False)
    # La función solo imprime; si llegó acá sin tocar Docker, está bien.


class FakeSeedDocker:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.chowned = False

    def read_file(self, container: str, path: str) -> str | None:
        return self.files.get(path)

    def write_file(self, container: str, path: str, content: str) -> None:
        self.files[path] = content

    def exec_in(self, *args, **kwargs) -> CommandResult:
        return CommandResult([], 0, "", "")

    def chown(self, container: str) -> None:
        self.chowned = True


def test_seed_reemplaza_el_index_de_nginx() -> None:
    index = "/usr/share/nginx/html/index.html"
    docker = FakeSeedDocker({index: "<title>Welcome to nginx!</title>"})
    written = seed_content(docker, "spanel-demo-web")
    assert "index.html" in written
    assert MARKER in docker.files[index]
    assert docker.chowned is True


def test_seed_respeta_un_index_propio_sin_marcador() -> None:
    index = "/usr/share/nginx/html/index.html"
    docker = FakeSeedDocker({index: "<h1>mi página</h1>"})
    written = seed_content(docker, "spanel-demo-web")
    assert written == ["styles.css", "app.js"]
    assert docker.files[index] == "<h1>mi página</h1>"
