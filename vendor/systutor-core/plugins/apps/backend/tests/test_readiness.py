from __future__ import annotations

from pathlib import Path

from plugins.apps.backend.readiness import secret_available


def test_secret_availability_checks_only_existence(tmp_path: Path) -> None:
    key = tmp_path / "apps_key"
    assert secret_available(key) is False
    key.write_bytes(b"x" * 32)
    assert secret_available(key) is True
    assert secret_available(tmp_path) is False
