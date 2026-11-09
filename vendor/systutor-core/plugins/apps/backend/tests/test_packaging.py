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


def test_worker_image_installs_and_verifies_docker_compose_v2() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ARG DOCKER_COMPOSE_VERSION=\d+\.\d+\.\d+", dockerfile)
    assert "docker-compose-linux-x86_64" in dockerfile
    assert "/usr/local/lib/docker/cli-plugins/docker-compose" in dockerfile
    assert "docker compose version" in dockerfile
