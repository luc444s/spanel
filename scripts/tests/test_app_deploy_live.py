from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "app_deploy_live_module", ROOT / "scripts/app_deploy.py"
)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)

LIVE = os.environ.get("SPANEL_LIVE_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="live app deployment requires SPANEL_LIVE_TESTS=1 and explicit lab configuration",
)


def test_target_application_stays_running_healthy_and_non_root() -> None:
    """Destructive opt-in E2E; intentionally leaves the lab application running."""
    source_text = os.environ.get("SPANEL_APP_LIVE_SOURCE_DIR", "").strip()
    env_text = os.environ.get("SPANEL_APP_LIVE_ENV_FILE", "").strip()
    project = os.environ.get("SPANEL_APP_LIVE_PROJECT", "editors-lab")
    if not source_text or not env_text:
        pytest.fail(
            "SPANEL_APP_LIVE_SOURCE_DIR and SPANEL_APP_LIVE_ENV_FILE are required"
        )
    source = Path(source_text)
    result = app.audit_project(source, non_root=True, require_build=True)
    assert result.errors == []
    args = app.build_parser().parse_args(
        [
            "deploy",
            "--source-dir",
            str(source),
            "--branch",
            "feature/workflow-diseno",
            "--non-root",
            "--require-build",
            "--env-file",
            env_text,
            "--transport",
            "ssh",
            "--project",
            project,
        ]
    )
    runner = app.Runner("ssh")
    manifest = app.deploy(app._deploy_options(args), result, runner)
    assert manifest is not None
    assert manifest["status"] == "running"
    assert set(manifest["services"]) == {"frontend", "backend", "database"}
    assert set(manifest["containers"]) == set(manifest["services"])
    assert manifest["commit"] != "unknown"
    assert len(manifest["tree_hash"]) == 64
