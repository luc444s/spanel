"""Owned deployment lifecycle operations."""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .deployment import _compose_command, _ensure_owned, _state_root, _write_manifest
from .transport import Runner
from .types import (
    MANIFEST,
    OWNER_LABEL,
    PROJECT_RE,
    AppDeployError,
    LifecycleOptions,
    LifecycleResult,
)


def _read_manifest(runner: Runner, root: Path, project: str) -> dict[str, Any]:
    if not PROJECT_RE.fullmatch(project):
        raise AppDeployError("invalid project")
    path = root / project / MANIFEST
    if runner.transport == "ssh":
        result = runner.run(["cat", str(path)])
        text = result.stdout
    else:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AppDeployError("owned project manifest not found") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppDeployError("owned project manifest is invalid") from exc
    if data.get("project") != project or data.get("owner_label") != OWNER_LABEL:
        raise AppDeployError("manifest does not prove runner ownership")
    expected = root / project
    if data.get("release") != str(expected):
        raise AppDeployError("manifest release path is inconsistent")
    return data


def _redaction_values(runner: Runner, release: Path) -> list[str]:
    path = release / "app.env"
    if runner.transport == "ssh":
        content = runner.run(["cat", str(path)]).stdout
    else:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppDeployError(
                "cannot read staged env file for log redaction"
            ) from exc
    values: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if len(value) >= 3:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def _redact(text: str, values: Iterable[str]) -> str:
    for value in values:
        text = text.replace(value, "[REDACTED]")
    return text


def lifecycle(options: LifecycleOptions, runner: Runner) -> LifecycleResult:
    root = _state_root(options.transport, runner.environ)
    manifest = _read_manifest(runner, root, options.project)
    _ensure_owned(runner, options.project)
    release = root / options.project
    command = _compose_command(
        release, manifest["compose"], options.project, bool(manifest.get("has_env"))
    )
    if options.command == "status":
        result = runner.run([*command, "ps", "--format", "json"], check=False)
        return LifecycleResult(result.returncode, result.stdout)
    if options.command == "logs":
        result = runner.run(
            [*command, "logs", "--no-color", "--tail", str(options.tail)], check=False
        )
        output = _redact(result.stdout, _redaction_values(runner, release))
        return LifecycleResult(result.returncode, output)
    tail = ["down", "--remove-orphans"]
    if options.purge_data:
        if options.confirm_purge != options.project:
            raise AppDeployError(
                "--purge-data requires --confirm-purge equal to the project"
            )
        tail.append("--volumes")
    runner.run([*command, *tail])
    manifest["status"] = "down"
    _write_manifest(runner, release, manifest)
    return LifecycleResult(0)
