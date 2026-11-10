from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor/systutor-core"))
SPEC = importlib.util.spec_from_file_location(
    "install_routes_timer", ROOT / "scripts/install_routes_timer.py"
)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def test_service_uses_actual_repo_and_docker_path(tmp_path: Path) -> None:
    text = installer.render_service(tmp_path, "/usr/local/bin/docker")
    assert f"WorkingDirectory={tmp_path}" in text
    assert "ExecStart=/usr/local/bin/docker compose" in text
    assert "/opt/spanel" not in text


def test_timer_respects_custom_interval() -> None:
    text = installer.render_timer(15)
    assert "OnUnitActiveSec=15min" in text
    assert "OnUnitActiveSec=5min" not in text
