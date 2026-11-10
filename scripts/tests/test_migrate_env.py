from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor/systutor-core"))
SPEC = importlib.util.spec_from_file_location("migrate_env", ROOT / "scripts/migrate_env.py")
assert SPEC and SPEC.loader
migrate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migrate
SPEC.loader.exec_module(migrate)


def test_parse_env_text_ignores_comments_and_quotes_values() -> None:
    values = migrate.parse_env_text(
        "# comment\n"
        "DATABASE_URL=postgresql://${APP_DB_USER}:${APP_DB_PASSWORD}@database:5432/db\n"
        "export JWT_SECRET_KEY='secret value'\n"
        "EMPTY=\n"
        "INVALID LINE\n"
    )
    assert values == {
        "DATABASE_URL": "postgresql://${APP_DB_USER}:${APP_DB_PASSWORD}@database:5432/db",
        "JWT_SECRET_KEY": "secret value",
        "EMPTY": "",
    }


def test_tenant_entries_removes_only_managed_credentials() -> None:
    entries = migrate.tenant_entries({
        "APP_DB_NAME": "app_123",
        "APP_DB_USER": "app_123",
        "APP_DB_PASSWORD": "managed",
        "DATABASE_URL": "postgresql://${APP_DB_USER}:${APP_DB_PASSWORD}@db/x",
        "JWT_SECRET_KEY": "tenant",
    })
    assert entries == [
        ("DATABASE_URL", "postgresql://${APP_DB_USER}:${APP_DB_PASSWORD}@db/x"),
        ("JWT_SECRET_KEY", "tenant"),
    ]


def test_release_env_path_finds_private_release_copy(tmp_path: Path) -> None:
    release = tmp_path / "releases" / "op-1"
    private = tmp_path / "private" / "op-1"
    private.mkdir(parents=True)
    (private / "app.env").write_text("A=1\n", encoding="utf-8")
    deployment = type("Deployment", (), {"manifest": {"release": str(release)}})
    assert migrate.release_env_path(deployment, tmp_path) == private / "app.env"
