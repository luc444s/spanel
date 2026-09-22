from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import create_app
from systutor.core.config import Settings
from systutor.core.database import Base, build_engine, build_session_factory
from systutor.kernel.plugins.persistent import (
    build_persistent_plugin_runtime,
    downgrade_plugin,
    enable_plugin,
    get_plugin_registry_record_by_plugin_id,
    install_plugin,
    rollback_plugin,
    upgrade_plugin,
)
from systutor.kernel.plugins.runtime import PluginManifestRegistry, PluginRuntimeError
from systutor.sdk import PluginContext


def login(client, email: str = "admin@example.com", password: str = "ChangeMe123!"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def auth_headers(client) -> dict[str, str]:
    response = login(client)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _write_plugin(
    plugin_root: Path,
    *,
    plugin_id: str,
    register_body: str | None = None,
    migrations: list[tuple[str, str]] | None = None,
    requires: list[str] | None = None,
) -> None:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for directory in ["backend", "frontend", "migrations", "permissions", "events"]:
        (plugin_root / directory).mkdir(exist_ok=True)

    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.1.0",
        "api_version": "1",
        "requires": requires or [],
        "backend_entrypoint": "backend.plugin:register",
        "frontend_entrypoint": "frontend/register.ts",
        "permissions": [f"{plugin_id}.sample.read"],
        "events": [f"{plugin_id}.sample.created"],
        "description": f"Plugin {plugin_id}",
    }
    (plugin_root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_root / "README.md").write_text(f"# {plugin_id}\n", encoding="utf-8")
    (plugin_root / "frontend" / "register.ts").write_text(
        "export function registerPlugin() { return { pluginId: '"
        + plugin_id
        + "', routes: [], navigation: [], widgets: [] }; }\n",
        encoding="utf-8",
    )
    (plugin_root / "backend" / "plugin.py").write_text(
        register_body
        or (
            "from systutor.sdk import PluginContext\n\n"
            "def register(context: PluginContext) -> None:\n"
            f"    context.register_permissions(['{plugin_id}.sample.read'])\n"
            f"    context.register_events(['{plugin_id}.sample.created'])\n"
        ),
        encoding="utf-8",
    )

    for migration_name, body in migrations or []:
        (plugin_root / "migrations" / migration_name).write_text(body, encoding="utf-8")


def _build_runtime_environment(
    tmp_path: Path,
    test_settings: Settings,
) -> tuple[Settings, PluginManifestRegistry]:
    plugins_dir = tmp_path / "plugins"
    settings = test_settings.model_copy(
        update={
            "database_url": f"sqlite+pysqlite:///{tmp_path / 'persistent_runtime.db'}",
            "plugins_dir": plugins_dir,
        }
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    registry = PluginManifestRegistry(plugins_dir)
    return settings, registry


def _load_runtime(db: Session, registry: PluginManifestRegistry):
    registry.discover()

    def context_builder(manifest):
        return PluginContext(manifest)

    return build_persistent_plugin_runtime(
        db,
        registry=registry,
        context_builder=context_builder,
    )


def _create_log_migration_script(revision: str = "0001") -> str:
    return (
        "from sqlalchemy import text\n\n"
        f"revision = '{revision}'\n\n"
        "def upgrade(db):\n"
        '    db.execute(text("CREATE TABLE billing_migration_log "'
        '"(revision TEXT PRIMARY KEY, applied INTEGER NOT NULL)"))\n'
        '    db.execute(text("INSERT INTO billing_migration_log "'
        f"\"(revision, applied) VALUES ('{revision}', 1)\"))\n\n"
        "def downgrade(db):\n"
        '    db.execute(text("DROP TABLE billing_migration_log"))\n'
    )


def _marker_migration_script(*, revision: str = "0002", fail: bool = False) -> str:
    body = (
        "from sqlalchemy import text\n\n"
        f"revision = '{revision}'\n\n"
        "def upgrade(db):\n"
        '    db.execute(text("INSERT INTO billing_migration_log "'
        f"\"(revision, applied) VALUES ('{revision}', 1)\"))\n"
    )
    if fail:
        body += "    raise RuntimeError('migration exploded')\n"
    body += (
        "\ndef downgrade(db):\n"
        '    db.execute(text("DELETE FROM billing_migration_log "'
        f"\"WHERE revision = '{revision}'\"))\n"
    )
    return body


def _fetch_migration_revisions(db: Session) -> list[str]:
    query = text("SELECT revision FROM billing_migration_log ORDER BY revision ASC")
    return list(db.execute(query).scalars())


def test_plugin_runtime_debug_endpoints_cover_install_enable_disable(
    client,
    app,
    seeded_demo: dict[str, str],
) -> None:
    headers = auth_headers(client)

    list_response = client.get("/api/v1/system/plugin-runtime", headers=headers)
    assert list_response.status_code == 200
    assert any(item["plugin_id"] == "alpha" for item in list_response.json())

    disable_response = client.post("/api/v1/plugin-runtime/alpha/disable", headers=headers)
    assert disable_response.status_code == 200
    assert disable_response.json()["state"] == "disabled"
    assert disable_response.json()["is_enabled"] is False

    crm_install_response = client.post("/api/v1/plugin-runtime/beta/install", headers=headers)
    assert crm_install_response.status_code == 200
    crm_enable_response = client.post("/api/v1/plugin-runtime/beta/enable", headers=headers)
    assert crm_enable_response.status_code == 200

    install_response = client.post("/api/v1/plugin-runtime/alpha/install", headers=headers)
    assert install_response.status_code == 200
    assert install_response.json()["state"] == "installed"
    assert install_response.json()["installed_at"] is not None

    enable_response = client.post("/api/v1/plugin-runtime/alpha/enable", headers=headers)
    assert enable_response.status_code == 200
    assert enable_response.json()["state"] == "enabled"
    assert enable_response.json()["is_enabled"] is True

    debug_response = client.get("/api/v1/plugin-runtime/debug", headers=headers)
    assert debug_response.status_code == 200
    alpha = next(item for item in debug_response.json() if item["plugin_id"] == "alpha")
    assert alpha["state"] == "enabled"


def test_plugin_runtime_state_persists_after_reboot(
    client,
    app,
    seeded_demo: dict[str, str],
) -> None:
    headers = auth_headers(client)
    crm_enable_response = client.post("/api/v1/plugin-runtime/beta/enable", headers=headers)
    assert crm_enable_response.status_code == 200
    enable_response = client.post("/api/v1/plugin-runtime/alpha/enable", headers=headers)
    assert enable_response.status_code == 200

    rebooted_app = create_app(app.state.settings)
    alpha = next(
        result
        for result in rebooted_app.state.plugin_runtime.list_results()
        if result.manifest is not None and result.manifest.id == "alpha"
    )

    assert alpha.status == "enabled"
    assert alpha.registration is not None

    with rebooted_app.state.session_factory() as db:
        record = get_plugin_registry_record_by_plugin_id(db, plugin_id="alpha")
        assert record is not None
        assert record.state == "enabled"
        assert record.enabled_at is not None


def test_plugin_runtime_marks_failed_plugin(tmp_path: Path, test_settings: Settings) -> None:
    settings, registry = _build_runtime_environment(tmp_path, test_settings)
    _write_plugin(
        settings.plugins_dir / "broken",
        plugin_id="broken",
        register_body=(
            "from systutor.sdk import PluginContext\n\n"
            "def register(context: PluginContext) -> None:\n"
            "    context.register_permissions(['wrong.sample.read'])\n"
        ),
    )
    session_factory = build_session_factory(settings)

    with session_factory() as db:
        registry.discover()
        with pytest.raises(PluginRuntimeError, match="undeclared plugin permissions"):
            enable_plugin(db, registry=registry, plugin_id="broken")
        _load_runtime(db, registry)
        db.commit()

    with session_factory() as db:
        record = get_plugin_registry_record_by_plugin_id(db, plugin_id="broken")
        assert record is not None
        assert record.state == "failed"
        assert record.last_error is not None
        assert "undeclared plugin permissions" in record.last_error


def test_plugin_migrations_apply_and_respect_order(tmp_path: Path, test_settings: Settings) -> None:
    settings, registry = _build_runtime_environment(tmp_path, test_settings)
    _write_plugin(
        settings.plugins_dir / "billing",
        plugin_id="billing",
        migrations=[
            ("0001_create_log.py", _create_log_migration_script()),
            ("0002_insert_marker.py", _marker_migration_script()),
        ],
    )
    session_factory = build_session_factory(settings)

    with session_factory() as db:
        registry.discover()
        record = install_plugin(db, registry=registry, plugin_id="billing")
        db.commit()

        revisions = _fetch_migration_revisions(db)

        assert record.migration_version == "0002"
        assert revisions == ["0001", "0002"]


def test_plugin_migrations_rollback_on_failure(tmp_path: Path, test_settings: Settings) -> None:
    settings, registry = _build_runtime_environment(tmp_path, test_settings)
    _write_plugin(
        settings.plugins_dir / "billing",
        plugin_id="billing",
        migrations=[
            ("0001_create_log.py", _create_log_migration_script()),
            ("0002_fail.py", _marker_migration_script(fail=True)),
        ],
    )
    session_factory = build_session_factory(settings)

    with session_factory() as db:
        registry.discover()
        record = upgrade_plugin(
            db,
            registry=registry,
            plugin_id="billing",
            target_revision="0001",
        )
        db.commit()
        assert record.migration_version == "0001"

    with session_factory() as db:
        registry.discover()
        try:
            upgrade_plugin(db, registry=registry, plugin_id="billing")
        except RuntimeError as exc:
            assert str(exc) == "migration exploded"
        else:  # pragma: no cover - defensive assertion for test correctness
            raise AssertionError("Expected migration failure")
        db.rollback()

        revisions = _fetch_migration_revisions(db)
        record = get_plugin_registry_record_by_plugin_id(db, plugin_id="billing")

        assert record is not None
        assert record.migration_version == "0001"
        assert revisions == ["0001"]


def test_plugin_migrations_downgrade_and_idempotency(
    tmp_path: Path,
    test_settings: Settings,
) -> None:
    settings, registry = _build_runtime_environment(tmp_path, test_settings)
    _write_plugin(
        settings.plugins_dir / "billing",
        plugin_id="billing",
        migrations=[
            ("0001_create_log.py", _create_log_migration_script()),
            ("0002_insert_marker.py", _marker_migration_script()),
        ],
    )
    session_factory = build_session_factory(settings)

    with session_factory() as db:
        registry.discover()
        record = install_plugin(db, registry=registry, plugin_id="billing")
        record = upgrade_plugin(db, registry=registry, plugin_id="billing")
        db.commit()
        assert record.migration_version == "0002"

    with session_factory() as db:
        registry.discover()
        record = downgrade_plugin(
            db,
            registry=registry,
            plugin_id="billing",
            target_revision="0001",
        )
        db.commit()
        revisions = _fetch_migration_revisions(db)
        assert record.migration_version == "0001"
        assert revisions == ["0001"]

    with session_factory() as db:
        registry.discover()
        record = rollback_plugin(db, registry=registry, plugin_id="billing")
        db.commit()
        assert record.migration_version is None

    with session_factory() as db:
        registry.discover()
        record = upgrade_plugin(db, registry=registry, plugin_id="billing")
        record = upgrade_plugin(db, registry=registry, plugin_id="billing")
        db.commit()
        revisions = _fetch_migration_revisions(db)
        assert record.migration_version == "0002"
        assert revisions == ["0001", "0002"]
