"""Dockerfile and low-level Compose audit helpers."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .source import _inside
from .types import MOUNT_BLOCKED, ROOT_USERS, SECRET_NAME_RE, AppDeployError, Finding


def _finding(
    findings: list[Finding],
    severity: str,
    code: str,
    service: str | None,
    path: str,
    line: int,
    message: str,
    remediation: str,
) -> None:
    findings.append(Finding(severity, code, service, path, line, message, remediation))


def _is_root_user(value: Any) -> bool:
    text = str(value).strip().lower()
    first = text.split(":", 1)[0]
    return (
        text in ROOT_USERS or first == "root" or (first.isdigit() and int(first) == 0)
    )


def _is_definitely_non_root_user(value: Any) -> bool:
    text = str(value).strip()
    if _is_root_user(text) or "$" in text:
        return False
    user = text.split(":", 1)[0]
    if user.isdigit():
        return int(user) > 0
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", user))


def _blocked_mount_target(target: str) -> bool:
    normalized = "/" + target.strip().lstrip("/")
    return any(
        normalized == item or normalized.startswith(item + "/")
        for item in MOUNT_BLOCKED
    )


def _audit_volume(
    volume: Any,
    root: Path,
    compose_dir: Path,
    compose_rel: str,
    service: str,
    line: int,
    findings: list[Finding],
) -> None:
    source = ""
    target = ""
    bind = False
    if isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) >= 2:
            source, target = parts[0], parts[1]
            bind = source.startswith((".", "/")) or "/" in source or "\\" in source
        else:
            target = parts[0]
    elif isinstance(volume, dict):
        source = str(volume.get("source", ""))
        target = str(volume.get("target", ""))
        bind = volume.get("type") == "bind"
    else:
        _finding(
            findings,
            "error",
            "compose_mount_invalid",
            service,
            compose_rel,
            line,
            "Volume entry is not a supported string or mapping.",
            "Use a standard Compose volume entry.",
        )
        return
    if _blocked_mount_target(target):
        _finding(
            findings,
            "error",
            "compose_mount_sensitive",
            service,
            compose_rel,
            line,
            "Volume targets a sensitive container filesystem path.",
            "Use a dedicated application data path.",
        )
    lowered = source.rstrip("/").lower()
    if lowered.endswith("/docker.sock") or lowered == "docker.sock":
        _finding(
            findings,
            "error",
            "compose_docker_socket",
            service,
            compose_rel,
            line,
            "Docker daemon socket mount is forbidden.",
            "Remove the Docker socket mount.",
        )
    if bind and source:
        if Path(source).is_absolute():
            _finding(
                findings,
                "error",
                "compose_bind_absolute",
                service,
                compose_rel,
                line,
                "Absolute host bind mount is forbidden.",
                "Use a named volume or a checkout-relative path.",
            )
        else:
            candidate = (compose_dir / source).resolve(strict=False)
            if not _inside(candidate, root):
                _finding(
                    findings,
                    "error",
                    "compose_bind_escape",
                    service,
                    compose_rel,
                    line,
                    "Bind mount escapes the checkout.",
                    "Keep relative bind sources inside the checkout.",
                )


def _audit_environment(
    value: Any,
    compose_rel: str,
    service: str,
    line: int,
    findings: list[Finding],
) -> None:
    pairs: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        pairs = [(str(key), item) for key, item in value.items()]
    elif isinstance(value, list):
        for item in value:
            key, separator, content = str(item).partition("=")
            pairs.append((key, content if separator else None))
    elif value is not None:
        _finding(
            findings,
            "error",
            "compose_environment_invalid",
            service,
            compose_rel,
            line,
            "Service environment must be a mapping or list.",
            "Use standard Compose environment syntax.",
        )
        return
    for key, content in pairs:
        text = "" if content is None else str(content).strip()
        if SECRET_NAME_RE.search(key) and text and not text.startswith("${"):
            _finding(
                findings,
                "error",
                "compose_inline_secret",
                service,
                compose_rel,
                line,
                "A secret-like environment key has a literal versioned value.",
                "Reference a variable supplied by the external private env file.",
            )


def _dockerfile_instructions(path: Path) -> list[tuple[str, str, int]]:
    try:
        physical = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AppDeployError(f"Dockerfile is unreadable: {path.name}") from exc
    instructions: list[tuple[str, str, int]] = []
    buffer = ""
    start = 1
    for number, raw in enumerate(physical, 1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        piece = raw.rstrip()
        continued = piece.endswith("\\")
        buffer += piece[:-1] + " " if continued else piece
        if not continued:
            match = re.match(r"^\s*([A-Za-z]+)\s+(.*)$", buffer, re.DOTALL)
            if match:
                instructions.append(
                    (match.group(1).upper(), match.group(2).strip(), start)
                )
            buffer = ""
    if buffer:
        match = re.match(r"^\s*([A-Za-z]+)\s+(.*)$", buffer, re.DOTALL)
        if match:
            instructions.append((match.group(1).upper(), match.group(2).strip(), start))
    return instructions


def _audit_dockerfile(
    path: Path, root: Path, service: str, non_root: bool, findings: list[Finding]
) -> None:
    rel = path.relative_to(root).as_posix()
    instructions = _dockerfile_instructions(path)
    final_user: tuple[str, int] | None = None
    health = False
    for command, value, line in instructions:
        if command == "FROM":
            final_user = None
            health = False
        elif command == "USER":
            final_user = (value.split()[0], line)
        elif command == "HEALTHCHECK":
            health = True
        if command == "ADD" and re.search(r"(?:https?|git)://", value, re.IGNORECASE):
            _finding(
                findings,
                "warning",
                "dockerfile_remote_add",
                service,
                rel,
                line,
                "Remote ADD delegates network content handling to the build.",
                "Download, verify, and COPY a pinned artifact.",
            )
        if command == "RUN" and re.search(
            r"(?:curl|wget)\b[^|\n]*\|\s*(?:sh|bash)\b", value, re.IGNORECASE
        ):
            _finding(
                findings,
                "warning",
                "dockerfile_pipe_shell",
                service,
                rel,
                line,
                "A network download is piped directly to a shell.",
                "Download, verify, then execute the pinned file.",
            )
        if command == "RUN" and re.search(r"\bchmod\s+(?:-[^ ]+\s+)*777\b", value):
            _finding(
                findings,
                "warning",
                "dockerfile_chmod_777",
                service,
                rel,
                line,
                "World-writable executable or data permissions were requested.",
                "Use the narrowest owner/group permissions.",
            )
        if command == "RUN" and re.search(
            r"(?:\bsetuid\b|\bchmod\s+[^\n]*(?:[24][0-7]{3}|u\+s)|/sudoers\b)",
            value,
            re.IGNORECASE,
        ):
            _finding(
                findings,
                "warning",
                "dockerfile_privilege_artifact",
                service,
                rel,
                line,
                "Potential setuid or sudoers modification found.",
                "Remove privilege escalation artifacts.",
            )
        if command in {"ARG", "ENV"}:
            names = re.findall(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)", value)
            if any(SECRET_NAME_RE.search(name) for name in names):
                _finding(
                    findings,
                    "warning",
                    "dockerfile_apparent_secret",
                    service,
                    rel,
                    line,
                    "ARG/ENV name appears to carry secret material.",
                    "Use runtime secret injection; never bake secret values into images.",
                )
    if non_root and (
        final_user is None or not _is_definitely_non_root_user(final_user[0])
    ):
        line = final_user[1] if final_user else 1
        _finding(
            findings,
            "error",
            "dockerfile_final_user_root",
            service,
            rel,
            line,
            "Final stage does not declare a definitely non-root USER.",
            "Create a dedicated account and set USER in the final stage.",
        )
    if not health:
        _finding(
            findings,
            "info",
            "dockerfile_no_healthcheck",
            service,
            rel,
            1,
            "Final stage has no HEALTHCHECK; Compose may provide one.",
            "Add HEALTHCHECK here or healthcheck in Compose.",
        )
