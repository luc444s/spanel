from __future__ import annotations

import json
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import systutor.kernel.models  # noqa: F401, E402
from app.main import create_app  # noqa: E402
from systutor.api.seed import seed_demo_data  # noqa: E402
from systutor.core.config import Settings  # noqa: E402
from systutor.core.database import Base, build_engine, build_session_factory  # noqa: E402


def write_fake_plugin(
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


@pytest.fixture()
def plugins_dir(tmp_path: Path) -> Path:
    plugins_root = tmp_path / "plugins"
    write_fake_plugin(plugins_root / "alpha", plugin_id="alpha")
    write_fake_plugin(plugins_root / "beta", plugin_id="beta")
    return plugins_root


@pytest.fixture()
def test_settings(tmp_path: Path, plugins_dir: Path) -> Settings:
    database_path = tmp_path / "systutor_test.db"
    return Settings(
        app_name="SYSTUTOR API Test",
        env="test",
        debug=True,
        version="0.2.0-test",
        api_prefix="/api/v1",
        log_level="WARNING",
        database_url=f"sqlite+pysqlite:///{database_path}",
        redis_url="redis://localhost:6379/15",
        outbox_dispatch_batch_size=25,
        outbox_max_retries=2,
        jwt_secret_key="test-secret-key",
        jwt_access_token_ttl_minutes=30,
        plugins_dir=plugins_dir,
        seed_demo_tenant_name="Demo Tenant",
        seed_demo_tenant_slug="demo",
        seed_demo_branch_name="Main Branch",
        seed_demo_branch_code="MAIN",
        seed_admin_email="admin@example.com",
        seed_admin_password="ChangeMe123!",
        seed_admin_full_name="System Admin",
    )


@pytest.fixture()
def engine(test_settings: Settings) -> Generator[Engine, None, None]:
    engine = build_engine(test_settings)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def app(test_settings: Settings, engine: Engine):
    app = create_app(test_settings)
    app.state.session_factory = build_session_factory(test_settings)
    return app


@pytest.fixture()
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def db_session(app) -> Generator[Session, None, None]:
    session_factory = app.state.session_factory
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded_demo(app, db_session: Session) -> dict[str, str]:
    return seed_demo_data(db_session, app.state.settings, app.state.plugin_runtime.list_results())
