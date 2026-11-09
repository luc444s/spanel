"""Checkout validation and Compose source discovery."""
from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from .transport import Runner
from .types import COMPOSE_NAMES, AppDeployError


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_inside(path: Path, root: Path, what: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AppDeployError(f"{what} does not exist or cannot be resolved") from exc
    if not _inside(resolved, root):
        raise AppDeployError(f"{what} escapes the checkout")
    return resolved


def validate_checkout(root: Path) -> Path:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise AppDeployError("source directory is not a directory")
    for path in root.rglob("*"):
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise AppDeployError("checkout contains an unreadable path") from exc
        if stat.S_ISLNK(mode):
            _resolve_inside(path, root, f"symlink {path.relative_to(root)}")
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise AppDeployError(
                f"checkout contains special file: {path.relative_to(root)}"
            )
    return root


def clone_repo(
    repo: str, branch: str, destination: Path, runner: Runner | None = None
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise AppDeployError("repository must be a GitHub owner/name identifier")
    if not branch or branch.startswith("-") or any(ch in branch for ch in "\x00\r\n"):
        raise AppDeployError("invalid branch")
    local = runner or Runner("local")
    local.run(
        [
            "gh",
            "repo",
            "clone",
            repo,
            str(destination),
            "--",
            "--branch",
            branch,
            "--single-branch",
        ],
        timeout=600,
    )


def discover_compose(root: Path, selected: str | None = None) -> Path:
    root = root.resolve(strict=True)
    if selected:
        candidate = _resolve_inside(root / selected, root, "Compose file")
        if not candidate.is_file():
            raise AppDeployError("Compose selection is not a regular file")
        return candidate
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_file()
        and (
            path.name in COMPOSE_NAMES
            or re.fullmatch(r"docker-compose\..+\.ya?ml", path.name)
        )
    )
    if not candidates:
        raise AppDeployError("no Compose file found")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise AppDeployError(
            f"multiple Compose files found; use --compose-file: {names}"
        )
    return candidates[0]


def _node_lines(node: Node, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], int]:
    result: dict[tuple[str, ...], int] = {}
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            key = str(getattr(key_node, "value", ""))
            current = (*prefix, key)
            result[current] = key_node.start_mark.line + 1
            result.update(_node_lines(value_node, current))
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            current = (*prefix, str(index))
            result[current] = value_node.start_mark.line + 1
            result.update(_node_lines(value_node, current))
    return result


def _load_compose(path: Path) -> tuple[dict[str, Any], dict[tuple[str, ...], int]]:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AppDeployError("Compose file is unreadable or invalid YAML") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("services"), dict)
        or not data["services"]
    ):
        raise AppDeployError("Compose must contain a non-empty services mapping")
    return data, _node_lines(node) if node else {}


def _line(lines: dict[tuple[str, ...], int], *parts: str) -> int:
    probe = tuple(parts)
    while probe:
        if probe in lines:
            return lines[probe]
        probe = probe[:-1]
    return 1
