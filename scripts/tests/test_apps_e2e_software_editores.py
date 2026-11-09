from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "e2e_apps_software_editores",
    ROOT / "scripts/e2e_apps_software_editores.py",
)
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e2e
SPEC.loader.exec_module(e2e)


def test_generated_environment_matches_editors_contract_without_leaking() -> None:
    values, sensitive = e2e.generated_environment("0123456789abcdef")
    assert values["VITE_API_BASE_URL"] == "/api"
    assert values["RUN_DB_MIGRATIONS"] == "true"
    assert values["CLOUDFLARE_R2_ENABLED"] == "false"
    assert values["DATABASE_URL"].startswith(
        "postgresql://app_e2e_0123456789abcdef:"
    )
    assert values["APP_DB_PASSWORD"] in sensitive
    assert values["DATABASE_URL"] in sensitive
    assert values["API_KEY"] in sensitive
    rendered = e2e.redact_text(json.dumps(values), sensitive)
    assert values["APP_DB_PASSWORD"] not in rendered
    assert values["API_KEY"] not in rendered


def test_environment_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "app.env"
    e2e.write_environment(path, {"VITE_API_BASE_URL": "/api", "A": "b"})
    assert path.read_text() == "VITE_API_BASE_URL=/api\nA=b\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_image_name_is_stable_and_project_scoped() -> None:
    first = e2e.image_name("project-a", "frontend")
    assert first == e2e.image_name("project-a", "frontend")
    assert first != e2e.image_name("project-b", "frontend")
    assert first.startswith("spanel-app-")
    assert first.endswith(":latest")
