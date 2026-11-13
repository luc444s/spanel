#!/usr/bin/env python3
"""Configure the certificate agent for an existing Docker Mailserver safely.

The default mode is a read-only plan. Use ``--apply`` to split managed SNI
entries from legacy entries, update the agent TOML, restart Docker Mailserver,
and validate that every pre-existing hostname remains available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import tomllib

DEFAULT_AGENT_CONFIG = Path("/etc/spanel/cert-agent.toml")
CONTAINER_CONFIG_ROOT = PurePosixPath("/tmp/docker-mailserver")
MANAGED_POSTFIX_RELATIVE = PurePosixPath("postfix/sni-spanel")
MANAGED_DOVECOT_RELATIVE = PurePosixPath("spanel-local_name.conf")
DOVECOT_INCLUDE = "!include_try /tmp/docker-mailserver/spanel-local_name.conf"


class ConfigureError(Exception):
    pass


@dataclass(frozen=True)
class ConfigurationPlan:
    container: str
    config_root: Path
    agent_config: Path
    managed_hostnames: tuple[str, ...]
    legacy_hostnames: tuple[str, ...]
    original_hostnames: tuple[str, ...]
    legacy_postfix_internal: str
    writes: dict[Path, str]


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=check,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = (
            getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
        )
        raise ConfigureError(
            f"failed {' '.join(command)}: {str(detail).strip()[:400]}"
        ) from exc


def _detect_config_root(container: str) -> Path:
    result = _run(["docker", "inspect", container])
    try:
        payload = json.loads(result.stdout)[0]
        mounts = payload["Mounts"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ConfigureError(
            f"invalid docker inspect response for {container}"
        ) from exc
    for mount in mounts:
        if mount.get("Destination") == str(CONTAINER_CONFIG_ROOT):
            source = mount.get("Source")
            if source:
                return Path(source)
    raise ConfigureError(f"{container} does not mount {CONTAINER_CONFIG_ROOT}")


def _allowlist(config: Path) -> tuple[str, ...]:
    if not config.is_file():
        raise ConfigureError(f"missing agent config: {config}")
    try:
        raw = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigureError(f"invalid agent config {config}: {exc}") from exc
    values = raw.get("allowlist")
    if not isinstance(values, list) or not values:
        raise ConfigureError("agent allowlist is empty")
    return tuple(
        sorted({str(value).strip().lower().removesuffix(".") for value in values})
    )


def _postfix_maps(text: str) -> list[str]:
    match = re.search(r"^tls_server_sni_maps\s*=\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise ConfigureError("postfix-main.cf has no tls_server_sni_maps")
    return re.findall(r"hash:([^,\s]+)", match.group(1))


def _host_path(config_root: Path, internal: str) -> Path:
    path = PurePosixPath(internal)
    try:
        relative = path.relative_to(CONTAINER_CONFIG_ROOT)
    except ValueError as exc:
        raise ConfigureError(
            f"Postfix map is outside {CONTAINER_CONFIG_ROOT}: {internal}"
        ) from exc
    return config_root.joinpath(*relative.parts)


def _parse_postfix_entries(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        hostname = line.split(maxsplit=1)[0].lower().removesuffix(".")
        entries[hostname] = line
    return entries


def _render_postfix_entries(entries: dict[str, str]) -> str:
    return "".join(f"{entries[name]}\n" for name in sorted(entries))


def _parse_dovecot_blocks(text: str) -> dict[str, str]:
    pattern = re.compile(r"local_name\s+(\S+)\s*\{.*?^\}", re.MULTILINE | re.DOTALL)
    return {
        match.group(1).lower().removesuffix("."): match.group(0).strip()
        for match in pattern.finditer(text)
    }


def _remove_dovecot_blocks(text: str, hostnames: set[str]) -> str:
    pattern = re.compile(r"local_name\s+(\S+)\s*\{.*?^\}\s*", re.MULTILINE | re.DOTALL)
    result = pattern.sub(
        lambda match: (
            ""
            if match.group(1).lower().removesuffix(".") in hostnames
            else match.group(0)
        ),
        text,
    )
    lines = [
        line for line in result.rstrip().splitlines() if line.strip() != DOVECOT_INCLUDE
    ]
    return "\n".join(lines).rstrip() + f"\n\n{DOVECOT_INCLUDE}\n"


def _render_dovecot_blocks(blocks: dict[str, str]) -> str:
    return "\n\n".join(blocks[name] for name in sorted(blocks)).strip() + "\n"


def _replace_postfix_maps(text: str, maps: list[str]) -> str:
    value = ", ".join(f"hash:{item}" for item in maps)
    return re.sub(
        r"^tls_server_sni_maps\s*=.*$",
        f"tls_server_sni_maps = {value}",
        text,
        count=1,
        flags=re.MULTILINE,
    )


def _upsert_toml_section(text: str, name: str, body: str) -> str:
    block = f"[{name}]\n{body.strip()}\n"
    pattern = re.compile(
        rf"^\[{re.escape(name)}\]\s*\n.*?(?=^\[|\Z)", re.MULTILINE | re.DOTALL
    )
    if pattern.search(text):
        return pattern.sub(block + "\n", text).rstrip() + "\n"
    return text.rstrip() + "\n\n" + block


def build_plan(
    *,
    container: str,
    config_root: Path,
    agent_config: Path,
) -> ConfigurationPlan:
    managed = set(_allowlist(agent_config))
    postfix_main = config_root / "postfix-main.cf"
    dovecot_config = config_root / "dovecot.cf"
    if not postfix_main.is_file() or not dovecot_config.is_file():
        raise ConfigureError(f"missing postfix-main.cf or dovecot.cf in {config_root}")

    postfix_text = postfix_main.read_text(encoding="utf-8")
    current_maps = _postfix_maps(postfix_text)
    managed_internal = str(CONTAINER_CONFIG_ROOT / MANAGED_POSTFIX_RELATIVE)
    legacy_maps = [item for item in current_maps if item != managed_internal]
    if not legacy_maps:
        raise ConfigureError("no legacy Postfix SNI map was found")
    legacy_internal = legacy_maps[0]
    legacy_path = _host_path(config_root, legacy_internal)
    if not legacy_path.is_file():
        raise ConfigureError(f"missing Postfix SNI map: {legacy_path}")

    managed_postfix_path = config_root.joinpath(*MANAGED_POSTFIX_RELATIVE.parts)
    legacy_entries = _parse_postfix_entries(legacy_path.read_text(encoding="utf-8"))
    managed_entries = _parse_postfix_entries(
        managed_postfix_path.read_text(encoding="utf-8")
        if managed_postfix_path.is_file()
        else ""
    )
    for hostname in managed:
        if hostname in legacy_entries:
            managed_entries[hostname] = legacy_entries.pop(hostname)

    dovecot_text = dovecot_config.read_text(encoding="utf-8")
    source_blocks = _parse_dovecot_blocks(dovecot_text)
    managed_dovecot_path = config_root.joinpath(*MANAGED_DOVECOT_RELATIVE.parts)
    managed_blocks = _parse_dovecot_blocks(
        managed_dovecot_path.read_text(encoding="utf-8")
        if managed_dovecot_path.is_file()
        else ""
    )
    for hostname in managed:
        if hostname in source_blocks:
            managed_blocks[hostname] = source_blocks[hostname]

    original_hostnames = (
        set(source_blocks)
        | set(managed_blocks)
        | set(legacy_entries)
        | set(managed_entries)
    )
    missing = sorted(
        hostname
        for hostname in managed
        if hostname not in managed_entries or hostname not in managed_blocks
    )
    if missing:
        raise ConfigureError(
            "managed hostnames are absent from current Postfix/Dovecot config: "
            + ", ".join(missing)
        )

    ordered_maps = [managed_internal, *legacy_maps]
    agent_text = agent_config.read_text(encoding="utf-8")
    agent_text = _upsert_toml_section(
        agent_text,
        "postfix",
        f'sni_map_path = "{managed_postfix_path}"\n'
        f'postmap_command = ["docker", "exec", "{container}", "postmap", "{managed_internal}"]\n'
        f'reload_command = ["docker", "exec", "{container}", "postfix", "reload"]',
    )
    agent_text = _upsert_toml_section(
        agent_text,
        "dovecot",
        f'local_name_path = "{managed_dovecot_path}"\n'
        f'reload_command = ["docker", "exec", "{container}", "dovecot", "reload"]',
    )

    writes = {
        postfix_main: _replace_postfix_maps(postfix_text, ordered_maps),
        legacy_path: _render_postfix_entries(legacy_entries),
        managed_postfix_path: _render_postfix_entries(managed_entries),
        dovecot_config: _remove_dovecot_blocks(dovecot_text, managed),
        managed_dovecot_path: _render_dovecot_blocks(managed_blocks),
        agent_config: agent_text,
    }
    return ConfigurationPlan(
        container=container,
        config_root=config_root,
        agent_config=agent_config,
        managed_hostnames=tuple(sorted(managed)),
        legacy_hostnames=tuple(sorted(original_hostnames - managed)),
        original_hostnames=tuple(sorted(original_hostnames)),
        legacy_postfix_internal=legacy_internal,
        writes=writes,
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def _wait_for_container(container: str) -> None:
    for _ in range(30):
        result = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container], check=False
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(1)
    raise ConfigureError(f"{container} did not return to running state")


def apply_plan(plan: ConfigurationPlan) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    originals: dict[Path, bytes | None] = {}
    changed = {
        path: content
        for path, content in plan.writes.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    }
    if not changed:
        print("already configured; no files changed")
        return
    try:
        for path, content in changed.items():
            originals[path] = path.read_bytes() if path.exists() else None
            if path.exists():
                backup = path.with_name(f"{path.name}.spanel-dms-{stamp}.bak")
                backup.write_bytes(originals[path] or b"")
            _atomic_write(path, content)

        managed_internal = str(CONTAINER_CONFIG_ROOT / MANAGED_POSTFIX_RELATIVE)
        _run(["docker", "exec", plan.container, "postmap", managed_internal])
        _run(
            ["docker", "exec", plan.container, "postmap", plan.legacy_postfix_internal]
        )
        _run(["docker", "restart", plan.container])
        _wait_for_container(plan.container)
        postfix = _run(
            ["docker", "exec", plan.container, "postconf", "-h", "tls_server_sni_maps"]
        )
        if (
            managed_internal not in postfix.stdout
            or plan.legacy_postfix_internal not in postfix.stdout
        ):
            raise ConfigureError("Postfix did not load both SNI maps")
        dovecot = _run(["docker", "exec", plan.container, "doveconf", "-n"])
        missing = [
            name
            for name in plan.original_hostnames
            if f"local_name {name} " not in dovecot.stdout
        ]
        if missing:
            raise ConfigureError("Dovecot lost hostnames: " + ", ".join(missing))
        _run(["docker", "exec", plan.container, "postfix", "reload"])
        _run(["docker", "exec", plan.container, "dovecot", "reload"])
    except Exception:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        _run(["docker", "restart", plan.container], check=False)
        raise


def _print_plan(plan: ConfigurationPlan, *, as_json: bool) -> None:
    changes = [
        {"path": str(path), "change": "update" if path.exists() else "create"}
        for path, content in plan.writes.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]
    payload = {
        "container": plan.container,
        "config_root": str(plan.config_root),
        "managed_hostnames": list(plan.managed_hostnames),
        "legacy_hostnames": list(plan.legacy_hostnames),
        "changes": changes,
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"Docker Mailserver: {plan.container} ({plan.config_root})")
    print("Managed by Spanel: " + ", ".join(plan.managed_hostnames))
    print("Preserved legacy: " + (", ".join(plan.legacy_hostnames) or "none"))
    for item in changes:
        print(f"- {item['change']}: {item['path']}")
    print(
        "PLAN ONLY: run again with --apply after review"
        if changes
        else "No changes required"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="mailserver")
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--agent-config", type=Path, default=DEFAULT_AGENT_CONFIG)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.config_root or _detect_config_root(args.container)
        plan = build_plan(
            container=args.container,
            config_root=root,
            agent_config=args.agent_config,
        )
        _print_plan(plan, as_json=args.json)
        if args.apply:
            if os.geteuid() != 0:
                raise ConfigureError("--apply must run as root")
            apply_plan(plan)
            print("applied and validated")
        return 0
    except ConfigureError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
