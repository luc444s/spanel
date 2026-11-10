"""Shared runtime types and constants."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OWNER_LABEL = "io.spanel.app.owner"
MANIFEST = "manifest.json"
COMPOSE_NAMES = {
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
}
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
SAFE_REMOTE_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
SECRET_NAME_RE = re.compile(
    r"(?:secret|password|passwd|token|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
ROOT_USERS = {"", "0", "root", "0:0", "root:root"}
MOUNT_BLOCKED = ("/", "/proc", "/sys", "/dev", "/var/run")


class AppDeployError(RuntimeError):
    """Expected, safely printable failure."""


@dataclass(frozen=True, order=True)
class Finding:
    severity: str
    code: str
    service: str | None
    path: str
    line: int
    message: str
    remediation: str


@dataclass
class AuditResult:
    root: Path
    compose: Path
    data: dict[str, Any]
    dockerfiles: dict[str, Path]
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]


@dataclass
class Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class DeployOptions:
    repo: str | None = None
    branch: str = "main"
    env_file: Path | None = None
    dry_run: bool = False
    project: str | None = None
    transport: str = "local"
    non_root: bool = False
    timeout: int = 1800
    wait: int = 180
    release_id: str | None = None
    publish_state: bool = True


@dataclass(frozen=True)
class LifecycleOptions:
    command: str
    project: str
    transport: str = "local"
    tail: int = 200
    purge_data: bool = False
    confirm_purge: str | None = None
    release: Path | None = None
    service: str | None = None


@dataclass(frozen=True)
class LifecycleResult:
    returncode: int
    output: str = ""
