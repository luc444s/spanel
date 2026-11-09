#!/usr/bin/env python3
"""Audit and deploy an untrusted, generic Docker Compose application."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "vendor" / "systutor-core" / "plugins"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from apps.backend.runtime import (
    MANIFEST,
    OWNER_LABEL,
    AppDeployError,
    AuditResult,
    Completed,
    DeployOptions,
    LifecycleOptions,
    Runner,
    _ensure_owned,
    _image_users,
    _is_root_user,
    _manifest_data,
    _override,
    _redact,
    _redaction_values,
    _verify_containers,
    audit_project,
    clone_repo,
    deploy,
    discover_compose,
    lifecycle,
    render_report,
    validate_env_file,
)

__all__ = [
    "MANIFEST",
    "OWNER_LABEL",
    "AppDeployError",
    "AuditResult",
    "Completed",
    "DeployOptions",
    "Runner",
    "_ensure_owned",
    "_image_users",
    "_is_root_user",
    "_manifest_data",
    "_override",
    "_redact",
    "_redaction_values",
    "_verify_containers",
    "audit_project",
    "deploy",
    "discover_compose",
    "validate_env_file",
]


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
            item.add_argument("--env-file", type=Path)
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


def _deploy_options(args: argparse.Namespace) -> DeployOptions:
    return DeployOptions(
        repo=args.repo,
        branch=args.branch,
        env_file=args.env_file,
        dry_run=args.dry_run,
        project=args.project,
        transport=args.transport,
        non_root=args.non_root,
        timeout=args.timeout,
        wait=args.wait,
    )


def _lifecycle_options(args: argparse.Namespace) -> LifecycleOptions:
    return LifecycleOptions(
        command=args.command,
        project=args.project,
        transport=args.transport,
        tail=getattr(args, "tail", 200),
        purge_data=getattr(args, "purge_data", False),
        confirm_purge=getattr(args, "confirm_purge", None),
    )


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
        options = _deploy_options(args)
        runner = Runner("local" if options.dry_run else options.transport)
        manifest = deploy(options, result, runner)
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
        options = _lifecycle_options(args)
        result = lifecycle(options, Runner(options.transport))
        sys.stdout.write(result.output)
        return result.returncode
    except AppDeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
