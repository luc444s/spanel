"""CLI `spanel-cert-agent`.

Uso:
    python3 -m spanel_cert_agent run-once [--config PATH] [--json]
    python3 -m spanel_cert_agent refresh  [--config PATH]
    python3 -m spanel_cert_agent status   [--config PATH] [--json]
    python3 -m spanel_cert_agent verify   [--hostname H] [--json]
    python3 -m spanel_cert_agent audit    [--json]
    python3 -m spanel_cert_agent sync     [--hostname H] [--json]
    python3 -m spanel_cert_agent install  --hostname H [--json]
    python3 -m spanel_cert_agent rollback --hostname H [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AgentConfigError, load_config
from .operations import (
    install_current,
    rollback_current,
    run_audit,
    sync_hostname,
    verify_only,
)
from .runner import CertAgent, _run_command
from .state import read_json


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    parser.add_argument("--json", action="store_true", dest="as_json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanel-cert-agent")
    parser.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run-once", help="procesa solicitudes pendientes y refresca estado")
    _add_common(run)
    refresh = sub.add_parser("refresh", help="solo regenera state.json")
    _add_common(refresh)
    status = sub.add_parser("status", help="muestra el estado publicado")
    _add_common(status)

    verify = sub.add_parser("verify", help="probes TLS sin escribir estado")
    _add_common(verify)
    verify.add_argument("--hostname", default=None)

    audit = sub.add_parser("audit", help="verificación continua y alertas")
    _add_common(audit)

    sync = sub.add_parser("sync", help="instala si Certbot cambió la huella")
    _add_common(sync)
    sync.add_argument("--hostname", default=None)

    install = sub.add_parser("install", help="instalación transaccional")
    _add_common(install)
    install.add_argument("--hostname", required=True)

    rollback = sub.add_parser("rollback", help="revierte a la generación anterior")
    _add_common(rollback)
    rollback.add_argument("--hostname", required=True)
    return parser


def _targets(config, hostname: str | None) -> list[str]:
    if hostname:
        config.require_hostname(hostname)
        return [hostname]
    return sorted(config.allowlist)


def _print_outcomes(outcomes, *, as_json: bool) -> int:
    failures = [item for item in outcomes if not item.ok]
    if as_json:
        print(
            json.dumps(
                [item.as_dict() for item in outcomes],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        for item in outcomes:
            state = "ok" if item.ok else "fail"
            print(f"{item.hostname}: {state} changed={item.changed} {item.error or ''}".rstrip())
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except AgentConfigError as exc:
        print(f"spanel-cert-agent: {exc}", file=sys.stderr)
        return 2
    as_json = getattr(args, "as_json", False)

    if args.command == "run-once":
        agent = CertAgent(config)
        result = agent.run_once()
        if as_json:
            print(json.dumps(result.as_dict(), sort_keys=True))
        else:
            print(f"procesadas={result.processed} fallidas={result.failed}")
        return 0

    if args.command == "refresh":
        agent = CertAgent(config)
        agent.refresh()
        print(f"estado actualizado: {config.state_path}")
        return 0

    if args.command == "status":
        state = read_json(Path(config.state_path))
        if state is None:
            print(f"sin estado legible en {config.state_path}", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        else:
            print(f"generado: {state.get('generated_at')}")
            for item in state.get("certificates", []):
                print(f"{item.get('hostname')}: {item.get('status')} ({item.get('deployment')})")
        return 0

    try:
        if args.command == "verify":
            report = verify_only(config, hostname=args.hostname, runner=_run_command)
            if as_json:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            else:
                for item in report["health"].get("certificates", []):
                    print(f"{item.get('hostname')}: ok={item.get('ok')}")
            return 0
        if args.command == "audit":
            report = run_audit(config, runner=_run_command)
            if as_json:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            else:
                print(f"generado: {report['health'].get('generated_at')}")
            return 0
        if args.command == "sync":
            outcomes = [
                sync_hostname(config, host, runner=_run_command)
                for host in _targets(config, args.hostname)
            ]
            return _print_outcomes(outcomes, as_json=as_json)
        if args.command == "install":
            outcome = install_current(config, args.hostname, runner=_run_command)
            return _print_outcomes([outcome], as_json=as_json)
        if args.command == "rollback":
            outcome = rollback_current(config, args.hostname, runner=_run_command)
            return _print_outcomes([outcome], as_json=as_json)
    except AgentConfigError as exc:
        print(f"spanel-cert-agent: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
