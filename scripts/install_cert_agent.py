#!/usr/bin/env python3
"""Install the mail certificate agent on any host (inventory phase).

The script is idempotent and server-agnostic:

- derives the repository path from its own location;
- generates the systemd units with the real Python, repo and config paths;
- discovers the Certbot lineages it can manage, or accepts `--allowlist`;
- detects the Certbot webroot from the renewal configs;
- treats the no-SNI default certificate as optional.

It does not configure Postfix/Dovecot or install the Certbot deploy hook. Until
those sections exist in the config, issuance fails closed, so the first
deployment is read-only with respect to the mail server.

Usage (as root):

    python3 scripts/install_cert_agent.py --dry-run
    python3 scripts/install_cert_agent.py
    python3 scripts/install_cert_agent.py --allowlist mail.example.com
    python3 scripts/install_cert_agent.py \\
        --default-cert /etc/ssl/default.fullchain.crt \\
        --default-key /etc/ssl/default.key

Exit codes: 0 ok, 1 error, 2 usage.
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
from datetime import UTC, datetime
from pathlib import Path

import tomllib

DEFAULT_CONFIG = Path("/etc/spanel/cert-agent.toml")
DEFAULT_STATE_DIR = Path("/var/lib/spanel/cert-manager")
DEFAULT_INSTALL_ROOT = Path("/etc/ssl/spanel/mail")
DEFAULT_LETSENCRYPT_ROOT = Path("/etc/letsencrypt/live")
DEFAULT_RENEWAL_ROOT = Path("/etc/letsencrypt/renewal")
DEFAULT_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
DEFAULT_UNIT_DIR = Path("/etc/systemd/system")


class InstallError(Exception):
    pass


def _normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().removesuffix(".")
    if not hostname or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in hostname):
        raise InstallError(f"hostname inválido: {value!r}")
    if hostname.startswith("-") or ".." in hostname:
        raise InstallError(f"hostname inválido: {value!r}")
    return hostname


def _discover_allowlist(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    found: list[str] = []
    for lineage in sorted(root.iterdir()):
        if not lineage.is_dir():
            continue
        if (lineage / "fullchain.pem").is_file() and (lineage / "privkey.pem").is_file():
            found.append(lineage.name)
    return found


def _detect_webroot(renewal_root: Path) -> Path | None:
    if not renewal_root.is_dir():
        return None
    for conf in sorted(renewal_root.glob("*.conf")):
        try:
            text = conf.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"^\s*webroot_path\s*=\s*(\S+)", text, re.MULTILINE)
        if match:
            return Path(match.group(1).rstrip(","))
    return None


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_install(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as origin:
            shutil.copyfileobj(origin, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_config(
    *,
    allowlist: list[str],
    install_root: Path,
    state_dir: Path,
    default_cert: Path | None,
    default_key: Path | None,
    certbot: str | None,
    webroot: Path | None,
    ca_bundle: Path | None,
    lineages: Path,
) -> str:
    lines = [
        "# Generado por scripts/install_cert_agent.py",
        "# Fase inventario: sin [postfix]/[dovecot] la emisión falla cerrada.",
        "allowlist = [" + ", ".join(_toml_string(item) for item in allowlist) + "]",
    ]
    if default_cert and default_key:
        lines.extend(
            [
                'default_certificate = "default"',
                f"default_cert_path = {_toml_string(str(default_cert))}",
                f"default_key_path = {_toml_string(str(default_key))}",
            ]
        )
    else:
        lines.append('default_certificate = ""')
    lines.extend(
        [
            f"install_root = {_toml_string(str(install_root))}",
            f"state_path = {_toml_string(str(state_dir / 'state.json'))}",
            f"alerts_path = {_toml_string(str(state_dir / 'alerts.json'))}",
            f"requests_dir = {_toml_string(str(state_dir / 'requests'))}",
            f"jobs_dir = {_toml_string(str(state_dir / 'jobs'))}",
            f"generations_root = {_toml_string(str(state_dir / 'generations'))}",
            f"backup_root = {_toml_string(str(state_dir / 'backups'))}",
            f"locks_dir = {_toml_string(str(state_dir / 'locks'))}",
            "renew_days = 30",
            "",
            "[verification]",
            "ports = [993, 465, 587]",
        ]
    )
    if ca_bundle and ca_bundle.is_file():
        lines.append(f"ca_file = {_toml_string(str(ca_bundle))}")
    lines.extend(
        [
            "timeout_seconds = 10",
            "audit_interval_seconds = 600",
            "",
            "[certbot]",
            f"executable = {_toml_string(certbot or 'certbot')}",
            'challenge = "webroot"' if webroot else 'challenge = "auto"',
        ]
    )
    if webroot:
        lines.append(f"webroot_path = {_toml_string(str(webroot))}")
    lines.append(f"lineage_root = {_toml_string(str(lineages))}")
    return "\n".join(lines) + "\n"


def _render_units(
    *,
    repo_root: Path,
    python: str,
    config: Path,
    state_dir: Path,
    docs: Path,
) -> dict[str, str]:
    agent = f"{python} -m spanel_cert_agent"
    common = (
        "[Unit]\n"
        "Description=Spanel mail certificate agent\n"
        f"Documentation=file:{docs}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        f"WorkingDirectory={repo_root}\n"
        f"Environment=PYTHONPATH={repo_root / 'scripts'}\n"
    )
    return {
        "spanel-cert-agent.service": (
            common
            + f"ExecStart={agent} run-once --config {config}\n"
            + "TimeoutStartSec=900\n"
            + "RemainAfterExit=no\n"
            + "PrivateTmp=true\n"
            + "ProtectHome=true\n"
            + "ProtectKernelTunables=true\n"
            + "ProtectControlGroups=true\n"
        ),
        "spanel-cert-audit.service": (
            common
            + f"ExecStart={agent} audit --config {config}\n"
            + "TimeoutStartSec=600\n"
            + "RemainAfterExit=no\n"
            + "PrivateTmp=true\n"
            + "ProtectHome=true\n"
            + "ProtectKernelTunables=true\n"
            + "ProtectControlGroups=true\n"
        ),
        "spanel-cert-agent.path": (
            "[Unit]\n"
            "Description=Spanel mail certificate agent: watch requests\n"
            f"Documentation=file:{docs}\n\n"
            "[Path]\n"
            f"PathChanged={state_dir / 'requests'}\n"
            f"DirectoryNotEmpty={state_dir / 'requests'}\n"
            "Unit=spanel-cert-agent.service\n\n"
            "[Install]\n"
            "WantedBy=paths.target\n"
        ),
        "spanel-cert-agent.timer": (
            "[Unit]\n"
            "Description=Spanel mail certificate agent: periodic refresh\n"
            f"Documentation=file:{docs}\n\n"
            "[Timer]\n"
            "OnBootSec=2min\n"
            "OnUnitActiveSec=10min\n"
            "Unit=spanel-cert-agent.service\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
        "spanel-cert-audit.timer": (
            "[Unit]\n"
            "Description=Spanel mail certificate audit\n"
            f"Documentation=file:{docs}\n\n"
            "[Timer]\n"
            "OnBootSec=3min\n"
            "OnUnitActiveSec=10min\n"
            "Unit=spanel-cert-audit.service\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def _write_config(path: Path, content: str, *, force: bool, dry_run: bool) -> str:
    if path.exists() and not force:
        return "conservada"
    if dry_run:
        return "se escribiría"
    _atomic_write(path, content, 0o600)
    return "escrita"


def _seed_certificates(
    allowlist: list[str],
    *,
    lineages: Path,
    install_root: Path,
    refresh: bool,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    seeded: list[str] = []
    skipped: list[str] = []
    for hostname in allowlist:
        lineage = lineages / hostname
        cert = lineage / "fullchain.pem"
        key = lineage / "privkey.pem"
        if not cert.is_file() or not key.is_file():
            skipped.append(hostname)
            continue
        destination = install_root / hostname
        cert_dst = destination / "fullchain.pem"
        key_dst = destination / "privkey.pem"
        if cert_dst.exists() and key_dst.exists() and not refresh:
            continue
        if dry_run:
            seeded.append(hostname)
            continue
        _atomic_install(cert, cert_dst, 0o644)
        _atomic_install(key, key_dst, 0o600)
        seeded.append(hostname)
    return seeded, skipped


def _install_units(
    units: dict[str, str], *, unit_dir: Path, dry_run: bool
) -> list[str]:
    changed: list[str] = []
    for name, content in units.items():
        target = unit_dir / name
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        changed.append(name)
        if not dry_run:
            _atomic_write(target, content, 0o644)
    if changed and not dry_run:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    return changed


def _refresh(repo_root: Path, config: Path, *, dry_run: bool) -> None:
    if dry_run:
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "scripts")
    subprocess.run(
        [sys.executable, "-m", "spanel_cert_agent", "refresh", "--config", str(config)],
        check=True,
        env=env,
    )


def _enable_units(*, dry_run: bool) -> None:
    if dry_run:
        return
    subprocess.run(
        [
            "systemctl",
            "enable",
            "--now",
            "spanel-cert-agent.path",
            "spanel-cert-agent.timer",
            "spanel-cert-audit.timer",
        ],
        check=True,
    )


def _compose_app_container(repo_root: Path) -> str | None:
    try:
        output = subprocess.run(
            ["docker", "compose", "ps", "-q", "app"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    ids = [line.strip() for line in output.splitlines() if line.strip()]
    return ids[0] if ids else None


def _has_state_mount(container: str, state_dir: Path) -> bool:
    try:
        output = subprocess.run(
            ["docker", "inspect", container, "--format", "{{json .Mounts}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return str(state_dir) in output


def _state_age_seconds(path: Path) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    generated = payload.get("generated_at")
    if not isinstance(generated, str):
        return None
    try:
        moment = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).total_seconds()


def _enabled(unit: str) -> bool | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() in {"enabled", "enabled-runtime", "static"}


def _check(repo_root: Path, args: argparse.Namespace) -> int:
    checks: list[dict[str, object]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    config_ok = False
    allowlist: list[str] = []
    if not args.config.is_file():
        add("config", False, f"falta {args.config}")
    else:
        try:
            raw = tomllib.loads(args.config.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            add("config", False, f"inválida: {exc}")
        else:
            allowlist = [str(item) for item in raw.get("allowlist") or []]
            config_ok = bool(allowlist)
            add(
                "config",
                config_ok,
                f"{args.config} con {len(allowlist)} hostname(s)",
            )

    add(
        "certbot",
        bool(args.certbot),
        args.certbot or "no encontrado",
    )

    units = (
        "spanel-cert-agent.service",
        "spanel-cert-agent.path",
        "spanel-cert-agent.timer",
        "spanel-cert-audit.service",
        "spanel-cert-audit.timer",
    )
    missing = [name for name in units if not (args.unit_dir / name).is_file()]
    add(
        "units",
        not missing,
        "todas instaladas" if not missing else f"faltan: {', '.join(missing)}",
    )

    enabled = {unit: _enabled(unit) for unit in ("spanel-cert-agent.path", "spanel-cert-agent.timer", "spanel-cert-audit.timer")}
    if any(value is None for value in enabled.values()):
        add("timers", None, "systemd no disponible")
    else:
        disabled = [unit for unit, value in enabled.items() if not value]
        add(
            "timers",
            not disabled,
            "habilitados" if not disabled else f"deshabilitados: {', '.join(disabled)}",
        )

    state_path = args.state_dir / "state.json"
    if not state_path.is_file():
        add("state", False, f"falta {state_path}")
    else:
        age = _state_age_seconds(state_path)
        if age is None:
            add("state", False, f"{state_path} inválido o sin generated_at")
        else:
            add(
                "state",
                age <= 900,
                f"{state_path} con {int(age)}s de antigüedad",
            )

    installed = sum(
        1
        for hostname in allowlist
        if (args.install_root / hostname / "fullchain.pem").is_file()
        and (args.install_root / hostname / "privkey.pem").is_file()
    )
    add(
        "certificados",
        installed > 0 if allowlist else None,
        f"{installed} de {len(allowlist)} instalados en {args.install_root}",
    )

    if not args.no_mount_check:
        container = _compose_app_container(repo_root)
        if container is None:
            add("mount-app", None, "no se encontró el contenedor app")
        else:
            add(
                "mount-app",
                _has_state_mount(container, args.state_dir),
                f"{container} → {args.state_dir}",
            )

    if args.json:
        print(json.dumps({"checks": checks}, indent=2, ensure_ascii=False))
    else:
        for item in checks:
            mark = "OK   " if item["ok"] is True else ("AVISO" if item["ok"] is None else "FALLA")
            print(f"{mark} {item['check']}: {item['detail']}")

    failed = any(item["ok"] is False for item in checks)
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--lineages", type=Path, default=DEFAULT_LETSENCRYPT_ROOT)
    parser.add_argument("--renewal-root", type=Path, default=DEFAULT_RENEWAL_ROOT)
    parser.add_argument("--ca-bundle", type=Path, default=DEFAULT_CA_BUNDLE)
    parser.add_argument("--unit-dir", type=Path, default=DEFAULT_UNIT_DIR)
    parser.add_argument("--default-cert", type=Path)
    parser.add_argument("--default-key", type=Path)
    parser.add_argument("--webroot", type=Path)
    parser.add_argument("--certbot", default=shutil.which("certbot"))
    parser.add_argument(
        "--allowlist",
        action="append",
        default=[],
        help="hostname a administrar (repetible); por defecto descubre los linajes completos",
    )
    parser.add_argument("--force-config", action="store_true", help="reescribir cert-agent.toml")
    parser.add_argument(
        "--refresh-certs",
        action="store_true",
        help="volver a copiar los certificados de Certbot al install_root",
    )
    parser.add_argument("--no-enable", action="store_true", help="no habilitar los timers")
    parser.add_argument("--no-mount-check", action="store_true", help="omitir la revisión del mount")
    parser.add_argument(
        "--check",
        action="store_true",
        help="solo comprobar si el agente está instalado y en estado reciente",
    )
    parser.add_argument("--json", action="store_true", help="salida JSON para --check")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    if args.check:
        return _check(repo_root, args)
    if os.geteuid() != 0 and not args.dry_run:
        print("FALLO: ejecutá como root (o usá --dry-run)")
        return 2
    if sys.version_info < (3, 11):
        print("FALLO: se requiere Python 3.11+ (tomllib)")
        return 2
    if not args.certbot:
        print("FALLO: no se encontró certbot (usá --certbot)")
        return 1
    if bool(args.default_cert) != bool(args.default_key):
        print("FALLO: --default-cert y --default-key van juntos")
        return 2
    if args.default_cert and (
        not args.default_cert.is_file() or not args.default_key.is_file()  # type: ignore[union-attr]
    ):
        print("FALLO: el certificado predeterminado no existe")
        return 1

    try:
        allowlist = [_normalize_hostname(item) for item in args.allowlist]
        if not allowlist:
            allowlist = [_normalize_hostname(item) for item in _discover_allowlist(args.lineages)]
        if not allowlist:
            raise InstallError(
                "no hay hostnames: usá --allowlist o creá linajes en Certbot"
            )
        allowlist = sorted(set(allowlist))

        webroot = args.webroot or _detect_webroot(args.renewal_root)
        units = _render_units(
            repo_root=repo_root,
            python=sys.executable,
            config=args.config,
            state_dir=args.state_dir,
            docs=repo_root / "docs" / "certificate-manager.md",
        )
        config_text = _render_config(
            allowlist=allowlist,
            install_root=args.install_root,
            state_dir=args.state_dir,
            default_cert=args.default_cert,
            default_key=args.default_key,
            certbot=args.certbot,
            webroot=webroot,
            ca_bundle=args.ca_bundle,
            lineages=args.lineages,
        )

        if args.dry_run:
            print("dry-run: no se escribe nada")

        if not args.dry_run:
            args.state_dir.mkdir(parents=True, exist_ok=True)
            for name in ("requests", "jobs", "generations", "backups", "locks"):
                directory = args.state_dir / name
                directory.mkdir(parents=True, exist_ok=True)
                os.chmod(directory, 0o750)
            args.install_root.mkdir(parents=True, exist_ok=True)

        config_state = _write_config(
            args.config, config_text, force=args.force_config, dry_run=args.dry_run
        )
        seeded, skipped = _seed_certificates(
            allowlist,
            lineages=args.lineages,
            install_root=args.install_root,
            refresh=args.refresh_certs,
            dry_run=args.dry_run,
        )
        changed_units = _install_units(
            units, unit_dir=args.unit_dir, dry_run=args.dry_run
        )
        _refresh(repo_root, args.config, dry_run=args.dry_run)
        if not args.no_enable:
            _enable_units(dry_run=args.dry_run)
    except (InstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FALLO: {exc}")
        return 1

    print(f"repo: {repo_root}")
    print(f"allowlist: {', '.join(allowlist)}")
    print(f"webroot: {webroot or 'no detectado (challenge=auto)'}")
    print(f"config: {args.config} ({config_state})")
    print(f"unidades actualizadas: {', '.join(changed_units) or 'ninguna'}")
    print(f"certificados copiados: {', '.join(seeded) or 'ninguno'}")
    if skipped:
        print(f"sin linaje completo (omitidos): {', '.join(skipped)}")
    print(f"estado: {args.state_dir / 'state.json'}")

    if not args.no_mount_check and not args.dry_run:
        container = _compose_app_container(repo_root)
        if container and not _has_state_mount(container, args.state_dir):
            print(
                f"AVISO: el contenedor app ({container}) no monta {args.state_dir}. "
                "Agregá el volumen a docker-compose.yml y recreá solo la app:"
            )
            print("  docker compose up -d --no-deps app")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
