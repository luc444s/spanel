#!/usr/bin/env python3
"""Install, update or remove the SPanel route reconciliation systemd timer.

The timer only invokes ``refresh_routes.py`` inside the already-running app
container. It does not create containers or keep a process alive.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "scripts" / "systemd"
SYSTEMD_DIR = Path("/etc/systemd/system")
SERVICE = "spanel-routes-refresh.service"
TIMER = "spanel-routes-refresh.timer"


def render_service(repo_root: Path, docker: str) -> str:
    text = (UNITS / SERVICE).read_text(encoding="utf-8")
    return text.replace("/opt/spanel", str(repo_root)).replace(
        "/usr/bin/docker", docker
    )


def render_timer(interval_minutes: int) -> str:
    if interval_minutes < 1:
        raise ValueError("El intervalo debe ser de al menos 1 minuto")
    text = (UNITS / TIMER).read_text(encoding="utf-8")
    return text.replace(
        "OnUnitActiveSec=5min", f"OnUnitActiveSec={interval_minutes}min"
    )


def run(command: list[str], dry_run: bool) -> None:
    print("+", " ".join(command))
    if not dry_run:
        subprocess.run(command, check=True)


def install(interval_minutes: int, dry_run: bool) -> int:
    if not shutil.which("systemctl"):
        print("systemctl no está disponible; no se instala el timer", file=sys.stderr)
        return 2
    if not dry_run and os.geteuid() != 0:
        print("El instalador debe ejecutarse como root", file=sys.stderr)
        return 2
    docker = shutil.which("docker") or "/usr/bin/docker"
    service = render_service(ROOT, docker)
    timer = render_timer(interval_minutes)
    if dry_run:
        print(f"# {SYSTEMD_DIR / SERVICE}\n{service}")
        print(f"# {SYSTEMD_DIR / TIMER}\n{timer}")
        return 0
    for name, content in ((SERVICE, service), (TIMER, timer)):
        destination = SYSTEMD_DIR / name
        temporary = destination.with_name(f".{name}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(destination)
    run(["systemctl", "daemon-reload"], dry_run=False)
    run(["systemctl", "enable", "--now", TIMER], dry_run=False)
    run(["systemctl", "restart", TIMER], dry_run=False)
    print("Timer instalado. Próxima ejecución:")
    subprocess.run(["systemctl", "list-timers", TIMER], check=False)
    return 0


def uninstall(dry_run: bool) -> int:
    if not shutil.which("systemctl"):
        return 2
    if not dry_run and os.geteuid() != 0:
        print("El instalador debe ejecutarse como root", file=sys.stderr)
        return 2
    run(["systemctl", "disable", "--now", TIMER], dry_run=dry_run)
    for name in (TIMER, SERVICE):
        path = SYSTEMD_DIR / name
        print("+", f"rm -f {path}")
        if not dry_run:
            path.unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"], dry_run=dry_run)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.uninstall:
            return uninstall(args.dry_run)
        return install(args.interval_minutes, args.dry_run)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"install_routes_timer: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
