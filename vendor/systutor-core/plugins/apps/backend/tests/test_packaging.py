from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def test_worker_image_installs_git_for_gh_repo_clone() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r"apt-get install -y --no-install-recommends(?P<packages>.*?)&&",
        dockerfile,
        re.DOTALL,
    )
    assert match is not None
    packages = set(re.findall(r"[A-Za-z0-9.+-]+", match.group("packages")))
    assert {"git", "gh"} <= packages
