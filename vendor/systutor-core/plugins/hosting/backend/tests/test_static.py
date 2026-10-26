"""Pure static names, labels, argv and initial content (A.SPEC 0031)."""

from __future__ import annotations

from plugins.hosting.backend.settings import HostingSettings
from plugins.hosting.backend.static import (
    CONTENT,
    LABEL_DOMAIN,
    LABEL_KIND,
    LABEL_MANAGED,
    LABEL_SITE,
    LABEL_STATIC,
    MARKER,
    WEB_ROOT,
    StaticNames,
    build_static_run_args,
    build_static_volume_args,
    files_root_for,
)


def test_names() -> None:
    names = StaticNames("paginadelucas")
    assert names.container == "spanel-paginadelucas-web"
    assert names.volume == names.container
    assert names.service == names.container


def test_labels_carry_site_tenant_and_kind() -> None:
    labels = dict(StaticNames("pagina").labels("t1", "pagina.spanel"))
    assert labels[LABEL_SITE] == "pagina"
    assert labels[LABEL_MANAGED] == "true"
    assert labels["spanel.tenant"] == "t1"
    assert labels[LABEL_KIND] == "static"
    assert labels[LABEL_STATIC] == "true"
    assert labels[LABEL_DOMAIN] == "pagina.spanel"


def test_volume_args_include_labels_and_name() -> None:
    args = build_static_volume_args(StaticNames("pagina"), "t1", "pagina.spanel")
    assert args[:2] == ["volume", "create"]
    assert "spanel-pagina-web" in args
    assert f"{LABEL_KIND}=static" in args


def test_run_args_include_network_volume_labels_and_image() -> None:
    settings = HostingSettings(network="proxy-lab", static_image="nginx:alpine")
    args = build_static_run_args(
        StaticNames("pagina"), settings, "t1", "pagina.spanel", traefik=True
    )
    assert args[0] == "run"
    assert args[args.index("--network") + 1] == "proxy-lab"
    assert f"spanel-pagina-web:{WEB_ROOT}" in args
    assert f"{LABEL_KIND}=static" in args
    assert "nginx:alpine" == args[-1]
    assert any("traefik.http.routers.pagina.rule" in item for item in args)


def test_run_args_without_traefik_keep_ownership_labels() -> None:
    settings = HostingSettings(network="proxy-lab")
    args = build_static_run_args(
        StaticNames("pagina"), settings, "t1", "pagina.spanel", traefik=False
    )
    assert not any("traefik." in item for item in args)
    assert f"{LABEL_SITE}=pagina" in args


def test_initial_content_has_marker_and_three_files() -> None:
    assert set(CONTENT) == {"index.html", "styles.css", "app.js"}
    assert MARKER in CONTENT["index.html"]
    assert "/styles.css" in CONTENT["index.html"]
    assert "/app.js" in CONTENT["index.html"]


def test_filemanager_root_depends_on_kind() -> None:
    assert files_root_for("static", "/var/www/html") == WEB_ROOT
    assert files_root_for("wordpress", "/var/www/html") == "/var/www/html"
