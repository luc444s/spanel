#!/usr/bin/env python3
"""Configure and optionally restart the SPanel Apps deployment.

The command is intentionally narrow: it manages only SPANEL_APPS_* settings,
creates one encryption key, and recreates app/worker in migration-safe order.
It never prints secret material and never runs ``docker compose down``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

APPS_KEYS = (
    "SPANEL_APPS_PUBLIC_URL",
    "SPANEL_APPS_ENCRYPTION_KEY_PATH",
    "SPANEL_APPS_ENCRYPTION_KEY_FILE",
    "SPANEL_APPS_STATE_ROOT",
    "SPANEL_APPS_KEY_VERSION",
)
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
TRAEFIK_HOST = re.compile(r"Host\(\s*[`\"']([^`\"']+)[`\"']\s*\)")


class ConfigureError(RuntimeError):
    """Safe operator-facing configuration failure."""


def parse_env(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = ENV_LINE.match(line)
        if not match:
            continue
        key = match.group(1)
        value = line[match.end():].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_public_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigureError("Apps public URL must be an absolute HTTP(S) origin")
    if parsed.username or parsed.password:
        raise ConfigureError("Apps public URL must not contain credentials")
    if parsed.path or parsed.query or parsed.fragment:
        raise ConfigureError("Apps public URL must not contain a path, query or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigureError("Apps public URL has an invalid port") from exc
    if parsed.scheme != "https" and not _loopback(parsed.hostname.lower()):
        raise ConfigureError("Apps public URL must use HTTPS unless it is loopback")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None else ''}"


def detect_public_url(
    explicit: str | None,
    env_values: dict[str, str],
    compose_text: str,
) -> str:
    if explicit:
        return validate_public_url(explicit)
    existing = env_values.get("SPANEL_APPS_PUBLIC_URL", "").strip()
    if existing:
        return validate_public_url(existing)
    hosts = {host.strip().lower() for host in TRAEFIK_HOST.findall(compose_text) if host.strip()}
    if len(hosts) == 1:
        return validate_public_url(f"https://{hosts.pop()}")
    if not hosts:
        raise ConfigureError("Could not detect the public URL; pass --public-url")
    raise ConfigureError("Multiple Traefik hosts found; pass --public-url explicitly")


def _decode_key(value: str) -> bytes:
    try:
        padded = value.strip() + "=" * (-len(value.strip()) % 4)
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConfigureError("Existing Apps encryption key is not valid urlsafe base64") from exc


def ensure_key(path: Path, *, dry_run: bool = False) -> bool:
    """Create or validate a key. Return True only when a key was created."""
    path = Path(os.path.abspath(path.expanduser()))
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise ConfigureError("Apps encryption key must be a regular non-symlink file")
        try:
            value = path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise ConfigureError("Cannot read the Apps encryption key") from exc
        if len(_decode_key(value)) != 32:
            raise ConfigureError("Apps encryption key must decode to exactly 32 bytes")
        if not dry_run:
            path.chmod(0o600)
        return False
    if dry_run:
        return True
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return ensure_key(path, dry_run=False)
    except OSError as exc:
        raise ConfigureError("Cannot create the Apps encryption key") from exc
    path.chmod(0o600)
    return True


def render_env(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        if key not in APPS_KEYS:
            raise ConfigureError(f"Refusing to update non-Apps variable: {key}")
        if not value or any(character in value for character in "\r\n\0"):
            raise ConfigureError(f"Invalid value for {key}")
        if any(character.isspace() for character in value):
            raise ConfigureError(f"Whitespace is not supported in {key}")

    output: list[str] = []
    emitted: set[str] = set()
    for line in text.splitlines():
        match = ENV_LINE.match(line)
        key = match.group(1) if match else None
        if key in values:
            if key not in emitted:
                output.append(f"{key}={values[key]}")
                emitted.add(key)
            continue
        output.append(line)
    missing = [key for key in APPS_KEYS if key in values and key not in emitted]
    if missing:
        if output and output[-1] != "":
            output.append("")
        output.append("# APPS — managed by scripts/configure_apps.py")
        output.extend(f"{key}={values[key]}" for key in missing)
    return "\n".join(output).rstrip() + "\n"


def write_env(path: Path, content: str, *, dry_run: bool = False) -> Path | None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if original == content or dry_run:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.apps-backup-{stamp}")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.apps-backup-{stamp}-{counter}")
            counter += 1
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


CommandRunner = Callable[[list[str]], None]


def run_command(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise ConfigureError(f"Required executable not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise ConfigureError(f"Command failed with exit code {exc.returncode}") from exc


def wait_for_app(url: str = "http://127.0.0.1:3000/api/v1/system/health", timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(2)
    raise ConfigureError("SPanel app did not become healthy; apps-worker was not restarted")


def compose_commands(
    env_file: Path,
    compose_file: Path,
    *,
    restart: bool,
    build: bool,
) -> list[list[str]]:
    base = ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file)]
    # Plain `docker compose config` expands and prints every environment value,
    # including secrets. Validation must never emit the normalized document.
    commands = [base + ["config", "--quiet"]]
    if restart:
        options = ["up", "-d"] + (["--build"] if build else [])
        options += ["--force-recreate", "--no-deps"]
        commands.append(base + options + ["app"])
        commands.append(base + options + ["apps-worker"])
    return commands


def configure(
    *,
    env_file: Path,
    compose_file: Path,
    key_path: Path,
    public_url: str | None,
    restart: bool,
    build: bool,
    dry_run: bool,
    runner: CommandRunner = run_command,
    waiter: Callable[[], None] = wait_for_app,
) -> tuple[str, bool, Path | None]:
    try:
        env_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        compose_text = compose_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigureError("Cannot read env or Compose configuration") from exc
    detected_url = detect_public_url(public_url, parse_env(env_text), compose_text)
    absolute_key = Path(os.path.abspath(key_path.expanduser()))
    created = ensure_key(absolute_key, dry_run=dry_run)
    values = {
        "SPANEL_APPS_PUBLIC_URL": detected_url,
        "SPANEL_APPS_ENCRYPTION_KEY_PATH": str(absolute_key),
        "SPANEL_APPS_ENCRYPTION_KEY_FILE": "/run/secrets/apps_key",
        "SPANEL_APPS_STATE_ROOT": "/var/lib/spanel/apps",
        "SPANEL_APPS_KEY_VERSION": "1",
    }
    rendered = render_env(env_text, values)
    backup = write_env(env_file, rendered, dry_run=dry_run)
    if not dry_run:
        commands = compose_commands(env_file, compose_file, restart=restart, build=build)
        runner(commands[0])
        if restart:
            runner(commands[1])
            waiter()
            runner(commands[2])
    return detected_url, created, backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.docker"))
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument("--key-path", type=Path, default=Path("secrets/apps_key"))
    parser.add_argument("--public-url", help="Explicit public HTTPS origin")
    parser.add_argument("--restart", action="store_true", help="Build and recreate app then apps-worker")
    parser.add_argument("--no-build", action="store_true", help="Reuse the current image with --restart")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show the plan without writes/commands")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        public_url, created, backup = configure(
            env_file=args.env_file.resolve(), compose_file=args.compose_file.resolve(),
            key_path=args.key_path, public_url=args.public_url, restart=args.restart,
            build=not args.no_build, dry_run=args.dry_run,
        )
    except ConfigureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    action = "would create" if args.dry_run and created else "created" if created else "reused"
    print(f"Apps public URL: {public_url}")
    key_display = Path(os.path.abspath(args.key_path.expanduser()))
    print(f"Encryption key: {action} at {key_display}")
    if backup is not None:
        print(f"Environment backup: {backup}")
    if args.dry_run:
        print("Dry-run: no files or services changed")
    elif args.restart:
        print("SPanel app and apps-worker recreated successfully")
    else:
        print("Configuration updated; run again with --restart to deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
