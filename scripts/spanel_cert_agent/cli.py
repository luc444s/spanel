"""CLI `spanel-cert-agent`.

Uso:
    python3 -m spanel_cert_agent run-once [--config PATH] [--json]
    python3 -m spanel_cert_agent refresh  [--config PATH]
    python3 -m spanel_cert_agent status   [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import AgentConfigError, load_config
from .runner import CertAgent
from .state import read_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanel-cert-agent")
    parser.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run-once", help="procesa solicitudes pendientes y refresca estado")
    run.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    run.add_argument("--json", action="store_true", dest="as_json")
    refresh = sub.add_parser("refresh", help="solo regenera state.json")
    refresh.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    status = sub.add_parser("status", help="muestra el estado publicado")
    status.add_argument("--config", default=None, help="ruta de cert-agent.toml")
    status.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except AgentConfigError as exc:
        print(f"spanel-cert-agent: {exc}", file=sys.stderr)
        return 2

    if args.command == "run-once":
        agent = CertAgent(config)
        result = agent.run_once()
        if getattr(args, "as_json", False):
            print(json.dumps(result.as_dict(), sort_keys=True))
        else:
            print(f"procesadas={result.processed} fallidas={result.failed}")
        return 0

    if args.command == "refresh":
        agent = CertAgent(config)
        agent.refresh()
        print(f"estado actualizado: {config.state_path}")
        return 0

    state = read_json(Path(config.state_path))
    if state is None:
        print(f"sin estado legible en {config.state_path}", file=sys.stderr)
        return 1
    if getattr(args, "as_json", False):
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    else:
        print(f"generado: {state.get('generated_at')}")
        for item in state.get("certificates", []):
            print(
                f"{item.get('hostname')}: {item.get('status')} "
                f"({item.get('deployment')})"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
