#!/usr/bin/env python3
"""Migrate legacy Postfix SNI entries to per-hostname Certbot certificates.

The case this script targets: a Docker Mailserver keeps serving a legacy
multi-domain certificate from an unmanaged Postfix map (for example
``sni-legalattorney``), while ``spanel_cert_agent`` only manages ``sni-spanel``.
The legacy hostnames work until the certificate expires, then clients fail.

The script is read-only by default. It prints what would change. With
``--apply`` it can:

1. add the detected hostnames to the agent allowlist,
2. verify HTTP-01 with the configured webroot,
3. issue one Certbot lineage per hostname,
4. install the pair through ``spanel_cert_agent install``,
5. verify the served certificate,
6. optionally remove the migrated lines from the legacy SNI map.

Usage:

    python3 scripts/migrate_legacy_sni.py                 # plan only
    python3 scripts/migrate_legacy_sni.py --apply         # migrate
    python3 scripts/migrate_legacy_sni.py --apply --clean-legacy
    python3 scripts/migrate_legacy_sni.py --apply \
        --hostname mail.algisac.com --hostname mail.algima.pe

Exit codes: 0 ok/plan, 1 migration failure, 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import tomllib

DEFAULT_CONFIG = Path("/etc/spanel/cert-agent.toml")
DEFAULT_CONTAINER = "mailserver"
DEFAULT_CONFIG_ROOT = Path("/opt/stacks/mail/config")
DEFAULT_INSTALL_ROOT = Path("/etc/ssl/spanel/mail")
DEFAULT_LINEAGE_ROOT = Path("/etc/letsencrypt/live")
CONTAINER_CONFIG_ROOT = "/tmp/docker-mailserver"
MANAGED_MAP_NAME = "sni-spanel"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_CONFIG = 2


class MigrationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentSettings:
    path: Path
    text: str
    allowlist: tuple[str, ...]
    install_root: Path
    lineage_root: Path
    certbot: str
    challenge: str
    webroot: Path | None
    postfix_map: Path | None
    postmap_command: tuple[str, ...]
    reload_command: tuple[str, ...]


def normalize_hostname(value: str) -> str:
    return value.strip().lower().rstrip(".")


def load_settings(path: Path) -> AgentSettings:
    if not path.is_file():
        raise MigrationError(f"missing agent config: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MigrationError(f"invalid TOML in {path}: {exc}") from exc

    values = raw.get("allowlist")
    if not isinstance(values, list):
        raise MigrationError("the agent config has no allowlist list")
    allowlist = tuple(sorted({normalize_hostname(str(item)) for item in values if str(item).strip()}))

    certbot_raw = raw.get("certbot") if isinstance(raw.get("certbot"), dict) else {}
    postfix_raw = raw.get("postfix") if isinstance(raw.get("postfix"), dict) else {}

    def _path(mapping: dict, key: str, default: Path) -> Path:
        value = mapping.get(key)
        return Path(str(value)) if value else default

    sni_map_raw = postfix_raw.get("sni_map_path")
    sni_map = Path(str(sni_map_raw)) if sni_map_raw else None

    certbot = str(certbot_raw.get("executable") or "/usr/bin/certbot")
    challenge = str(certbot_raw.get("challenge") or "webroot")
    webroot_raw = certbot_raw.get("webroot_path")
    webroot = Path(str(webroot_raw)) if webroot_raw else None

    postmap_raw = postfix_raw.get("postmap_command")
    postmap = tuple(str(part) for part in postmap_raw) if isinstance(postmap_raw, list) else ()
    reload_raw = postfix_raw.get("reload_command")
    reload_command = tuple(str(part) for part in reload_raw) if isinstance(reload_raw, list) else ()

    return AgentSettings(
        path=path,
        text=text,
        allowlist=allowlist,
        install_root=_path(raw, "install_root", DEFAULT_INSTALL_ROOT),
        lineage_root=_path(certbot_raw, "lineage_root", DEFAULT_LINEAGE_ROOT),
        certbot=certbot,
        challenge=challenge,
        webroot=webroot,
        postfix_map=sni_map,
        postmap_command=postmap,
        reload_command=reload_command,
    )


# ---------------------------------------------------------------------------
# SNI maps
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SniEntry:
    hostname: str
    key_path: str
    cert_path: str
    raw: str


def parse_sni_map(text: str) -> list[SniEntry]:
    entries: list[SniEntry] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        hostname = normalize_hostname(parts[0])
        if not hostname:
            continue
        entries.append(
            SniEntry(
                hostname=hostname,
                key_path=parts[1],
                cert_path=parts[2],
                raw=raw,
            )
        )
    return entries


def discover_legacy_map(config_root: Path) -> Path | None:
    postfix = config_root / "postfix"
    preferred = postfix / "sni-legalattorney"
    if preferred.is_file():
        return preferred
    candidates = [
        path
        for path in sorted(postfix.glob("sni-*"))
        if path.is_file() and path.name != MANAGED_MAP_NAME
    ]
    return candidates[0] if candidates else None


def remove_entries(text: str, hostnames: set[str]) -> str:
    kept: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(raw)
            continue
        parts = stripped.split()
        if parts and normalize_hostname(parts[0]) in hostnames:
            continue
        kept.append(raw)
    return "\n".join(kept).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HostPlan:
    hostname: str
    legacy: SniEntry
    in_allowlist: bool
    lineage: Path
    has_lineage: bool
    installed: Path
    installed_matches: bool
    actions: list[str] = field(default_factory=list)


def _installed_pair(install_root: Path, hostname: str) -> tuple[Path, Path]:
    directory = install_root / hostname
    return directory / "fullchain.pem", directory / "privkey.pem"


def _files_equal(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file():
        return False
    try:
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def build_plan(
    settings: AgentSettings,
    entries: list[SniEntry],
    *,
    install_root: Path | None = None,
    lineage_root: Path | None = None,
) -> list[HostPlan]:
    installs = install_root or settings.install_root
    lineages = lineage_root or settings.lineage_root
    plan: list[HostPlan] = []
    for entry in entries:
        lineage = lineages / entry.hostname
        fullchain = lineage / "fullchain.pem"
        installed_cert, installed_key = _installed_pair(installs, entry.hostname)
        has_lineage = fullchain.is_file()
        matches = (
            has_lineage
            and installed_cert.is_file()
            and installed_key.is_file()
            and _files_equal(fullchain, installed_cert)
        )
        actions: list[str] = []
        if entry.hostname not in settings.allowlist:
            actions.append("allowlist")
        if not has_lineage:
            actions.append("issue")
        if not matches:
            actions.append("install")
        plan.append(
            HostPlan(
                hostname=entry.hostname,
                legacy=entry,
                in_allowlist=entry.hostname in settings.allowlist,
                lineage=lineage,
                has_lineage=has_lineage,
                installed=installed_cert,
                installed_matches=matches,
                actions=actions,
            )
        )
    return plan


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def render_allowlist(hostnames: list[str]) -> str:
    body = "\n".join(f'  "{hostname}",' for hostname in hostnames)
    return f"allowlist = [\n{body}\n]"


def upsert_allowlist(text: str, hostnames: list[str]) -> str:
    pattern = re.compile(r"(?ms)^allowlist\s*=\s*\[[^\]]*\]")
    replacement = render_allowlist(sorted(hostnames))
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + "\n\n" + replacement + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_with_backup(path: Path, content: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    _atomic_write(path, content)
    return backup


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def challenge_args(settings: AgentSettings) -> list[str]:
    if settings.challenge == "auto":
        return []
    if settings.challenge == "webroot":
        if settings.webroot is None:
            raise MigrationError("challenge=webroot requires certbot.webroot_path")
        return ["--webroot", "-w", str(settings.webroot)]
    raise MigrationError(
        f"challenge={settings.challenge!r} is not supported by this script; "
        "use the panel or a DNS-01 capable agent"
    )


def _agent_command(settings: AgentSettings, *args: str) -> tuple[list[str], dict[str, str]]:
    scripts_dir = Path(__file__).resolve().parent
    env = dict(os.environ)
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(scripts_dir) + (os.pathsep + current if current else "")
    command = [
        sys.executable,
        "-m",
        "spanel_cert_agent",
        *args,
        "--config",
        str(settings.path),
    ]
    return command, env


def issue_certificate(settings: AgentSettings, hostname: str) -> None:
    command = [
        settings.certbot,
        "certonly",
        "--cert-name",
        hostname,
        "-d",
        hostname,
        *challenge_args(settings),
        "--non-interactive",
        "--agree-tos",
    ]
    print("  + " + " ".join(command))
    result = run(command)
    if result.returncode != 0:
        raise MigrationError(
            f"certbot failed for {hostname} (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()[-1200:]}"
        )


def install_certificate(settings: AgentSettings, hostname: str) -> None:
    command, env = _agent_command(settings, "install", "--hostname", hostname)
    print("  + " + " ".join(command))
    result = run(command, env=env)
    if result.returncode != 0:
        raise MigrationError(
            f"agent install failed for {hostname} (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()[-1200:]}"
        )


def verify_certificate(settings: AgentSettings, hostname: str) -> None:
    command, env = _agent_command(settings, "verify", "--hostname", hostname)
    print("  + " + " ".join(command))
    result = run(command, env=env)
    if result.returncode != 0:
        raise MigrationError(
            f"TLS verification failed for {hostname} (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()[-1200:]}"
        )


def check_http(hostname: str, webroot: Path, timeout: float = 10.0) -> None:
    directory = webroot / ".well-known" / "acme-challenge"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"spanel-probe-{os.urandom(6).hex()}"
    token = os.urandom(16).hex()
    probe = directory / name
    probe.write_text(token, encoding="utf-8")
    url = f"http://{hostname}/.well-known/acme-challenge/{name}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace").strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MigrationError(f"HTTP-01 check failed for {hostname} ({url}): {exc}") from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if body != token:
        raise MigrationError(
            f"HTTP-01 check failed for {hostname}: {url} did not serve the probe "
            "(check the webroot route or ACME redirect)"
        )
    print(f"  + HTTP-01 ok: {url}")


def postmap_legacy(settings: AgentSettings, config_root: Path, legacy_path: Path) -> None:
    try:
        relative = legacy_path.relative_to(config_root)
    except ValueError as exc:
        raise MigrationError(
            f"legacy map {legacy_path} is outside {config_root}; "
            "run postmap manually"
        ) from exc
    internal = f"{CONTAINER_CONFIG_ROOT}/{relative.as_posix()}"
    if settings.postmap_command:
        command = list(settings.postmap_command)
        command[-1] = internal
    else:
        command = ["docker", "exec", DEFAULT_CONTAINER, "postmap", "-F", internal]
    print("  + " + " ".join(command))
    result = run(command)
    if result.returncode != 0:
        raise MigrationError(
            f"postmap failed (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()[-800:]}"
        )
    if settings.reload_command:
        reload_command = list(settings.reload_command)
    else:
        reload_command = ["docker", "exec", DEFAULT_CONTAINER, "postfix", "reload"]
    print("  + " + " ".join(reload_command))
    result = run(reload_command)
    if result.returncode != 0:
        raise MigrationError(
            f"reload failed (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()[-800:]}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_plan(plan: list[HostPlan], legacy_path: Path, settings: AgentSettings) -> None:
    print(f"agent config: {settings.path}")
    print(f"legacy map:   {legacy_path}")
    print(f"install root: {settings.install_root}")
    print(f"lineage root: {settings.lineage_root}")
    print()
    for item in plan:
        status = "OK" if not item.actions else "MIGRATE"
        print(f"[{status}] {item.hostname}")
        print(f"  legacy cert: {item.legacy.cert_path}")
        print(f"  in allowlist: {'yes' if item.in_allowlist else 'no'}")
        print(f"  lineage:      {'yes' if item.has_lineage else 'no'} ({item.lineage})")
        print(f"  installed:    {'yes' if item.installed_matches else 'no'} ({item.installed})")
        if item.actions:
            print(f"  actions:      {', '.join(item.actions)}")
    if not plan:
        print("No legacy SNI entries found.")


def _select_entries(entries: list[SniEntry], hostnames: list[str]) -> list[SniEntry]:
    if not hostnames:
        return entries
    wanted = {normalize_hostname(item) for item in hostnames}
    selected = [entry for entry in entries if entry.hostname in wanted]
    missing = sorted(wanted - {entry.hostname for entry in selected})
    if missing:
        raise MigrationError("hostnames not present in the legacy map: " + ", ".join(missing))
    return selected


def _apply(args: argparse.Namespace, settings: AgentSettings, legacy_path: Path, plan: list[HostPlan]) -> int:
    pending = [item for item in plan if item.actions]
    if not pending:
        print("Everything is already managed; nothing to do.")
        return EXIT_OK

    new_hosts = [item.hostname for item in pending if not item.in_allowlist]
    if new_hosts:
        merged = sorted({*settings.allowlist, *new_hosts})
        backup = write_with_backup(settings.path, upsert_allowlist(settings.text, merged))
        print(f"allowlist updated: {settings.path} (backup: {backup})")
        settings = load_settings(settings.path)

    failures: list[str] = []
    migrated: list[str] = []
    for item in pending:
        print(f"\n== {item.hostname}")
        try:
            if "issue" in item.actions and not args.skip_http_check:
                if settings.webroot is None:
                    raise MigrationError(
                        "HTTP-01 check needs certbot.webroot_path; "
                        "use --skip-http-check to bypass"
                    )
                check_http(item.hostname, settings.webroot)
            if "issue" in item.actions:
                issue_certificate(settings, item.hostname)
            install_certificate(settings, item.hostname)
            if not args.skip_verify:
                verify_certificate(settings, item.hostname)
            migrated.append(item.hostname)
        except MigrationError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            failures.append(item.hostname)

    if args.clean_legacy:
        if failures:
            print(
                "\nlegacy map preserved: migration failures: " + ", ".join(failures),
                file=sys.stderr,
            )
        elif migrated:
            text = legacy_path.read_text(encoding="utf-8")
            updated = remove_entries(text, set(migrated))
            if updated != text:
                backup = write_with_backup(legacy_path, updated)
                print(f"\nlegacy map updated: {legacy_path} (backup: {backup})")
                try:
                    postmap_legacy(settings, args.config_root, legacy_path)
                except MigrationError as exc:
                    print(f"  ! {exc}", file=sys.stderr)
                    failures.append("legacy-map")

    if failures:
        print("\nFailed: " + ", ".join(failures), file=sys.stderr)
        return EXIT_FAILURE
    print("\nMigration complete: " + ", ".join(migrated))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate unmanaged Postfix SNI entries to per-host Certbot certificates."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="agent TOML")
    parser.add_argument(
        "--config-root",
        default=str(DEFAULT_CONFIG_ROOT),
        help="host directory mounted at /tmp/docker-mailserver",
    )
    parser.add_argument("--legacy-map", default=None, help="explicit legacy SNI map path")
    parser.add_argument(
        "--hostname",
        action="append",
        default=[],
        help="only migrate this hostname (repeatable)",
    )
    parser.add_argument("--apply", action="store_true", help="perform changes")
    parser.add_argument(
        "--skip-http-check",
        action="store_true",
        help="do not probe HTTP-01 before issuing",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="skip the post-install TLS probes",
    )
    parser.add_argument(
        "--clean-legacy",
        action="store_true",
        help="remove migrated hostnames from the legacy SNI map",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="print the plan as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(Path(args.config))
        config_root = Path(args.config_root)
        legacy_path = (
            Path(args.legacy_map)
            if args.legacy_map
            else discover_legacy_map(config_root)
        )
        if legacy_path is None or not legacy_path.is_file():
            raise MigrationError(
                f"no legacy SNI map found under {config_root}/postfix "
                "(use --legacy-map)"
            )
        entries = parse_sni_map(legacy_path.read_text(encoding="utf-8"))
        entries = _select_entries(entries, args.hostname)
        plan = build_plan(settings, entries)
    except MigrationError as exc:
        print(f"migrate-legacy-sni: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "hostname": item.hostname,
                        "in_allowlist": item.in_allowlist,
                        "has_lineage": item.has_lineage,
                        "installed_matches": item.installed_matches,
                        "actions": item.actions,
                        "legacy_cert": item.legacy.cert_path,
                    }
                    for item in plan
                ],
                indent=2,
            )
        )
    else:
        _print_plan(plan, legacy_path, settings)

    if not args.apply:
        print("\nPlan only. Re-run with --apply to migrate.")
        return EXIT_OK

    return _apply(args, settings, legacy_path, plan)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
