"""A.SPEC 0015: the site config and its secret file, against a real Docker host.

The point of this suite is the file the panel writes on the Docker host:
`/opt/spanel/sites/<container>/wp.env`, mode 600, holding the WordPress
database secret. The API must let a tenant change whitelisted env values and
rotate the auth salts, and must never return the secret value.

Run with:

    SPANEL_LIVE_TESTS=1 python -m pytest scripts/tests/test_hosting_config_live.py -q

Every destructive action is limited to a `spanel-lab-*` site created for the
test and removed in the fixture's teardown.
"""

from __future__ import annotations

import os
import secrets
import shlex
import sys
from pathlib import Path

os.environ.setdefault("SPANEL_HOSTING_TRAEFIK", "false")
_core = Path(__file__).resolve().parents[2] / "vendor" / "systutor-core"
sys.path.insert(0, str(_core / "src"))
sys.path.insert(0, str(_core))

import pytest  # noqa: E402
from plugins.hosting.backend.config import env_path  # noqa: E402
from plugins.hosting.backend.inspection import inspect_container  # noqa: E402
from plugins.hosting.backend.models import HostingSite  # noqa: E402
from plugins.hosting.backend.provisioner import Provisioner, SiteNames  # noqa: E402
from plugins.hosting.backend.settings import get_hosting_settings  # noqa: E402
from plugins.hosting.backend.transport import (  # noqa: E402
    Fallo,
    build_docker_argv,
    leer_archivo_remoto,
    run,
)
from sqlalchemy import select  # noqa: E402
from systutor.kernel.audit.models import AuditLog  # noqa: E402
from test_hosting_api_live import LiveApi, _headers, live_api as live_api_fixture  # noqa: E402, F401

URL = "/api/v1/plugins/hosting/sites"


def _require_live() -> None:
    if os.getenv("SPANEL_LIVE_TESTS") != "1":
        pytest.skip("GAP: SPANEL_LIVE_TESTS=1 requerido")


def _mode_of(provisioner: Provisioner, path: str) -> str:
    """The octal mode of a file on the Docker host (GNU stat, then BSD)."""
    for template in ("stat -c %a {path}", "stat -f %Lp {path}"):
        result = run(
            build_docker_argv(template.format(path=shlex.quote(path)), env=provisioner.env),
            env=provisioner.env,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return "unknown"


def _auth_key(provisioner: Provisioner, names: SiteNames) -> str:
    return provisioner.command(
        "run",
        "--rm",
        "--network",
        provisioner.settings.network,
        "--volumes-from",
        names.wp,
        "wordpress:cli",
        "wp",
        "config",
        "get",
        "AUTH_KEY",
    ).strip()


def _wp_core_installed(provisioner: Provisioner, names: SiteNames, env_file: str) -> bool:
    # `wp core is-installed` prints nothing: its exit code is the answer.
    try:
        provisioner.command(
            "run",
            "--rm",
            "--network",
            provisioner.settings.network,
            "--volumes-from",
            names.wp,
            "--env-file",
            env_file,
            "wordpress:cli",
            "wp",
            "core",
            "is-installed",
        )
    except Fallo:
        return False
    return True


@pytest.fixture()
def lab(live_api_fixture: LiveApi):
    _require_live()
    live_api = live_api_fixture
    settings = get_hosting_settings()
    provisioner = Provisioner(settings)
    reference = inspect_container(provisioner.env, "spanel-demowp-wp")
    if reference is None:
        pytest.skip("GAP: no se encuentra la red de WordPress del laboratorio")
    networks = (reference.get("NetworkSettings") or {}).get("Networks") or {}
    if not networks:
        pytest.skip("GAP: la red existente del laboratorio no esta disponible")
    settings.network = next(iter(networks))
    settings.traefik = False
    for image in (settings.wp_image, settings.db_image, "wordpress:cli"):
        if not provisioner.exists("image", image):
            pytest.skip(f"GAP: falta la imagen {image}")

    slug = "lab-" + secrets.token_hex(4)
    names = SiteNames(slug)
    assert names.wp.startswith("spanel-lab-")
    response = live_api.client.post(
        URL,
        headers=_headers(live_api.token_a),
        json={
            "name": slug,
            "domain": f"{slug}.example.test",
            "admin_email": "admin@example.test",
        },
    )
    assert response.status_code == 201, response.text
    site_id = response.json()["site"]["id"]
    try:
        yield live_api, provisioner, names, site_id
    finally:
        with live_api.app.state.session_factory() as db:
            site = db.get(HostingSite, site_id)
        if site is not None:
            live_api.client.delete(f"{URL}/{site_id}", headers=_headers(live_api.token_a))
        for kind, name in (
            ("container", names.wp),
            ("container", names.db),
            ("volume", names.wp),
            ("volume", names.db),
        ):
            assert not provisioner.exists(kind, name), f"GAP: recurso huerfano {name}"
        # Never leave a lab secret behind even if the API delete failed.
        stable_dir = env_path(provisioner.settings, names.wp).rsplit("/", 1)[0]
        run(
            build_docker_argv(f"rm -rf {shlex.quote(stable_dir)}", env=provisioner.env),
            env=provisioner.env,
        )


def test_get_config_no_devuelve_el_secreto(lab) -> None:
    api, provisioner, names, site_id = lab
    response = api.client.get(f"{URL}/{site_id}/config", headers=_headers(api.token_a))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "PHP_MEMORY_LIMIT" in body["editable_keys"]
    assert "WORDPRESS_DB_PASSWORD" in body["secret_names"]
    assert "MYSQL_PWD" in body["secret_names"]
    assert "WORDPRESS_DB_PASSWORD" not in body["editable_env"]

    file_text = leer_archivo_remoto(provisioner.env, env_path(provisioner.settings, names.wp))
    assert file_text is not None, "GAP: el sync no escribio el env file estable"
    secret = None
    for line in file_text.splitlines():
        if line.startswith("WORDPRESS_DB_PASSWORD="):
            secret = line.split("=", 1)[1]
    assert secret, "GAP: el env file no contiene el secreto de la base"
    assert secret not in response.text, "FAIL: el secreto viajo en la respuesta"


def test_el_archivo_de_secretos_es_600(lab) -> None:
    api, provisioner, names, site_id = lab
    # Any config write materialises the stable file.
    response = api.client.patch(
        f"{URL}/{site_id}/config",
        headers=_headers(api.token_a),
        json={"env": {"PHP_MEMORY_LIMIT": "512M"}},
    )
    assert response.status_code == 200, response.text
    path = env_path(provisioner.settings, names.wp)
    assert leer_archivo_remoto(provisioner.env, path) is not None
    assert _mode_of(provisioner, path) == "600", "FAIL: el secreto no esta en modo 600"


def test_patch_escribe_y_recrea_sin_perder_datos(lab) -> None:
    api, provisioner, names, site_id = lab
    path = env_path(provisioner.settings, names.wp)
    before_id = inspect_container(provisioner.env, names.wp)["Id"]

    response = api.client.patch(
        f"{URL}/{site_id}/config",
        headers=_headers(api.token_a),
        json={"env": {"PHP_MEMORY_LIMIT": "512M", "WP_DEBUG": "false"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["editable_env"]["PHP_MEMORY_LIMIT"] == "512M"

    after = inspect_container(provisioner.env, names.wp)
    assert after is not None
    assert after["Id"] != before_id, "GAP: el contenedor no se recreo"
    env = after["Config"]["Env"]
    assert "PHP_MEMORY_LIMIT=512M" in env
    assert "WP_DEBUG=false" in env

    # The secret moved with the recreate, and the site is still installed.
    file_text = leer_archivo_remoto(provisioner.env, path)
    assert file_text is not None
    assert "WORDPRESS_DB_PASSWORD=" in file_text
    assert _wp_core_installed(provisioner, names, path), "FAIL: el recreate perdio WordPress"


def test_patch_rechaza_claves_de_conexion(lab) -> None:
    api, provisioner, names, site_id = lab
    path = env_path(provisioner.settings, names.wp)
    before = leer_archivo_remoto(provisioner.env, path)
    response = api.client.patch(
        f"{URL}/{site_id}/config",
        headers=_headers(api.token_a),
        json={"env": {"WORDPRESS_DB_HOST": "spanel-otro-db"}},
    )
    assert response.status_code == 422, response.text
    assert leer_archivo_remoto(provisioner.env, path) == before


def test_otro_tenant_no_lee_ni_escribe(lab) -> None:
    api, _, _, site_id = lab
    assert (
        api.client.get(f"{URL}/{site_id}/config", headers=_headers(api.token_b)).status_code
        == 404
    )
    assert (
        api.client.patch(
            f"{URL}/{site_id}/config",
            headers=_headers(api.token_b),
            json={"env": {"WP_DEBUG": "true"}},
        ).status_code
        == 404
    )


def test_delete_borra_el_archivo_de_secretos(lab) -> None:
    api, provisioner, names, site_id = lab
    path = env_path(provisioner.settings, names.wp)
    api.client.patch(
        f"{URL}/{site_id}/config",
        headers=_headers(api.token_a),
        json={"env": {"PHP_MEMORY_LIMIT": "512M"}},
    )
    assert leer_archivo_remoto(provisioner.env, path) is not None
    deleted = api.client.delete(f"{URL}/{site_id}", headers=_headers(api.token_a))
    assert deleted.status_code == 204, deleted.text
    assert (
        leer_archivo_remoto(provisioner.env, path) is None
    ), "FAIL: el secreto de un sitio borrado sigue en disco"


def test_rotar_sales_cambia_wp_config_y_audita(lab) -> None:
    api, provisioner, names, site_id = lab
    before = _auth_key(provisioner, names)
    response = api.client.post(
        f"{URL}/{site_id}/secrets/salts", headers=_headers(api.token_a)
    )
    assert response.status_code == 200, response.text
    assert response.json()["rotated"] == ["salts"]
    after = _auth_key(provisioner, names)
    assert before and after and before != after, "FAIL: las sales no cambiaron"

    with api.app.state.session_factory() as db:
        audits = list(
            db.scalars(
                select(AuditLog).where(
                    AuditLog.entity_id == site_id,
                    AuditLog.action == "hosting.site.secrets_rotated",
                )
            )
        )
        assert audits, "FAIL: la rotacion no quedo auditada"
        assert before not in str([audit.details for audit in audits])
        assert after not in str([audit.details for audit in audits])
