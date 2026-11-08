#!/usr/bin/env python3
"""Audit and deploy an untrusted, generic Docker Compose application.

The static audit is deliberately conservative.  Passing it is not proof that
an image or its build is safe: a Docker build executes code supplied by the
checkout.  Deployments use the current Docker daemon; ``--non-root`` refers to
container processes and never selects a different daemon.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

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


class Runner:
    """Run argv locally or through SSH without placing passwords in argv."""

    def __init__(self, transport: str = "local", environ: dict[str, str] | None = None):
        self.transport = transport
        self.environ = dict(os.environ if environ is None else environ)
        if transport == "ssh":
            self.host = self.environ.get("SPANEL_DOCKER_SSH_HOST", "").strip()
            self.user = self.environ.get("SPANEL_DOCKER_SSH_USER", "").strip()
            self.port = self.environ.get("SPANEL_DOCKER_SSH_PORT", "22").strip()
            self.strict = self.environ.get(
                "SPANEL_DOCKER_SSH_STRICT_HOST_KEY", "yes"
            ).strip()
            self.password = self.environ.get("SPANEL_DOCKER_SSH_PASSWORD", "").strip()
            if not self.password:
                self.password = self.environ.get("MAIL_SERVER_PASSWORD", "").strip()
            if not self.host or not self.user or not self.port.isdigit():
                raise AppDeployError(
                    "SSH requires valid SPANEL_DOCKER_SSH_HOST/USER/PORT"
                )

    def _ssh_argv(self, command: str) -> tuple[list[str], dict[str, str]]:
        argv: list[str] = []
        env = dict(self.environ)
        if self.password:
            binary = shutil.which("sshpass")
            if not binary:
                raise AppDeployError(
                    "sshpass is required for configured SSH password authentication"
                )
            argv.extend([binary, "-e"])
            env["SSHPASS"] = self.password
        argv.extend(
            [
                "ssh",
                "-p",
                self.port,
                "-o",
                f"StrictHostKeyChecking={self.strict}",
                "--",
                f"{self.user}@{self.host}",
                command,
            ]
        )
        return argv, env

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        timeout: int = 600,
        check: bool = True,
    ) -> Completed:
        if self.transport == "ssh":
            if cwd is not None:
                command = f"cd {shlex.quote(str(cwd))} && {shlex.join(list(argv))}"
            else:
                command = shlex.join(list(argv))
            actual, env = self._ssh_argv(command)
            actual_cwd = None
        else:
            actual, env, actual_cwd = list(argv), self.environ, cwd
        try:
            result = subprocess.run(
                actual,
                cwd=actual_cwd,
                input=stdin,
                capture_output=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppDeployError(f"command failed to execute: {argv[0]}") from exc
        completed = Completed(
            result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )
        if check and completed.returncode:
            # Command output may contain expanded environment values, so do not echo it.
            raise AppDeployError(f"command failed ({completed.returncode}): {argv[0]}")
        return completed


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


def audit_project(
    root: Path,
    compose_file: str | None = None,
    *,
    non_root: bool = False,
    require_build: bool = False,
) -> AuditResult:
    root = validate_checkout(root)
    compose = discover_compose(root, compose_file)
    data, lines = _load_compose(compose)
    compose_rel = compose.relative_to(root).as_posix()
    findings: list[Finding] = []
    dockerfiles: dict[str, Path] = {}
    services = data["services"]
    for service, config in services.items():
        if not isinstance(service, str) or not isinstance(config, dict):
            raise AppDeployError(
                "Compose service names/configurations must be mappings"
            )
        base = ("services", service)
        for key, code, message, remediation in (
            (
                "privileged",
                "compose_privileged",
                "Privileged containers are forbidden.",
                "Remove privileged and grant no broad host access.",
            ),
            (
                "cap_add",
                "compose_cap_add",
                "Added Linux capabilities are forbidden.",
                "Remove cap_add and redesign around default privileges.",
            ),
            (
                "devices",
                "compose_devices",
                "Host device access is forbidden.",
                "Remove devices from the service.",
            ),
        ):
            if key in config and (key != "privileged" or config[key] is True):
                _finding(
                    findings,
                    "error",
                    code,
                    service,
                    compose_rel,
                    _line(lines, *base, key),
                    message,
                    remediation,
                )
        for key in ("network_mode", "pid", "ipc", "uts", "userns", "cgroup"):
            if str(config.get(key, "")).strip().lower() == "host":
                _finding(
                    findings,
                    "error",
                    f"compose_host_{key}",
                    service,
                    compose_rel,
                    _line(lines, *base, key),
                    f"Host {key} namespace is forbidden.",
                    f"Remove {key}: host.",
                )
        if "container_name" in config:
            _finding(
                findings,
                "error",
                "compose_fixed_container_name",
                service,
                compose_rel,
                _line(lines, *base, "container_name"),
                "Fixed container names can collide with resources outside the project.",
                "Remove container_name and let Compose scope the name.",
            )
        if config.get("ports"):
            _finding(
                findings,
                "error",
                "compose_published_port",
                service,
                compose_rel,
                _line(lines, *base, "ports"),
                "Published host ports are outside this laboratory phase.",
                "Remove ports and use only the project-internal network.",
            )
        if "user" in config and _is_root_user(config["user"]):
            _finding(
                findings,
                "error",
                "compose_root_user",
                service,
                compose_rel,
                _line(lines, *base, "user"),
                "Service explicitly selects root user.",
                "Set a non-root user or inherit the image USER.",
            )
        options = config.get("security_opt", [])
        if isinstance(options, str):
            options = [options]
        if not isinstance(options, list):
            options = [str(options)]
        for index, option in enumerate(options):
            normalized = str(option).lower().replace(" ", "")
            if "unconfined" in normalized or normalized in {
                "no-new-privileges:false",
                "no-new-privileges= false",
                "no-new-privileges=false",
            }:
                _finding(
                    findings,
                    "error",
                    "compose_security_opt_unsafe",
                    service,
                    compose_rel,
                    _line(lines, *base, "security_opt", str(index)),
                    "security_opt disables a confinement control.",
                    "Remove the unsafe security option.",
                )
        volumes = config.get("volumes", [])
        if not isinstance(volumes, list):
            volumes = [volumes]
        for index, volume in enumerate(volumes):
            _audit_volume(
                volume,
                root,
                compose.parent,
                compose_rel,
                service,
                _line(lines, *base, "volumes", str(index)),
                findings,
            )
        if "env_file" in config:
            _finding(
                findings,
                "error",
                "compose_repo_env_file",
                service,
                compose_rel,
                _line(lines, *base, "env_file"),
                "Service env_file could load secrets from the checkout.",
                "Remove env_file and pass one external private file to the runner.",
            )
        _audit_environment(
            config.get("environment"),
            compose_rel,
            service,
            _line(lines, *base, "environment"),
            findings,
        )
        build = config.get("build")
        if build is None:
            if require_build:
                _finding(
                    findings,
                    "error",
                    "compose_build_required",
                    service,
                    compose_rel,
                    _line(lines, *base),
                    "Service does not define build.",
                    "Define a checkout-contained build context and Dockerfile.",
                )
            continue
        if isinstance(build, str):
            context_text, dockerfile_text = build, "Dockerfile"
        elif isinstance(build, dict):
            context_text = str(build.get("context", "."))
            dockerfile_text = str(build.get("dockerfile", "Dockerfile"))
            if str(build.get("network", "")).lower() == "host":
                _finding(
                    findings,
                    "error",
                    "compose_build_host_network",
                    service,
                    compose_rel,
                    _line(lines, *base, "build", "network"),
                    "Build uses the host network.",
                    "Remove build.network: host.",
                )
            if build.get("entitlements"):
                _finding(
                    findings,
                    "error",
                    "compose_build_entitlements",
                    service,
                    compose_rel,
                    _line(lines, *base, "build", "entitlements"),
                    "Build entitlements are forbidden.",
                    "Remove privileged build entitlements.",
                )
            build_args = build.get("args", {})
            if isinstance(build_args, dict):
                for name, value in build_args.items():
                    text = "" if value is None else str(value).strip()
                    if (
                        SECRET_NAME_RE.search(str(name))
                        and text
                        and not text.startswith("${")
                    ):
                        _finding(
                            findings,
                            "error",
                            "compose_inline_build_secret",
                            service,
                            compose_rel,
                            _line(lines, *base, "build", "args", str(name)),
                            "A secret-like build argument has a literal versioned value.",
                            "Remove it from build args and inject secrets only at runtime.",
                        )
        else:
            _finding(
                findings,
                "error",
                "compose_build_invalid",
                service,
                compose_rel,
                _line(lines, *base, "build"),
                "Build configuration is invalid.",
                "Use a context string or mapping.",
            )
            continue
        try:
            context = _resolve_inside(
                compose.parent / context_text, root, f"build context for {service}"
            )
            dockerfile = _resolve_inside(
                context / dockerfile_text, root, f"Dockerfile for {service}"
            )
            if not context.is_dir() or not dockerfile.is_file():
                raise AppDeployError("build context/Dockerfile has wrong type")
        except AppDeployError:
            _finding(
                findings,
                "error",
                "compose_build_escape",
                service,
                compose_rel,
                _line(lines, *base, "build"),
                "Build context or Dockerfile is missing or escapes checkout.",
                "Keep both inside the checkout and avoid escaping symlinks.",
            )
            continue
        dockerfiles[service] = dockerfile
        _audit_dockerfile(dockerfile, root, service, non_root, findings)
    for section in ("volumes", "networks"):
        entries = data.get(section, {}) or {}
        if not isinstance(entries, dict):
            raise AppDeployError(f"Compose {section} must be a mapping")
        for name, config in entries.items():
            if isinstance(config, dict) and config.get("external"):
                _finding(
                    findings,
                    "error",
                    "compose_external_resource",
                    None,
                    compose_rel,
                    _line(lines, section, str(name)),
                    "External resources have uncertain ownership.",
                    "Use a project-owned non-external resource.",
                )
            if isinstance(config, dict) and config.get("name"):
                _finding(
                    findings,
                    "error",
                    "compose_fixed_resource_name",
                    None,
                    compose_rel,
                    _line(lines, section, str(name), "name"),
                    "Fixed resource name can collide outside the project.",
                    "Remove name and let Compose scope the resource.",
                )
    secrets = data.get("secrets", {}) or {}
    if not isinstance(secrets, dict):
        raise AppDeployError("Compose secrets must be a mapping")
    for name, config in secrets.items():
        _finding(
            findings,
            "error",
            "compose_secret_unsupported",
            None,
            compose_rel,
            _line(lines, "secrets", str(name)),
            "Compose-managed secrets are not owned or staged by this primitive runner.",
            "Use the external private env file for this phase.",
        )
        if isinstance(config, dict) and "file" in config:
            _finding(
                findings,
                "error",
                "compose_repo_secret_file",
                None,
                compose_rel,
                _line(lines, "secrets", str(name), "file"),
                "Compose secret file would be loaded from the checkout.",
                "Use the external private env file supported by this primitive runner.",
            )
    findings.sort(
        key=lambda item: (item.path, item.line, item.service or "", item.code)
    )
    return AuditResult(root, compose, data, dockerfiles, findings)


def validate_env_file(path: Path, checkout: Path) -> Path:
    path = path.expanduser()
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AppDeployError("env file does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AppDeployError("env file must be a regular non-symlink file")
    if info.st_mode & (stat.S_IRGRP | stat.S_IROTH):
        raise AppDeployError("env file must not be readable by group or others")
    if _inside(resolved, checkout.resolve(strict=True)):
        raise AppDeployError("env file must be outside the checkout")
    return resolved


def render_report(result: AuditResult, output_format: str = "human") -> str:
    if output_format == "json":
        return json.dumps(
            {
                "compose": result.compose.relative_to(result.root).as_posix(),
                "findings": [asdict(item) for item in result.findings],
                "errors": len(result.errors),
                "warning": "Static heuristics do not prove safety; Docker builds execute untrusted code.",
            },
            sort_keys=True,
            indent=2,
        )
    rows = [
        f"Compose: {result.compose.relative_to(result.root).as_posix()}",
        "WARNING: static heuristics do not prove safety; Docker builds execute untrusted code.",
    ]
    for item in result.findings:
        owner = f" service={item.service}" if item.service else ""
        rows.append(
            f"{item.severity.upper()} {item.code} {item.path}:{item.line}{owner}: "
            f"{item.message} Remediation: {item.remediation}"
        )
    rows.append(f"Summary: errors={len(result.errors)} findings={len(result.findings)}")
    return "\n".join(rows)


def _project_id(repo: str | None, source: Path) -> str:
    seed = repo or source.name
    slug = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-")[-40:] or "app"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:10]
    return f"{slug}-{digest}"[:63]


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        digest.update(rel.encode() + b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return (
        value
        if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40,64}", value)
        else "unknown"
    )


def _override(data: dict[str, Any], project: str, non_root: bool) -> str:
    services: dict[str, Any] = {}
    for service in data["services"]:
        image_key = hashlib.sha256(f"{project}\0{service}".encode()).hexdigest()[:20]
        config: dict[str, Any] = {
            "image": f"spanel-app-{image_key}:latest",
            "labels": {OWNER_LABEL: project},
        }
        if non_root:
            config.update(
                {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
            )
        services[service] = config
    output: dict[str, Any] = {"services": services}
    for section in ("volumes", "networks"):
        if data.get(section):
            output[section] = {
                name: {"labels": {OWNER_LABEL: project}} for name in data[section]
            }
    return yaml.safe_dump(output, sort_keys=True)


def _tar_release(
    root: Path, compose_rel: str, override: str, env_file: Path | None
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz", dereference=False) as archive:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if rel.parts and rel.parts[0] == ".git":
                continue
            archive.add(
                path, arcname=(Path("source") / rel).as_posix(), recursive=False
            )
        override_bytes = override.encode()
        info = tarfile.TarInfo("override.yaml")
        info.size, info.mode = len(override_bytes), 0o600
        archive.addfile(info, io.BytesIO(override_bytes))
        # An explicit (possibly empty) env file prevents Compose from loading
        # a checkout .env implicitly.
        env_bytes = env_file.read_bytes() if env_file else b""
        info = tarfile.TarInfo("app.env")
        info.size, info.mode = len(env_bytes), 0o600
        archive.addfile(info, io.BytesIO(env_bytes))
        marker = compose_rel.encode()
        info = tarfile.TarInfo("compose.path")
        info.size, info.mode = len(marker), 0o600
        archive.addfile(info, io.BytesIO(marker))
    return stream.getvalue()


def _state_root(transport: str, environ: dict[str, str]) -> Path:
    if transport == "ssh":
        value = environ.get("SPANEL_APP_REMOTE_ROOT", "").strip()
        if (
            not value
            or not SAFE_REMOTE_RE.fullmatch(value)
            or ".." in Path(value).parts
        ):
            raise AppDeployError("SSH requires a safe absolute SPANEL_APP_REMOTE_ROOT")
        return Path(value)
    value = environ.get("SPANEL_APP_STATE_ROOT", "").strip()
    return (
        Path(value).expanduser() if value else Path.home() / ".local/state/spanel/apps"
    )


def _stage_release(
    runner: Runner,
    root: Path,
    release: Path,
    compose_rel: str,
    override: str,
    env_file: Path | None,
) -> None:
    payload = _tar_release(root, compose_rel, override, env_file)
    if runner.transport == "ssh":
        command = (
            f"umask 077; mkdir -p {shlex.quote(str(release))}; "
            f"tar -xzf - -C {shlex.quote(str(release))}; "
            f"chmod 600 {shlex.quote(str(release / 'override.yaml'))}"
        )
        command += f" {shlex.quote(str(release / 'app.env'))}"
        argv, env = runner._ssh_argv(command)
        try:
            result = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                env=env,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppDeployError("failed to transfer release over SSH") from exc
        if result.returncode:
            raise AppDeployError("failed to transfer release over SSH")
    else:
        release.mkdir(parents=True, exist_ok=False)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(release, filter="data")


def _compose_command(
    release: Path, compose_rel: str, project: str, env: bool, *tail: str
) -> list[str]:
    args = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(release / "source"),
        "-f",
        str(release / "source" / compose_rel),
    ]
    if env:
        args.extend(["--env-file", str(release / "app.env")])
    args.extend(["-f", str(release / "override.yaml"), *tail])
    return args


def _ensure_owned(runner: Runner, project: str) -> None:
    for kind, listing in (
        (
            "container",
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
        (
            "network",
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
        (
            "volume",
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
        ),
    ):
        found = runner.run(listing).stdout.split()
        for resource in found:
            label = runner.run(
                [
                    "docker",
                    kind,
                    "inspect",
                    "-f",
                    f'{{{{index .Labels "{OWNER_LABEL}"}}}}',
                    resource,
                ],
                check=False,
            )
            if label.returncode or label.stdout.strip() != project:
                raise AppDeployError(f"project name is occupied by an unowned {kind}")


def _image_users(
    runner: Runner,
    release: Path,
    compose_rel: str,
    project: str,
    env: bool,
    services: Iterable[str],
) -> dict[str, str]:
    rendered = runner.run(
        _compose_command(
            release, compose_rel, project, env, "config", "--format", "json"
        )
    ).stdout
    try:
        configured = json.loads(rendered)
        configured_services = configured["services"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AppDeployError("cannot resolve images from normalized Compose config") from exc
    images: dict[str, str] = {}
    for service in services:
        service_config = configured_services.get(service)
        image = service_config.get("image", "") if isinstance(service_config, dict) else ""
        if not isinstance(image, str) or not image.strip() or "\n" in image:
            raise AppDeployError(
                f"cannot resolve exactly one image for service {service}"
            )
        image = image.strip()
        user = runner.run(
            ["docker", "image", "inspect", "-f", "{{.Config.User}}", image]
        ).stdout.strip()
        if not _is_definitely_non_root_user(user):
            raise AppDeployError(
                f"image for service {service} lacks a definite non-root Config.User"
            )
        images[service] = image
    return images


def _containers(
    runner: Runner,
    release: Path,
    compose_rel: str,
    project: str,
    env: bool,
    services: Iterable[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for service in services:
        value = runner.run(
            _compose_command(release, compose_rel, project, env, "ps", "-q", service)
        ).stdout.strip()
        if not value or "\n" in value:
            raise AppDeployError(
                f"service {service} does not have exactly one container"
            )
        result[service] = value
    return result


def _verify_containers(
    runner: Runner,
    containers: dict[str, str],
    project: str,
    non_root: bool,
    wait: int,
) -> None:
    deadline = time.monotonic() + wait
    last = ""
    while time.monotonic() < deadline:
        ready = True
        for service, container in containers.items():
            status = runner.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "-f",
                    "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                    container,
                ],
                check=False,
            ).stdout.strip()
            last = f"{service}: {status}"
            if status not in {"running none", "running healthy"}:
                ready = False
        if ready:
            break
        time.sleep(2)
    else:
        raise AppDeployError(
            f"services did not become running/healthy before timeout ({last})"
        )
    for service, container in containers.items():
        owner = runner.run(
            [
                "docker",
                "container",
                "inspect",
                "-f",
                f'{{{{index .Config.Labels "{OWNER_LABEL}"}}}}',
                container,
            ]
        ).stdout.strip()
        if owner != project:
            raise AppDeployError(
                f"container {service} does not have the expected ownership label"
            )
    if non_root:
        for service, container in containers.items():
            configured = runner.run(
                ["docker", "container", "inspect", "-f", "{{.Config.User}}", container]
            ).stdout.strip()
            if not _is_definitely_non_root_user(configured):
                raise AppDeployError(
                    f"container {service} lacks a definite configured non-root user"
                )
            top = runner.run(
                ["docker", "top", container, "-eo", "pid,uid"], check=False
            )
            if top.returncode:
                raise AppDeployError(
                    f"cannot inspect effective process users for {service}"
                )
            lines = [line.split() for line in top.stdout.splitlines() if line.strip()]
            try:
                uid_index = [item.lower() for item in lines[0]].index("uid")
                users = [row[uid_index].lower() for row in lines[1:]]
            except (IndexError, ValueError):
                users = []
            if not users or any(
                user == "root" or (user.isdigit() and int(user) == 0) for user in users
            ):
                raise AppDeployError(
                    f"container {service} has root or uninspectable effective processes"
                )


def _manifest_data(
    args: argparse.Namespace,
    result: AuditResult,
    project: str,
    images: dict[str, str],
    status: str,
    host: str,
    release: Path,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "owner_label": OWNER_LABEL,
        "project": project,
        "repo": args.repo,
        "branch": args.branch,
        "commit": _git_commit(result.root),
        "tree_hash": _tree_hash(result.root),
        "compose": result.compose.relative_to(result.root).as_posix(),
        "services": sorted(result.data["services"]),
        "images": images,
        "host": host,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "release": str(release),
        "non_root": bool(args.non_root),
        "has_env": True,
        "external_env_supplied": bool(args.env_file),
    }


def _write_manifest(runner: Runner, release: Path, manifest: dict[str, Any]) -> None:
    content = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    if runner.transport == "ssh":
        runner.run(
            [
                "sh",
                "-c",
                'umask 077; cat > "$1"',
                "app-deploy",
                str(release / MANIFEST),
            ],
            stdin=content,
        )
    else:
        path = release / MANIFEST
        path.write_bytes(content)
        path.chmod(0o600)


def deploy(
    args: argparse.Namespace, result: AuditResult, runner: Runner
) -> dict[str, Any] | None:
    if result.errors:
        raise AppDeployError("static audit has errors; deployment blocked")
    env_file = (
        validate_env_file(Path(args.env_file), result.root) if args.env_file else None
    )
    if args.dry_run:
        return None
    project = args.project or _project_id(args.repo, result.root)
    if not PROJECT_RE.fullmatch(project):
        raise AppDeployError(
            "project must match lowercase [a-z0-9_-] and be at most 63 characters"
        )
    state_root = _state_root(args.transport, runner.environ)
    release = state_root / project
    if runner.transport == "local" and release.exists():
        raise AppDeployError(
            "project release already exists; use lifecycle commands or a new project"
        )
    if runner.transport == "ssh":
        available = runner.run(["test", "!", "-e", str(release)], check=False)
        if available.returncode:
            raise AppDeployError(
                "remote project release already exists; use lifecycle commands or a new project"
            )
    _ensure_owned(runner, project)
    compose_rel = result.compose.relative_to(result.root).as_posix()
    override = _override(result.data, project, args.non_root)
    _stage_release(runner, result.root, release, compose_rel, override, env_file)
    services = sorted(result.data["services"])
    command = _compose_command(release, compose_rel, project, True, "build")
    runner.run(command, timeout=args.timeout)
    images: dict[str, str] = {}
    if args.non_root:
        images = _image_users(runner, release, compose_rel, project, True, services)
    else:
        for service in services:
            image = runner.run(
                _compose_command(
                    release,
                    compose_rel,
                    project,
                    True,
                    "images",
                    "-q",
                    service,
                )
            ).stdout.strip()
            if image:
                images[service] = image
    host = "local" if runner.transport == "local" else runner.host
    manifest = _manifest_data(args, result, project, images, "building", host, release)
    _write_manifest(runner, release, manifest)
    up_started = False
    try:
        runner.run(
            _compose_command(release, compose_rel, project, True, "up", "-d"),
            timeout=args.timeout,
        )
        up_started = True
        containers = _containers(runner, release, compose_rel, project, True, services)
        _verify_containers(runner, containers, project, args.non_root, args.wait)
        manifest["status"] = "running"
        manifest["containers"] = containers
        _write_manifest(runner, release, manifest)
        return manifest
    except AppDeployError:
        manifest["status"] = "failed"
        if up_started:
            logs = runner.run(
                _compose_command(
                    release,
                    compose_rel,
                    project,
                    True,
                    "logs",
                    "--no-color",
                    "--tail",
                    "200",
                ),
                check=False,
            )
            # Logs can contain secrets. Record only an integrity digest and size.
            diagnostic = (logs.stdout + logs.stderr).encode()
            manifest["diagnostics"] = {
                "sha256": hashlib.sha256(diagnostic).hexdigest(),
                "bytes": len(diagnostic),
            }
            runner.run(
                _compose_command(
                    release,
                    compose_rel,
                    project,
                    True,
                    "down",
                    "--remove-orphans",
                ),
                check=False,
            )
        _write_manifest(runner, release, manifest)
        raise


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


def lifecycle(args: argparse.Namespace, runner: Runner) -> int:
    root = _state_root(args.transport, runner.environ)
    manifest = _read_manifest(runner, root, args.project)
    _ensure_owned(runner, args.project)
    release = root / args.project
    command = _compose_command(
        release, manifest["compose"], args.project, bool(manifest.get("has_env"))
    )
    if args.command == "status":
        result = runner.run([*command, "ps", "--format", "json"], check=False)
        sys.stdout.write(result.stdout)
        return result.returncode
    if args.command == "logs":
        result = runner.run(
            [*command, "logs", "--no-color", "--tail", str(args.tail)], check=False
        )
        sys.stdout.write(_redact(result.stdout, _redaction_values(runner, release)))
        return result.returncode
    tail = ["down", "--remove-orphans"]
    if args.purge_data:
        if args.confirm_purge != args.project:
            raise AppDeployError(
                "--purge-data requires --confirm-purge equal to the project"
            )
        tail.append("--volumes")
    runner.run([*command, *tail])
    manifest["status"] = "down"
    _write_manifest(runner, release, manifest)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "deploy"):
        item = sub.add_parser(name)
        source = item.add_mutually_exclusive_group(required=True)
        source.add_argument("--repo")
        source.add_argument("--source-dir", type=Path)
        item.add_argument("--branch", default="main")
        item.add_argument("--compose-file")
        item.add_argument("--non-root", action="store_true")
        item.add_argument("--require-build", action="store_true")
        item.add_argument("--format", choices=("human", "json"), default="human")
        if name == "deploy":
            item.add_argument("--env-file")
            item.add_argument("--dry-run", action="store_true")
            item.add_argument("--transport", choices=("local", "ssh"), default="local")
            item.add_argument("--project")
            item.add_argument("--timeout", type=int, default=1800)
            item.add_argument("--wait", type=int, default=180)
    for name in ("status", "logs", "down"):
        item = sub.add_parser(name)
        item.add_argument("--project", required=True)
        item.add_argument("--transport", choices=("local", "ssh"), default="local")
        if name == "logs":
            item.add_argument("--tail", type=int, default=200)
        if name == "down":
            item.add_argument("--purge-data", action="store_true")
            item.add_argument("--confirm-purge")
    return parser


def _run_source_command(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="spanel-app-audit-") as temporary:
        if args.source_dir:
            root = args.source_dir
        else:
            root = Path(temporary) / "checkout"
            clone_repo(args.repo, args.branch, root)
        result = audit_project(
            root,
            args.compose_file,
            non_root=args.non_root,
            require_build=args.require_build,
        )
        print(render_report(result, args.format))
        if args.command == "inspect":
            return 2 if result.errors else 0
        # Dry-run cannot contact SSH or require its configuration.
        runner = Runner("local" if args.dry_run else args.transport)
        manifest = deploy(args, result, runner)
        if manifest is not None:
            summary = {
                key: manifest[key]
                for key in ("project", "status", "services", "images")
            }
            print(json.dumps(summary, sort_keys=True))
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"inspect", "deploy"}:
            return _run_source_command(args)
        return lifecycle(args, Runner(args.transport))
    except AppDeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
