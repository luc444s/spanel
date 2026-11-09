#!/usr/bin/env python3
"""Run a destructive, isolated Apps E2E against Software para Editores."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shlex
import stat
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "vendor" / "systutor-core" / "plugins"
if not PLUGIN_ROOT.exists():
    PLUGIN_ROOT = ROOT / "plugins"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from apps.backend.runtime import (
    AppDeployError,
    Completed,
    DeployOptions,
    LifecycleOptions,
    Runner,
    _verify_containers,
    audit_project,
    clone_repo,
    deploy,
    lifecycle,
)
from apps.backend.runtime.deployment import _compose_command

DEFAULT_REPO = "https://github.com/luc444s/software-de-gestion-para-editores.git"
DEFAULT_BRANCH = "feature/workflow-diseno"
DEFAULT_COMPOSE = "docker-compose.dokploy.yml"
EXPECTED_SERVICES = {"backend", "database", "frontend"}


@dataclass(frozen=True)
class CommandRecord:
    argv: tuple[str, ...]
    returncode: int
    log_path: Path


def redact_text(text: str, sensitive_values: set[str]) -> str:
    for value in sorted(sensitive_values, key=len, reverse=True):
        if len(value) >= 8:
            text = text.replace(value, "[REDACTED]")
    return text


class DiagnosticRunner(Runner):
    """Runner that writes redacted stdout/stderr for every Docker phase."""

    def __init__(self, artifacts: Path, environ: dict[str, str]):
        super().__init__("local", environ)
        self.artifacts = artifacts
        self.sensitive_values: set[str] = set()
        self.records: list[CommandRecord] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        timeout: int = 600,
        check: bool = True,
    ) -> Completed:
        try:
            completed = super().run(
                argv, cwd=cwd, stdin=stdin, timeout=timeout, check=False
            )
        except AppDeployError as exc:
            self._record(argv, Completed(125, "", str(exc)))
            raise
        self._record(argv, completed)
        if check and completed.returncode:
            raise AppDeployError(
                f"command failed ({completed.returncode}): {argv[0]}"
            )
        return completed

    def _record(self, argv: Sequence[str], completed: Completed) -> None:
        number = len(self.records) + 1
        name = Path(argv[0]).name.replace(".", "-") if argv else "command"
        path = self.artifacts / f"{number:03d}-{name}.log"
        content = (
            f"$ {shlex.join(list(argv))}\n"
            f"exit={completed.returncode}\n\n"
            f"{completed.stdout}{completed.stderr}"
        )
        path.write_text(
            redact_text(content, self.sensitive_values), encoding="utf-8"
        )
        path.chmod(0o600)
        self.records.append(CommandRecord(tuple(argv), completed.returncode, path))

    def last_failure(self) -> CommandRecord | None:
        return next(
            (record for record in reversed(self.records) if record.returncode), None
        )


def generated_environment(identity: str | None = None) -> tuple[dict[str, str], set[str]]:
    suffix = identity or secrets.token_hex(8)
    database_identity = f"app_e2e_{suffix}"
    database_password = secrets.token_hex(32)
    api_key = secrets.token_hex(32)
    jwt_secret = secrets.token_hex(32)
    worker_secret = secrets.token_hex(32)
    secret_key = secrets.token_hex(32)
    database_url = (
        f"postgresql://{database_identity}:{database_password}"
        f"@database:5432/{database_identity}"
    )
    values = {
        "APP_DB_NAME": database_identity,
        "APP_DB_USER": database_identity,
        "APP_DB_PASSWORD": database_password,
        "DATABASE_URL": database_url,
        "API_KEY": api_key,
        "JWT_SECRET_KEY": jwt_secret,
        "SECRET_KEY": secret_key,
        "CORS_ORIGINS": "http://localhost:8080",
        "VITE_API_BASE_URL": "/api",
        "VITE_CHAT_ENABLED": "false",
        "PAYPAL_ENABLED": "false",
        "CLOUDFLARE_R2_ENABLED": "false",
        "CLOUDFLARE_R2_ACCESS_KEY_ID": "e2e-disabled",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY": "e2e-disabled",
        "CLOUDFLARE_R2_ENDPOINT_URL": "https://r2.invalid",
        "CLOUDFLARE_R2_BUCKET": "e2e-disabled",
        "WORKER_BASE_URL": "https://worker.invalid",
        "CLOUDFLARE_WORKER_SHARED_SECRET": worker_secret,
        "RUN_DB_MIGRATIONS": "true",
        "WAIT_FOR_DB": "true",
    }
    sensitive = {
        database_password,
        database_url,
        api_key,
        jwt_secret,
        worker_secret,
        secret_key,
    }
    return values, sensitive


def write_environment(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def image_name(project: str, service: str) -> str:
    key = hashlib.sha256(f"{project}\0{service}".encode()).hexdigest()[:20]
    return f"spanel-app-{key}:latest"


def diagnostic_tail(record: CommandRecord, lines: int = 50) -> str:
    content = record.log_path.read_text(encoding="utf-8").splitlines()
    return "\n".join(content[-lines:])


def cleanup(
    runner: DiagnosticRunner,
    release: Path,
    compose_rel: str,
    project: str,
    services: set[str],
) -> list[str]:
    errors: list[str] = []
    if release.joinpath("override.yaml").exists():
        result = runner.run(
            [
                *_compose_command(release, compose_rel, project, True),
                "down",
                "--remove-orphans",
                "--volumes",
            ],
            check=False,
        )
        if result.returncode:
            errors.append(f"Compose cleanup exited {result.returncode}")
    for service in services:
        runner.run(
            ["docker", "image", "rm", "-f", image_name(project, service)],
            check=False,
        )
    resources = (
        ("containers", ["docker", "ps", "-aq", "--filter"]),
        ("networks", ["docker", "network", "ls", "-q", "--filter"]),
        ("volumes", ["docker", "volume", "ls", "-q", "--filter"]),
    )
    label = f"label=com.docker.compose.project={project}"
    for name, command in resources:
        result = runner.run([*command, label], check=False)
        if result.stdout.split():
            errors.append(f"leaked Docker {name}: {len(result.stdout.split())}")
    return errors


def _artifacts_path(explicit: Path | None, run_id: str) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    configured = os.environ.get("SPANEL_APPS_STATE_ROOT", "").strip()
    base = Path(configured) if configured else ROOT / "logs" / "apps-e2e"
    return (base / "e2e-artifacts" / run_id).resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--compose-file", default=DEFAULT_COMPOSE)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--wait", type=int, default=240)
    return parser


def run_e2e(args: argparse.Namespace) -> int:
    run_id = f"{int(time.time())}-{secrets.token_hex(4)}"
    project = f"spanel-e2e-editors-{secrets.token_hex(5)}"
    artifacts = _artifacts_path(args.artifacts_dir, run_id)
    artifacts.mkdir(parents=True, mode=0o700)
    artifacts.chmod(0o700)
    started = time.monotonic()
    primary_error: Exception | None = None
    failure_record: CommandRecord | None = None
    cleanup_errors: list[str] = []

    print(f"E2E project: {project}")
    print(f"Redacted artifacts: {artifacts}")
    with tempfile.TemporaryDirectory(prefix="spanel-editors-e2e-") as temporary:
        workspace = Path(temporary)
        checkout = workspace / "checkout"
        state_root = workspace / "state"
        env_file = workspace / "application.env"
        release = state_root / project / "releases" / run_id
        runner = DiagnosticRunner(
            artifacts,
            {**os.environ, "SPANEL_APPS_STATE_ROOT": str(state_root)},
        )
        compose_rel = args.compose_file
        services = set(EXPECTED_SERVICES)
        manifest: dict | None = None
        try:
            print("[1/7] Checking Docker daemon")
            runner.run(["docker", "info"], timeout=30)
            print("[2/7] Cloning source")
            clone_repo(args.repo, args.branch, checkout, runner)
            print("[3/7] Running Apps static inspection")
            audit = audit_project(
                checkout, args.compose_file, non_root=True, require_build=True
            )
            if audit.errors:
                codes = ", ".join(sorted({finding.code for finding in audit.errors}))
                raise AppDeployError(f"static inspection blocked deployment: {codes}")
            compose_rel = audit.compose.relative_to(audit.root).as_posix()
            services = set(audit.data["services"])
            missing = EXPECTED_SERVICES.difference(services)
            if missing:
                raise AppDeployError(
                    f"expected services are missing: {', '.join(sorted(missing))}"
                )
            values, sensitive = generated_environment()
            runner.sensitive_values.update(sensitive)
            write_environment(env_file, values)
            print("[4/7] Deploying with generated managed credentials")
            manifest = deploy(
                DeployOptions(
                    repo=args.repo,
                    branch=args.branch,
                    env_file=env_file,
                    project=project,
                    non_root=True,
                    timeout=args.timeout,
                    wait=args.wait,
                    release_id=run_id,
                    publish_state=False,
                ),
                audit,
                runner,
            )
            if not manifest or manifest.get("status") != "running":
                raise AppDeployError("deployment did not produce a running manifest")
            containers = manifest.get("containers", {})
            lifecycle_base = {
                "project": project,
                "release": release,
            }
            print("[5/7] Testing refresh, logs and restart lifecycle")
            refreshed = lifecycle(
                LifecycleOptions(command="status", **lifecycle_base), runner
            )
            if refreshed.returncode or not refreshed.output.strip():
                raise AppDeployError("refresh did not return Compose service status")
            logs = lifecycle(
                LifecycleOptions(command="logs", tail=50, **lifecycle_base), runner
            )
            if logs.returncode:
                raise AppDeployError("logs lifecycle command failed")
            lifecycle(
                LifecycleOptions(command="restart", **lifecycle_base), runner
            )
            _verify_containers(
                runner, containers, project, non_root=True, wait=args.wait
            )
            print("[6/7] Probing frontend and internal backend connectivity")
            runner.run(
                [
                    "docker", "exec", containers["frontend"], "wget", "-qO-",
                    "http://127.0.0.1:8080/",
                ]
            )
            runner.run(
                [
                    "docker", "exec", containers["frontend"], "wget", "-qO-",
                    "http://backend:8000/health",
                ]
            )
        except Exception as exc:  # noqa: BLE001 -- cleanup must run for every failure
            primary_error = exc
            failure_record = runner.last_failure()
        finally:
            print("[7/7] Removing test containers, networks, volumes and images")
            cleanup_errors = cleanup(
                runner, release, compose_rel, project, services
            )
            env_file.unlink(missing_ok=True)

        elapsed = round(time.monotonic() - started, 2)
        report = {
            "project": project,
            "repo": args.repo,
            "branch": args.branch,
            "status": "failed" if primary_error or cleanup_errors else "passed",
            "services": sorted(services),
            "elapsed_seconds": elapsed,
            "error": str(primary_error) if primary_error else None,
            "cleanup_errors": cleanup_errors,
        }
        report_path = artifacts / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        report_path.chmod(0o600)

    if primary_error is not None:
        print(f"E2E FAILED: {primary_error}", file=sys.stderr)
        if failure_record is not None:
            print(
                f"Failing command log: {failure_record.log_path}\n"
                f"--- redacted tail ---\n{diagnostic_tail(failure_record)}",
                file=sys.stderr,
            )
    if cleanup_errors:
        print(f"E2E CLEANUP FAILED: {'; '.join(cleanup_errors)}", file=sys.stderr)
    if primary_error is not None or cleanup_errors:
        return 1
    print(f"E2E PASSED in {report['elapsed_seconds']}s")
    print(f"Report: {report_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_e2e(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
