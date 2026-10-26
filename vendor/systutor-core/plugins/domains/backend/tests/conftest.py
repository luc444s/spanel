from __future__ import annotations

import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[4]
# Test the checkout, not whatever editable install is in the environment.
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))


@pytest.fixture(autouse=True)
def isolate_proxy_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's `.env` from leaking a profile into unit tests.

    `hosting.settings` loads the repo `.env` into `os.environ`. The domains
    transport then reads `SPANEL_DOMAINS_PROFILE` from the process, not from
    the explicit `Settings.from_env(..., env=...)` mapping. Tests that build
    `public` settings must not flip to `internal` because of that file.
    """
    monkeypatch.delenv("SPANEL_DOMAINS_PROFILE", raising=False)
    monkeypatch.delenv("SPANEL_PROXY_PROFILE", raising=False)
