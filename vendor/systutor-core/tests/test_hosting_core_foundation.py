"""A.SPEC 0012: the hosting core foundation.

Three contracts:

1. a tenant administrator receives exactly what installed plugins declare for
   tenants, never the full plugin permission set;
2. the seed administrator still receives every declared plugin permission;
3. the proven Docker/WordPress logic is importable from the plugin namespace
   and the scripts are thin wrappers, not copies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from systutor.api.seed import sync_permissions, sync_tenant_admin_roles
from systutor.contracts.plugins import PluginManifestContract
from systutor.kernel.permissions.models import Permission, Role, RolePermission
from systutor.kernel.permissions.service import sync_tenant_admin_role
from systutor.kernel.plugins.manifest import PluginManifest
from systutor.kernel.plugins.persistent import (
    list_tenant_admin_permissions,
    register_tenant_admin_permissions,
    sync_plugin_registry_state,
)
from systutor.kernel.plugins.runtime import LoadedPlugin, PluginManifestRegistry
from systutor.kernel.tenants.models import Tenant

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
SCRIPTS_ROOT = REPO_ROOT.parent.parent / "scripts"

MAIL_TENANT_ADMIN = [
    "mail.accounts.read",
    "mail.accounts.create",
    "mail.account.password",
]
# The shell cannot load the plugin runtime without it, so the kernel adds it
# even though no plugin manifest declares it.
TENANT_ADMIN_BASE = {"core.plugin.runtime.read"}


def _manifest(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "hosting",
        "name": "Hosting",
        "version": "0.1.0",
        "api_version": "1",
        "requires": [],
        "backend_entrypoint": "backend.register:register",
        "frontend_entrypoint": "frontend/register.ts",
        "permissions": [
            "hosting.sites.read",
            "hosting.sites.create",
            "hosting.sites.delete",
        ],
        "events": [],
        "description": "Hosting test plugin",
    }
    payload.update(overrides)
    return payload


def _role_permission_names(db: Session, role_id: str) -> set[str]:
    stmt = (
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return set(db.scalars(stmt))


def _declared_tenant_admin_permissions() -> set[str]:
    """The union the installed manifests declare, without hardcoding plugin names.

    A.SPEC 0013 adds hosting permissions to this set; the test must follow the
    manifests, not the mail-only list it was written against.
    """
    return {
        permission
        for plugin in _real_plugins()
        if plugin.manifest is not None
        for permission in plugin.manifest.tenant_admin_permissions
    }


def _real_plugins() -> list[LoadedPlugin]:
    loaded: list[LoadedPlugin] = []
    for manifest_path in sorted(PLUGINS_ROOT.glob("*/plugin.json")):
        manifest = PluginManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        loaded.append(
            LoadedPlugin(
                plugin_id=manifest.id,
                root=manifest_path.parent,
                status="enabled",
                manifest=manifest,
            )
        )
    return loaded


# ---------------------------------------------------------------------------
# I8: the manifest rejects a tenant-admin permission outside `permissions`.
# ---------------------------------------------------------------------------


def test_manifest_acepta_tenant_admin_permissions() -> None:
    default = PluginManifestContract.model_validate(_manifest())
    assert default.tenant_admin_permissions == []

    manifest = PluginManifestContract.model_validate(
        _manifest(tenant_admin_permissions=["hosting.sites.read"])
    )
    assert manifest.tenant_admin_permissions == ["hosting.sites.read"]


def test_manifest_rechaza_permiso_de_tenant_fuera_de_permissions() -> None:
    with pytest.raises(ValueError, match="hosting.sites.manage"):
        PluginManifestContract.model_validate(
            _manifest(tenant_admin_permissions=["hosting.sites.manage"])
        )


def test_manifest_rechaza_tenant_admin_con_namespace_ajeno() -> None:
    with pytest.raises(ValueError, match="plugin id namespace"):
        PluginManifestContract.model_validate(
            _manifest(tenant_admin_permissions=["other.sites.read"])
        )


def test_manifest_rechaza_tenant_admin_duplicado() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        PluginManifestContract.model_validate(
            _manifest(tenant_admin_permissions=["hosting.sites.read", "hosting.sites.read"])
        )


# ---------------------------------------------------------------------------
# I7: a tenant administrator receives only the declared subset.
# ---------------------------------------------------------------------------


def test_tenant_admin_recibe_solo_los_declarados(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    from plugins.tenant.backend.service import create_tenant, create_user_for_tenant

    register_tenant_admin_permissions("mail", MAIL_TENANT_ADMIN)
    register_tenant_admin_permissions("tenant", [])

    tenant = create_tenant(db_session, name="Tenant nuevo", slug="tenant-nuevo")
    user = create_user_for_tenant(
        db_session,
        tenant_id=tenant.id,
        email="nuevo@example.com",
        password="Nuevo123!",
        full_name="Tenant Nuevo",
    )
    role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "tenant_admin")
    )
    assert role is not None

    granted = _role_permission_names(db_session, role.id)
    assert granted == set(MAIL_TENANT_ADMIN) | TENANT_ADMIN_BASE
    assert "mail.accounts.all" not in granted, "el permiso cross-domain llego al tenant"
    assert "tenant.tenants.manage" not in granted, "el tenant recibio poder cross-tenant"
    assert user.tenant_id == tenant.id


# ---------------------------------------------------------------------------
# I9: the seed admin keeps everything; backfill is idempotent.
# ---------------------------------------------------------------------------


def test_seed_admin_conserva_todos_los_permisos(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    plugins = _real_plugins()
    tenant = db_session.get(Tenant, seeded_demo["tenant_id"])
    assert tenant is not None

    sync_permissions(db_session, tenant, plugins)

    admin_role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "admin")
    )
    assert admin_role is not None
    granted = _role_permission_names(db_session, admin_role.id)

    declared = {
        permission
        for plugin in plugins
        if plugin.manifest is not None
        for permission in plugin.manifest.permissions
    }
    assert declared <= granted, f"el admin semilla perdio permisos: {sorted(declared - granted)}"
    assert "mail.accounts.all" in granted, "el admin semilla perdio el permiso cross-domain"
    assert "tenant.tenants.manage" in granted, "el admin semilla perdio la gestion de tenants"


def test_backfill_es_idempotente(app, db_session: Session, seeded_demo: dict[str, str]) -> None:
    from plugins.tenant.backend.service import create_tenant

    register_tenant_admin_permissions("mail", ["mail.accounts.read"])
    tenant = create_tenant(db_session, name="Backfill", slug="backfill")

    role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "tenant_admin")
    )
    assert role is not None
    assert _role_permission_names(db_session, role.id) == {"mail.accounts.read"} | TENANT_ADMIN_BASE

    register_tenant_admin_permissions(
        "mail",
        ["mail.accounts.read", "mail.accounts.create"],
    )
    sync_tenant_admin_role(db_session, tenant_id=tenant.id)
    sync_tenant_admin_role(db_session, tenant_id=tenant.id)

    granted = _role_permission_names(db_session, role.id)
    assert granted == {"mail.accounts.read", "mail.accounts.create"} | TENANT_ADMIN_BASE
    rows = db_session.scalars(
        select(RolePermission).where(RolePermission.role_id == role.id)
    ).all()
    assert len(rows) == 3, "el backfill duplico filas de role_permissions"

    # A permission a plugin stops declaring is no longer granted.
    register_tenant_admin_permissions("mail", ["mail.accounts.create"])
    sync_tenant_admin_role(db_session, tenant_id=tenant.id)
    assert _role_permission_names(db_session, role.id) == (
        {"mail.accounts.create"} | TENANT_ADMIN_BASE
    )


def test_el_boot_actualiza_tenants_existentes(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    """An existing tenant picks up a newly declared permission on the next boot."""
    from plugins.tenant.backend.service import create_tenant

    register_tenant_admin_permissions("mail", ["mail.accounts.read"])
    tenant = create_tenant(db_session, name="Existente", slug="existente")
    role = db_session.scalar(
        select(Role).where(Role.tenant_id == tenant.id, Role.name == "tenant_admin")
    )
    assert role is not None
    assert _role_permission_names(db_session, role.id) == {"mail.accounts.read"} | TENANT_ADMIN_BASE

    sync_tenant_admin_roles(db_session, _real_plugins())
    assert _role_permission_names(db_session, role.id) == (
        _declared_tenant_admin_permissions() | TENANT_ADMIN_BASE
    )

    before = db_session.scalars(
        select(RolePermission).where(RolePermission.role_id == role.id)
    ).all()
    sync_tenant_admin_roles(db_session, _real_plugins())
    after = db_session.scalars(
        select(RolePermission).where(RolePermission.role_id == role.id)
    ).all()
    assert len(after) == len(before), "el boot duplico permisos del tenant existente"


def test_la_sincronizacion_persiste_el_subconjunto_declarado(
    app,
    db_session: Session,
    seeded_demo: dict[str, str],
) -> None:
    registry = PluginManifestRegistry(PLUGINS_ROOT)
    registry.discover()
    sync_plugin_registry_state(db_session, registry=registry)

    declared = set(list_tenant_admin_permissions())
    assert declared == _declared_tenant_admin_permissions(), (
        "la sincronizacion no recordo exactamente lo que declaran los plugins"
    )
    assert "mail.accounts.all" not in declared
    assert "tenant.tenants.manage" not in declared


# ---------------------------------------------------------------------------
# I10: the package is importable and the kernel has no business logic.
# ---------------------------------------------------------------------------


def test_el_paquete_es_importable() -> None:
    code = (
        "from plugins.hosting.backend import "
        "discovery, transport, database, passwords, reset"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_el_kernel_no_sabe_de_docker_ni_wordpress() -> None:
    for path in (REPO_ROOT / "src" / "systutor").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in ("docker", "wordpress", "mariadb"):
            assert word not in text, f"{path} menciona {word}: la logica no vive en el kernel"


def test_el_paquete_no_tiene_efectos_al_importar() -> None:
    code = (
        "import sys\n"
        "eventos = []\n"
        "def gancho(event, args):\n"
        "    if event in {'subprocess.Popen', 'os.system'}:\n"
        "        eventos.append(event)\n"
        "    if event == 'open' and len(args) > 1 and isinstance(args[1], str):\n"
        "        if any(modo in args[1] for modo in ('w', 'a', 'x', '+')):\n"
        "            eventos.append(event)\n"
        "sys.addaudithook(gancho)\n"
        "from plugins.hosting.backend import discovery, transport, database, passwords, reset\n"
        "assert not eventos, eventos\n"
        "assert reset.RE_SQL_SEGURO.match('wp_')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# I11: the scripts are wrappers, not forks.
# ---------------------------------------------------------------------------


def test_los_scripts_no_duplican_logica() -> None:
    moved_definitions = [
        "def load_env_file(",
        "def build_docker_argv(",
        "def run(",
        "def parse_labels(",
        "def detectar_capa1(",
        "def detectar_capa2(",
        "def detectar_todos(",
        "def es_operable(",
        "def es_prefiltrado(",
        "def prefijo_de_tablas(",
        "def filas_de_usuarios(",
        "def usuarios_con_rol(",
        "def hash_de_password(",
        "def hash_valido(",
        "def para_sql(",
        "def resolver_objetivo(",
        "def actualizar_hash(",
        "def leer_hash(",
    ]
    for script in sorted(SCRIPTS_ROOT.glob("*.py")):
        texto = script.read_text(encoding="utf-8")
        for definicion in moved_definitions:
            assert definicion not in texto, f"{script} todavia define {definicion}"


@pytest.mark.skipif(
    not SCRIPTS_ROOT.is_dir(),
    reason="the operator scripts live in the host checkout, not in the kernel",
)
def test_los_scripts_conservan_los_flags() -> None:
    probe = subprocess.run(
        [sys.executable, str(SCRIPTS_ROOT / "probe_docker.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    reset = subprocess.run(
        [sys.executable, str(SCRIPTS_ROOT / "reset_wp_password.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0
    assert reset.returncode == 0
    for flag in ("--env-file",):
        assert flag in probe.stdout
    for flag in ("--password", "--usuario", "--id", "--env-file", "--dry-run", "--listar"):
        assert flag in reset.stdout


# ---------------------------------------------------------------------------
# The real manifests declare exactly what A.SPEC 0012 says.
# ---------------------------------------------------------------------------


def test_mail_declara_su_subconjunto_de_tenant() -> None:
    payload = json.loads((PLUGINS_ROOT / "mail" / "plugin.json").read_text(encoding="utf-8"))
    declared = set(payload["tenant_admin_permissions"])
    assert declared == set(MAIL_TENANT_ADMIN)
    assert "mail.accounts.all" not in declared
    assert declared <= set(payload["permissions"])


def test_tenant_plugin_no_declara_permisos_de_tenant_admin() -> None:
    payload = json.loads((PLUGINS_ROOT / "tenant" / "plugin.json").read_text(encoding="utf-8"))
    assert payload.get("tenant_admin_permissions", []) == []
