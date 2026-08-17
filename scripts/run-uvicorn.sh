#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${SPANEL_PYTHON:-$ROOT_DIR/.venv/bin/python3}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR/vendor/systutor-core"
exec "$PYTHON_BIN" -m uvicorn app.main:app "$@"
