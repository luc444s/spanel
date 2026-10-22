"""A.SPEC 0014: owned API creation and destructive operations on throwaway lab sites."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

os.environ.setdefault("SPANEL_HOSTING_TRAEFIK", "false")
_core = Path(__file__).resolve().parents[2] / "vendor" / "systutor-core"
sys.path.insert(0, str(_core / "src"))
sys.path.insert(0, str(_core))

import pytest  # noqa: E402
from plugins.hosting.backend.inspection import inspect_container  # noqa: E402
from plugins.hosting.backend.models import HostingSite  # noqa: E402
from plugins.hosting.backend.provisioner import Provisioner, SiteNames, slug_for  # noqa: E402
from plugins.hosting.backend.schemas import CreateSiteRequest  # noqa: E402
from plugins.hosting.backend.settings import get_hosting_settings  # noqa: E402
from sqlalchemy import select  # noqa: E402
from systutor.kernel.audit.models import AuditLog  # noqa: E402
from test_hosting_api_live import LiveApi, _headers, live_api as live_api_fixture  # noqa: E402, F401


def test_validacion_pura() -> None:
    assert slug_for("Lab Site") == "lab-site"
    for name in ("a", "A" * 41, "***"):
        with pytest.raises(ValueError):
            slug_for(name)
    for field in ("tenant_id", "container", "volume", "network"):
        with pytest.raises(ValueError):
            CreateSiteRequest.model_validate(
                {
                    "name": "lab-valid",
                    "domain": "lab.example.test",
                    "admin_email": "admin@example.test",
                    field: "other",
                }
            )
    with pytest.raises(ValueError):
        CreateSiteRequest(
            name="lab-valid", domain="not a domain", admin_email="a@example.test"
        )


@pytest.fixture()
def lab(live_api_fixture: LiveApi, caplog: pytest.LogCaptureFixture):
    live_api = live_api_fixture
    if os.getenv("SPANEL_LIVE_TESTS") != "1":
        pytest.skip("GAP: SPANEL_LIVE_TESTS=1 requerido")
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
    assert names.wp.startswith("spanel-lab-") and names.db.startswith("spanel-lab-")
    domain = f"{slug}.example.test"
    url = "/api/v1/plugins/hosting/sites"
    token = live_api.token_a
    try:
        response = live_api.client.post(
            url,
            headers=_headers(token),
            json={
                "name": slug,
                "domain": domain,
                "admin_email": "admin@example.test",
            },
        )
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "no-store"
        yield live_api, provisioner, names, response.json(), caplog
    finally:
        with live_api.app.state.session_factory() as db:
            site = db.scalar(
                select(HostingSite).where(HostingSite.container_name == names.wp)
            )
            site_id = site.id if site else None
        if site_id:
            deleted = live_api.client.delete(
                f"{url}/{site_id}", headers=_headers(token)
            )
            if deleted.status_code != 204:
                failures = provisioner.cleanup(names, live_api.tenant_a_id)
                with live_api.app.state.session_factory() as db:
                    remaining = db.get(HostingSite, site_id)
                    if remaining:
                        db.delete(remaining)
                        db.commit()
                assert not failures, failures
                pytest.fail(
                    f"GAP: delete devolvio {deleted.status_code}: {deleted.text}"
                )
        for kind, name in (
            ("container", names.wp),
            ("container", names.db),
            ("volume", names.wp),
            ("volume", names.db),
        ):
            assert not provisioner.exists(kind, name), f"GAP: recurso huérfano {name}"


def test_creacion_usa_el_tenant_del_contexto(lab) -> None:
    api, _, names, body, _ = lab
    with api.app.state.session_factory() as db:
        site = db.get(HostingSite, body["site"]["id"])
        assert site and site.tenant_id == api.tenant_a_id
    assert body["site"]["container_name"] == names.wp
    assert body["admin_password"]
    bad = api.client.post(
        "/api/v1/plugins/hosting/sites",
        headers=_headers(api.token_a),
        json={
            "name": "lab-other",
            "domain": "other.example.test",
            "admin_email": "admin@example.test",
            "tenant_id": api.tenant_b_id,
        },
    )
    assert bad.status_code == 422


def test_lifecycle_solo_toca_su_sitio(lab) -> None:
    api, provisioner, names, body, _ = lab
    site_id = body["site"]["id"]
    control = inspect_container(provisioner.env, "spanel-lucas-wp")
    assert control is not None
    for action in ("stop", "stop", "start", "start", "restart"):
        response = api.client.post(
            f"/api/v1/plugins/hosting/sites/{site_id}/{action}",
            headers=_headers(api.token_a),
        )
        assert response.status_code == 200, response.text
        assert inspect_container(provisioner.env, names.wp) is not None
    logs = api.client.get(
        f"/api/v1/plugins/hosting/sites/{site_id}/logs?lines=2000",
        headers=_headers(api.token_a),
    )
    assert logs.status_code == 200, logs.text
    after = inspect_container(provisioner.env, "spanel-lucas-wp")
    assert after and control["State"]["Status"] == after["State"]["Status"]


def test_otro_tenant_no_puede_operar(lab) -> None:
    api, _, _, body, _ = lab
    site_id = body["site"]["id"]
    for action in ("start", "stop", "restart"):
        assert (
            api.client.post(
                f"/api/v1/plugins/hosting/sites/{site_id}/{action}",
                headers=_headers(api.token_b),
            ).status_code
            == 404
        )
    assert (
        api.client.get(
            f"/api/v1/plugins/hosting/sites/{site_id}/logs",
            headers=_headers(api.token_b),
        ).status_code
        == 404
    )
    assert (
        api.client.delete(
            f"/api/v1/plugins/hosting/sites/{site_id}", headers=_headers(api.token_b)
        ).status_code
        == 404
    )


def test_dominio_y_nombre_duplicados_son_409(lab) -> None:
    api, provisioner, names, _, _ = lab
    for name, domain in (
        ("lab-another", f"{names.slug}.example.test"),
        (names.slug, "other.example.test"),
    ):
        response = api.client.post(
            "/api/v1/plugins/hosting/sites",
            headers=_headers(api.token_a),
            json={
                "name": name,
                "domain": domain,
                "admin_email": "admin@example.test",
            },
        )
        assert response.status_code == 409, response.text
    assert not provisioner.exists("container", "spanel-lab-another-wp")


def test_password_y_auditoria(lab) -> None:
    api, _, _, body, caplog = lab
    password = body["admin_password"]
    site_id = body["site"]["id"]
    listed = api.client.get(
        "/api/v1/plugins/hosting/sites", headers=_headers(api.token_a)
    )
    logs = api.client.get(
        f"/api/v1/plugins/hosting/sites/{site_id}/logs", headers=_headers(api.token_a)
    )
    assert password not in listed.text + logs.text + caplog.text
    with api.app.state.session_factory() as db:
        audits = list(db.scalars(select(AuditLog).where(AuditLog.entity_id == site_id)))
        assert audits
        assert password not in str([audit.details for audit in audits])
        for audit in audits:
            assert audit.tenant_id == api.tenant_a_id
            assert audit.actor_user_id is not None
            assert audit.branch_id is not None


def test_creacion_fallida_no_deja_huerfanos(
    lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    api, provisioner, control, _, _ = lab
    names = SiteNames("lab-" + secrets.token_hex(4))
    before = inspect_container(provisioner.env, control.wp)
    assert before is not None
    original = Provisioner.command

    def fail_after_db(self, *args, **kwargs):
        if args[:2] == ("run", "-d") and names.wp in args:
            raise RuntimeError("fallo inyectado despues de crear MariaDB")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Provisioner, "command", fail_after_db)
    try:
        with pytest.raises(RuntimeError, match="fallo inyectado"):
            provisioner.create(
                names,
                api.tenant_a_id,
                f"{names.slug}.example.test",
                "admin@example.test",
            )
    finally:
        monkeypatch.undo()
    for kind, name in (
        ("container", names.wp),
        ("container", names.db),
        ("volume", names.wp),
        ("volume", names.db),
    ):
        assert not provisioner.exists(kind, name), f"recurso huerfano {name}"
    with api.app.state.session_factory() as db:
        assert (
            db.scalar(select(HostingSite).where(HostingSite.container_name == names.wp))
            is None
        )
    after = inspect_container(provisioner.env, control.wp)
    assert after and before["Id"] == after["Id"]


def test_managed_false_bloquea_start_stop_delete(lab) -> None:
    api, provisioner, _, _, _ = lab
    names = SiteNames("lab-" + secrets.token_hex(4))
    assert names.wp.startswith("spanel-lab-")
    provisioner.command(
        "create",
        "--name",
        names.wp,
        "--network",
        provisioner.settings.network,
        "--label",
        f"spanel.site={names.slug}",
        "--label",
        "spanel.managed=false",
        "--label",
        f"spanel.tenant={api.tenant_a_id}",
        provisioner.settings.wp_image,
    )
    try:
        with api.app.state.session_factory() as db:
            site = HostingSite(tenant_id=api.tenant_a_id, container_name=names.wp)
            db.add(site)
            db.commit()
            site_id = site.id
        try:
            with api.app.state.session_factory() as db:
                before = len(
                    list(
                        db.scalars(
                            select(AuditLog).where(AuditLog.entity_id == site_id)
                        )
                    )
                )
            for action in ("start", "stop", "restart"):
                response = api.client.post(
                    f"/api/v1/plugins/hosting/sites/{site_id}/{action}",
                    headers=_headers(api.token_a),
                )
                assert response.status_code == 409, response.text
            deleted = api.client.delete(
                f"/api/v1/plugins/hosting/sites/{site_id}",
                headers=_headers(api.token_a),
            )
            assert deleted.status_code == 409, deleted.text
            with api.app.state.session_factory() as db:
                assert (
                    len(
                        list(
                            db.scalars(
                                select(AuditLog).where(AuditLog.entity_id == site_id)
                            )
                        )
                    )
                    == before
                )
                assert db.get(HostingSite, site_id) is not None
            assert inspect_container(provisioner.env, names.wp) is not None
        finally:
            with api.app.state.session_factory() as db:
                site = db.get(HostingSite, site_id)
                if site:
                    db.delete(site)
                    db.commit()
    finally:
        if provisioner._owned("container", names.wp, names, api.tenant_a_id):
            provisioner.command("rm", "-f", names.wp)


def test_delete_no_toca_otros_sitios(lab) -> None:
    api, provisioner, control, _, _ = lab
    names = SiteNames("lab-" + secrets.token_hex(4))
    second = api.client.post(
        "/api/v1/plugins/hosting/sites",
        headers=_headers(api.token_a),
        json={
            "name": names.slug,
            "domain": f"{names.slug}.example.test",
            "admin_email": "admin@example.test",
        },
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["site"]["id"]
    before = inspect_container(provisioner.env, control.wp)
    assert before is not None
    try:
        result = api.client.delete(
            f"/api/v1/plugins/hosting/sites/{second_id}", headers=_headers(api.token_a)
        )
        assert result.status_code == 204, result.text
        assert not provisioner.exists("volume", names.wp)
        assert not provisioner.exists("volume", names.db)
        after = inspect_container(provisioner.env, control.wp)
        assert after and before["Id"] == after["Id"]
        assert provisioner.exists("volume", control.wp)
    finally:
        with api.app.state.session_factory() as db:
            site = db.get(HostingSite, second_id)
        if site:
            response = api.client.delete(
                f"/api/v1/plugins/hosting/sites/{second_id}",
                headers=_headers(api.token_a),
            )
            assert response.status_code == 204, response.text
