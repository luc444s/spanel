"""Staging, ownership checks, deployment and manifests."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import tarfile
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .docker_audit import _is_definitely_non_root_user
from .source import _inside
from .transport import Runner
from .types import (
    MANIFEST,
    OWNER_LABEL,
    PROJECT_RE,
    SAFE_REMOTE_RE,
    AppDeployError,
    AuditResult,
    DeployOptions,
)


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
                "warning": (
                    "Static heuristics do not prove safety; Docker builds execute "
                    "untrusted code."
                ),
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
    if data.get("volumes"):
        output["volumes"] = {
            name: {"labels": {OWNER_LABEL: project}} for name in data["volumes"]
        }
    network_names = {"default", *(data.get("networks") or {})}
    output["networks"] = {
        name: {"labels": {OWNER_LABEL: project}} for name in network_names
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
    value = (
        environ.get("SPANEL_APPS_STATE_ROOT", "").strip()
        or environ.get("SPANEL_APP_STATE_ROOT", "").strip()
    )
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
        if release.parent.name == "releases":
            private = release.parent.parent / "private" / release.name
            command = (
                f"umask 077; mkdir -p {shlex.quote(str(release))} "
                f"{shlex.quote(str(private))}; "
                f"tar -xzf - -C {shlex.quote(str(release))}; "
                f"mv {shlex.quote(str(release / 'app.env'))} "
                f"{shlex.quote(str(private / 'app.env'))}; "
                f"chmod 600 {shlex.quote(str(release / 'override.yaml'))} "
                f"{shlex.quote(str(private / 'app.env'))}"
            )
        else:
            command = (
                f"umask 077; mkdir -p {shlex.quote(str(release))}; "
                f"tar -xzf - -C {shlex.quote(str(release))}; "
                f"chmod 600 {shlex.quote(str(release / 'override.yaml'))} "
                f"{shlex.quote(str(release / 'app.env'))}"
            )
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
        if release.parent.name == "releases":
            private = release.parent.parent / "private" / release.name
            private.mkdir(parents=True, mode=0o700)
            private.chmod(0o700)
            os.replace(release / "app.env", private / "app.env")
            (private / "app.env").chmod(0o600)


def _env_path(release: Path) -> Path:
    if release.parent.name == "releases":
        return release.parent.parent / "private" / release.name / "app.env"
    return release / "app.env"


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
        args.extend(["--env-file", str(_env_path(release))])
    args.extend(["-f", str(release / "override.yaml"), *tail])
    return args


def _ensure_owned(runner: Runner, project: str) -> None:
    for kind, label_source, listing in (
        (
            "container",
            ".Config.Labels",
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
            ".Labels",
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
            ".Labels",
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
                    f'{{{{index {label_source} "{OWNER_LABEL}"}}}}',
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


def _service_environment_names(data: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for service, config in data["services"].items():
        environment = config.get("environment", {}) if isinstance(config, dict) else {}
        if isinstance(environment, dict):
            names = [str(name) for name in environment]
        elif isinstance(environment, list):
            names = [str(item).split("=", 1)[0] for item in environment]
        else:
            names = []
        result[str(service)] = sorted({name for name in names if name})
    return result


def _validate_compose_environment(
    runner: Runner,
    release: Path,
    compose_rel: str,
    project: str,
) -> None:
    configured = runner.run(
        _compose_command(
            release, compose_rel, project, True, "config", "--format", "json"
        ),
        check=False,
    )
    compose_stderr = configured.stderr.replace(r'\"', '"')
    missing = sorted(set(re.findall(
        r'The "([A-Za-z_][A-Za-z0-9_]*)" variable is not set',
        compose_stderr,
    )))
    if missing:
        raise AppDeployError(
            f"missing required Compose environment variables: {', '.join(missing)}"
        )
    if configured.returncode:
        raise AppDeployError("Compose configuration is invalid")


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
                    "{{.State.Status}} {{if .State.Health}}"
                    "{{.State.Health.Status}}{{else}}none{{end}}",
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
    options: DeployOptions,
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
        "repo": options.repo,
        "branch": options.branch,
        "commit": _git_commit(result.root),
        "tree_hash": _tree_hash(result.root),
        "compose": result.compose.relative_to(result.root).as_posix(),
        "services": sorted(result.data["services"]),
        "service_environment": _service_environment_names(result.data),
        "images": images,
        "host": host,
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "release": str(release),
        "non_root": options.non_root,
        "has_env": True,
        "external_env_supplied": bool(options.env_file),
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


def _publish_release_state(runner: Runner, release: Path, manifest: dict[str, Any]) -> None:
    if release.parent.name != "releases":
        return
    project_root = release.parent.parent
    content = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    targets = [project_root / "manifests" / f"{release.name}.json", project_root / "current.json"]
    if runner.transport == "ssh":
        for target in targets:
            runner.run(
                ["sh", "-c", 'umask 077; mkdir -p "$(dirname "$1")"; cat > "$1"',
                 "app-deploy", str(target)],
                stdin=content,
            )
    else:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o600)


def deploy(
    options: DeployOptions, result: AuditResult, runner: Runner
) -> dict[str, Any] | None:
    if result.errors:
        raise AppDeployError("static audit has errors; deployment blocked")
    env_file = (
        validate_env_file(options.env_file, result.root) if options.env_file else None
    )
    if options.dry_run:
        return None
    project = options.project or _project_id(options.repo, result.root)
    if not PROJECT_RE.fullmatch(project):
        raise AppDeployError(
            "project must match lowercase [a-z0-9_-] and be at most 63 characters"
        )
    state_root = _state_root(options.transport, runner.environ)
    release = (
        state_root / project / "releases" / options.release_id
        if options.release_id
        else state_root / project
    )
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
    override = _override(result.data, project, options.non_root)
    _stage_release(runner, result.root, release, compose_rel, override, env_file)
    services = sorted(result.data["services"])
    _validate_compose_environment(runner, release, compose_rel, project)
    command = _compose_command(release, compose_rel, project, True, "build")
    built = runner.run(command, timeout=options.timeout, check=False)
    if built.returncode:
        raise AppDeployError(f"Compose image build failed (exit {built.returncode})")
    images: dict[str, str] = {}
    if options.non_root:
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
    manifest = _manifest_data(options, result, project, images, "building", host, release)
    _write_manifest(runner, release, manifest)
    up_started = False
    try:
        started = runner.run(
            _compose_command(release, compose_rel, project, True, "up", "-d"),
            timeout=options.timeout,
            check=False,
        )
        up_started = True
        if started.returncode:
            raise AppDeployError(
                f"Compose services failed to start (exit {started.returncode})"
            )
        containers = _containers(runner, release, compose_rel, project, True, services)
        _verify_containers(runner, containers, project, options.non_root, options.wait)
        manifest["status"] = "running"
        manifest["containers"] = containers
        _write_manifest(runner, release, manifest)
        if options.publish_state:
            _publish_release_state(runner, release, manifest)
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
