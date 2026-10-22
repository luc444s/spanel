"""Live API suite for A.SPEC 0013.

Two real tenants, real tokens from the kernel, real adoption, and the real
Docker machine over the transport from the core `.env`. The database is a
throwaway SQLite file: this suite proves the authorization boundary and the
WordPress read path, not PostgreSQL.

Destructive reset assertions are allowed only on `spanel-lab-*`. If the lab
does not exist, those tests skip: an absent lab is a GAP, not a pass.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The module-level app in app.main must not try the production database while
# this file is being imported.
os.environ.setdefault("SYSTUTOR_DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

RAIZ_DEL_REPO = Path(__file__).resolve().parents[2]
RAIZ_DEL_CORE = RAIZ_DEL_REPO / "vendor" / "systutor-core"
RAIZ_DE_SCRIPTS = RAIZ_DEL_REPO / "scripts"
RUTA_DE_MIGRACION = (
    RAIZ_DEL_CORE / "plugins" / "hosting" / "migrations" / "001_create_hosting_sites.py"
)

sys.path.insert(0, str(RAIZ_DEL_CORE / "src"))
sys.path.insert(0, str(RAIZ_DEL_CORE))
sys.path.insert(0, str(RAIZ_DE_SCRIPTS))

import systutor.kernel.models  # noqa: F401
from app.main import create_app
from hosting_adopt import adopt_container
from plugins.hosting.backend.database import (
    conexion_de_la_base,
    ejecutar_sql,
    leer_inspect,
    prefijo_de_tablas,
)
from plugins.hosting.backend.inspection import (
    candidato_de,
    database_container,
)
from plugins.hosting.backend.models import HostingSite
from plugins.hosting.backend.settings import (
    docker_env,
    get_hosting_settings,
)
from plugins.hosting.backend.transport import build_docker_argv, run
from plugins.tenant.backend.service import (
    create_tenant,
    create_user_for_tenant,
)
from systutor.api.seed import seed_demo_data
from systutor.core.config import Settings
from systutor.core.database import (
    Base,
    build_engine,
    build_session_factory,
)
from systutor.kernel.audit.models import AuditLog
from systutor.kernel.plugins.persistent import (
    disable_plugin,
    enable_plugin,
    sync_plugin_registry_state,
)
from systutor.kernel.plugins.runtime import PluginManifestRegistry
from systutor.sdk import PluginContext

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
SITIO_A = "spanel-demowp-wp"
# A running site that is not the demo and is not the lab. It is used only for
# read assertions and for a 404 that writes nothing.
SITIO_B = "spanel-lucas-wp"
A_EMAIL = "admin@example.com"
A_PASSWORD = "ChangeMe123!"
B_EMAIL = "admin@tenant-b.test"
B_PASSWORD = "TenantB123!"
RE_SITIO_LAB = re.compile(r"^spanel-lab-")

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="test de integracion: requiere SPANEL_LIVE_TESTS=1 y la laptop encendida",
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cargar_migracion():
    spec = importlib.util.spec_from_file_location("hosting_api_migration", RUTA_DE_MIGRACION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@dataclass(slots=True)
class LiveApi:
    client: TestClient
    app: Any
    settings: Settings
    engine: Engine
    token_a: str
    token_b: str
    tenant_a_id: str
    tenant_b_id: str
    user_b_id: str
    site_a: dict[str, Any]
    site_b: dict[str, Any]


@pytest.fixture()
def live_api(tmp_path: Path) -> Iterator[LiveApi]:
    db_path = tmp_path / "hosting_api_test.db"
    settings = Settings(
        app_name="Hosting API test",
        env="test",
        debug=True,
        api_prefix="/api/v1",
        log_level="WARNING",
        database_url=f"sqlite+pysqlite:///{db_path}",
        redis_url="redis://127.0.0.1:6379/15",
        jwt_secret_key="hosting-test-secret",
        jwt_access_token_ttl_minutes=30,
        plugins_dir=RAIZ_DEL_CORE / "plugins",
        seed_demo_tenant_name="Demo Tenant",
        seed_demo_tenant_slug="demo",
        seed_demo_branch_name="Main Branch",
        seed_demo_branch_code="MAIN",
        seed_admin_email=A_EMAIL,
        seed_admin_password=A_PASSWORD,
        seed_admin_full_name="System Admin",
    )

    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)

    registry = PluginManifestRegistry(settings.plugins_dir)
    registry.discover()
    with Session(engine) as db:
        sync_plugin_registry_state(db, registry=registry)
        enable_plugin(
            db,
            registry=registry,
            plugin_id="hosting",
            context_builder=lambda manifest: PluginContext(manifest),
        )
        db.commit()

    app = create_app(settings)
    app.state.session_factory = build_session_factory(settings)

    try:
        with TestClient(app) as client:
            with app.state.session_factory() as db:
                seeded = seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
                tenant_b = create_tenant(db, name="Tenant B", slug="tenant-b")
                user_b = create_user_for_tenant(
                    db,
                    tenant_id=tenant_b.id,
                    email=B_EMAIL,
                    password=B_PASSWORD,
                    full_name="Tenant B Admin",
                )
                db.commit()

            with app.state.session_factory() as db:
                adopt_container(db, container_name=SITIO_A, tenant_slug="demo")
                adopt_container(db, container_name=SITIO_B, tenant_slug="tenant-b")
                db.commit()

            token_a = _login(client, A_EMAIL, A_PASSWORD)
            token_b = _login(client, B_EMAIL, B_PASSWORD)

            response_a = client.get(
                "/api/v1/plugins/hosting/sites", headers=_headers(token_a)
            )
            assert response_a.status_code == 200, response_a.text
            response_b = client.get(
                "/api/v1/plugins/hosting/sites", headers=_headers(token_b)
            )
            assert response_b.status_code == 200, response_b.text

            sites_a = response_a.json()["sites"]
            sites_b = response_b.json()["sites"]
            site_a = next(site for site in sites_a if site["container_name"] == SITIO_A)
            site_b = next(site for site in sites_b if site["container_name"] == SITIO_B)

            yield LiveApi(
                client=client,
                app=app,
                settings=settings,
                engine=engine,
                token_a=token_a,
                token_b=token_b,
                tenant_a_id=seeded["tenant_id"],
                tenant_b_id=tenant_b.id,
                user_b_id=user_b.id,
                site_a=site_a,
                site_b=site_b,
            )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Read scope: I1 and I2.
# ---------------------------------------------------------------------------


def test_listado_solo_del_tenant(live_api: LiveApi) -> None:
    response = live_api.client.get(
        "/api/v1/plugins/hosting/sites", headers=_headers(live_api.token_a)
    )
    assert response.status_code == 200
    sites = response.json()["sites"]
    names = {site["container_name"] for site in sites}
    assert SITIO_A in names
    assert SITIO_B not in names, "el listado de A mostro un sitio de B"
    assert all("tenant_id" not in site for site in sites)

    response_b = live_api.client.get(
        "/api/v1/plugins/hosting/sites", headers=_headers(live_api.token_b)
    )
    names_b = {site["container_name"] for site in response_b.json()["sites"]}
    assert SITIO_B in names_b
    assert SITIO_A not in names_b, "el listado de B mostro un sitio de A"


def test_site_de_otro_tenant_es_404(live_api: LiveApi) -> None:
    response = live_api.client.get(
        f"/api/v1/plugins/hosting/sites/{live_api.site_b['id']}/users",
        headers=_headers(live_api.token_a),
    )
    assert response.status_code == 404
    assert response.status_code != 403


def test_listado_de_usuarios_de_un_sitio_propio(live_api: LiveApi) -> None:
    response = live_api.client.get(
        f"/api/v1/plugins/hosting/sites/{live_api.site_a['id']}/users",
        headers=_headers(live_api.token_a),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["site_id"] == live_api.site_a["id"]
    assert body["container_name"] == SITIO_A
    assert body["users"], "el sitio demo deberia tener al menos el usuario admin"
    for user in body["users"]:
        assert set(user) == {"id", "login", "email", "status", "role"}
        assert isinstance(user["id"], int)
        assert "user_pass" not in user
    assert any(user["login"] == "admin" for user in body["users"])



def test_usuario_de_otro_tenant_es_404(live_api: LiveApi) -> None:
    with Session(live_api.engine) as db:
        before = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "hosting.user.password_reset"
            )
        )

    response = live_api.client.post(
        f"/api/v1/plugins/hosting/sites/{live_api.site_b['id']}/users/1/password",
        headers=_headers(live_api.token_a),
        json={},
    )
    assert response.status_code == 404

    with Session(live_api.engine) as db:
        after = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "hosting.user.password_reset"
            )
        )
    assert after == before, "un 404 cross-tenant escribio una auditoria"


def test_la_api_no_acepta_container_name(live_api: LiveApi) -> None:
    url = (
        f"/api/v1/plugins/hosting/sites/{live_api.site_a['id']}"
        "/users/1/password"
    )
    response = live_api.client.post(
        url,
        headers=_headers(live_api.token_a),
        json={"container": "mailserver"},
    )
    assert response.status_code == 422

    response = live_api.client.post(
        url,
        headers=_headers(live_api.token_a),
        json={"tenant_id": live_api.tenant_b_id},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# I9: disabling the plugin hides routes but keeps ownership rows.
# ---------------------------------------------------------------------------


def test_plugin_deshabilitado_no_expone_rutas(live_api: LiveApi) -> None:
    with Session(live_api.engine) as db:
        before = db.scalar(select(func.count()).select_from(HostingSite))
        disable_plugin(
            db,
            registry=live_api.app.state.plugin_registry,
            plugin_id="hosting",
            context_builder=lambda manifest: PluginContext(manifest),
        )
        db.commit()

    from systutor.core.lifecycle import bootstrap_app_state

    bootstrap_app_state(live_api.app, live_api.settings)

    paths = [getattr(route, "path", "") for route in live_api.app.router.routes]
    assert not any(path.startswith("/api/v1/plugins/hosting") for path in paths)

    response = live_api.client.get(
        "/api/v1/plugins/hosting/sites", headers=_headers(live_api.token_a)
    )
    assert response.status_code == 404

    with Session(live_api.engine) as db:
        after = db.scalar(select(func.count()).select_from(HostingSite))
    assert after == before, "deshabilitar el plugin borro la propiedad de los sitios"


# ---------------------------------------------------------------------------
# I10: migration up/down/up on the real PostgreSQL.
# ---------------------------------------------------------------------------


def test_migracion_up_down_up() -> None:
    url = os.environ.get("SPANEL_TEST_POSTGRES_URL", "").strip()
    if not url:
        pytest.skip("GAP: SPANEL_TEST_POSTGRES_URL no configurada para PostgreSQL real")

    from sqlalchemy import create_engine, inspect

    engine = create_engine(url)
    migracion = _cargar_migracion()
    try:
        with Session(engine) as db:
            migracion.downgrade(db)  # checkfirst: tolerates a previous run
            db.commit()
        Base.metadata.create_all(bind=engine)
        # `HostingSite` is imported in this module, so create_all also created
        # its table. Drop it with the migration to prove the migration creates
        # it, not the test fixture.
        with Session(engine) as db:
            migracion.downgrade(db)
            db.commit()
        assert not inspect(engine).has_table("hosting_sites")

        with Session(engine) as db:
            migracion.upgrade(db)
            db.commit()
        assert inspect(engine).has_table("hosting_sites")

        with Session(engine) as db:
            migracion.downgrade(db)
            db.commit()
        assert not inspect(engine).has_table("hosting_sites")

        with Session(engine) as db:
            migracion.upgrade(db)
            db.commit()
        assert inspect(engine).has_table("hosting_sites")
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Destructive tests: only spanel-lab-*.
# ---------------------------------------------------------------------------


def _docker_env() -> dict[str, str]:
    return docker_env(get_hosting_settings())


def _find_lab_site() -> str | None:
    result = run(
        build_docker_argv("docker ps -a --format '{{.Names}}'", env=_docker_env()),
        env=_docker_env(),
        timeout=60,
    )
    if result.returncode != 0:
        return None
    for name in (line.strip() for line in result.stdout.splitlines()):
        if not RE_SITIO_LAB.match(name):
            continue
        info = leer_inspect(_docker_env(), name)
        if candidato_de(info).es_sitio:
            return name
    return None


def _lab_site_or_skip(live_api: LiveApi) -> str:
    lab = _find_lab_site()
    if lab is None:
        pytest.skip(
            "GAP: no existe ningun contenedor spanel-lab-* para probar el reset destructivo"
        )
    with Session(live_api.engine) as db:
        existing = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        if existing is None:
            adopt_container(db, container_name=lab, tenant_slug="tenant-b")
            db.commit()
    return lab


@dataclass(slots=True)
class SiteDb:
    env: dict[str, str]
    container: str
    user: str
    password: str
    base: str
    prefix: str


def _site_db(container: str) -> SiteDb:
    env = _docker_env()
    info = leer_inspect(env, container)
    creds = conexion_de_la_base(info)
    db_container = database_container(info)
    prefix = prefijo_de_tablas(
        env, db_container, creds["usuario"], creds["_password"], creds["base"]
    )
    return SiteDb(
        env=env,
        container=db_container,
        user=creds["usuario"],
        password=creds["_password"],
        base=creds["base"],
        prefix=prefix,
    )


def _sql(site_db: SiteDb, statement: str) -> list[list[str]]:
    output = ejecutar_sql(
        site_db.env,
        site_db.container,
        site_db.base,
        statement,
        usuario=site_db.user,
        password=site_db.password,
    )
    return [line.split("\t") for line in output.splitlines() if line.strip()]


def _lab_user_id(live_api: LiveApi, lab: str) -> int:
    with Session(live_api.engine) as db:
        site = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        assert site is not None
        site_id = site.id
    response = live_api.client.get(
        f"/api/v1/plugins/hosting/sites/{site_id}/users",
        headers=_headers(live_api.token_b),
    )
    assert response.status_code == 200, response.text
    users = response.json()["users"]
    if not users:
        pytest.skip("GAP: el sitio lab no tiene usuarios")
    return int(users[0]["id"])


def test_reset_por_id_una_sola_fila(live_api: LiveApi, caplog: pytest.LogCaptureFixture) -> None:
    lab = _lab_site_or_skip(live_api)
    user_id = _lab_user_id(live_api, lab)
    site_db = _site_db(lab)
    hashes_before = {
        int(row[0]): row[2] for row in _sql(site_db, f"SELECT ID, user_login, user_pass FROM {site_db.prefix}users")
    }

    with Session(live_api.engine) as db:
        site = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        assert site is not None
        site_id = site.id

    response = live_api.client.post(
        f"/api/v1/plugins/hosting/sites/{site_id}/users/{user_id}/password",
        headers=_headers(live_api.token_b),
        json={},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is True
    assert body["password"]
    assert response.headers.get("cache-control") == "no-store"

    hashes_after = {
        int(row[0]): row[2] for row in _sql(site_db, f"SELECT ID, user_login, user_pass FROM {site_db.prefix}users")
    }
    assert hashes_after[user_id] != hashes_before[user_id]
    for other_id, old_hash in hashes_before.items():
        if other_id != user_id:
            assert hashes_after[other_id] == old_hash, "el reset toco otra fila"

    # I5: the generated password is not in the audit and not in the logs.
    with Session(live_api.engine) as db:
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "hosting.user.password_reset")
        )
        assert audit is not None
        assert body["password"] not in str(audit.details)
        assert "user_pass" not in str(audit.details)
    assert body["password"] not in caplog.text


def test_la_password_no_aparece_en_audit_ni_en_logs(
    live_api: LiveApi, caplog: pytest.LogCaptureFixture
) -> None:
    lab = _lab_site_or_skip(live_api)
    user_id = _lab_user_id(live_api, lab)
    with Session(live_api.engine) as db:
        site = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        assert site is not None
        site_id = site.id

    response = live_api.client.post(
        f"/api/v1/plugins/hosting/sites/{site_id}/users/{user_id}/password",
        headers=_headers(live_api.token_b),
        json={},
    )
    assert response.status_code == 200, response.text
    password = response.json()["password"]
    assert password

    with Session(live_api.engine) as db:
        audits = list(
            db.scalars(
                select(AuditLog).where(AuditLog.action == "hosting.user.password_reset")
            )
        )
    assert audits
    for audit in audits:
        assert password not in str(audit.details)
        assert "$wp$" not in str(audit.details)
    assert password not in caplog.text


def test_audit_del_reset_tiene_el_tenant_del_contexto(live_api: LiveApi) -> None:
    lab = _lab_site_or_skip(live_api)
    user_id = _lab_user_id(live_api, lab)
    with Session(live_api.engine) as db:
        site = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        assert site is not None
        site_id = site.id

    response = live_api.client.post(
        f"/api/v1/plugins/hosting/sites/{site_id}/users/{user_id}/password",
        headers=_headers(live_api.token_b),
        json={},
    )
    assert response.status_code == 200, response.text

    with Session(live_api.engine) as db:
        audit = db.scalar(
            select(AuditLog)
            .where(AuditLog.action == "hosting.user.password_reset")
            .order_by(AuditLog.occurred_at.desc())
        )
        assert audit is not None
        assert audit.tenant_id == live_api.tenant_b_id
        assert audit.actor_user_id == live_api.user_b_id
        assert audit.module == "hosting"
        assert audit.entity_id == str(user_id)


def test_managed_false_no_se_puede_resetear(live_api: LiveApi) -> None:
    lab = _lab_site_or_skip(live_api)
    info = leer_inspect(_docker_env(), lab)
    labels = (info.get("Config") or {}).get("Labels") or {}
    if str(labels.get("spanel.managed", "")).lower() not in {"false", "0", "no", "off"}:
        pytest.skip("GAP: el sitio lab no tiene spanel.managed=false")

    user_id = _lab_user_id(live_api, lab)
    with Session(live_api.engine) as db:
        site = db.scalar(select(HostingSite).where(HostingSite.container_name == lab))
        assert site is not None
        site_id = site.id
        before = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "hosting.user.password_reset"
            )
        )

    response = live_api.client.post(
        f"/api/v1/plugins/hosting/sites/{site_id}/users/{user_id}/password",
        headers=_headers(live_api.token_b),
        json={},
    )
    assert response.status_code == 409

    with Session(live_api.engine) as db:
        after = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "hosting.user.password_reset"
            )
        )
    assert after == before, "un 409 por managed=false escribio una auditoria"
