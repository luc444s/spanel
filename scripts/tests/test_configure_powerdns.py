from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "configure_powerdns.sh"


def test_script_is_executable_and_syntax_is_valid() -> None:
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_plan_is_read_only_and_never_needs_a_secret(tmp_path: Path) -> None:
    state = tmp_path / "state"
    metadata = tmp_path / "powerdns.env"
    result = subprocess.run(
        [
            str(SCRIPT),
            "plan",
            "--state-dir",
            str(state),
            "--powerdns-env",
            str(metadata),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "read-only" in result.stdout
    assert "API key mount source" in result.stdout
    assert not state.exists()
    assert not metadata.exists()


def test_source_mounts_key_path_and_never_serializes_key_value() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "SPANEL_PDNS_API_KEY_PATH=$key_path" in source
    assert "SPANEL_PDNS_API_KEY=" not in source
    assert "mode 0600" in source
    assert "docker-compose.powerdns.yml" in source
