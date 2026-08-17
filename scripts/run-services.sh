#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL_PGDATA_DIR="$HOME/.postgresql"
PGDATA_DIR="${SPANEL_PGDATA:-$CANONICAL_PGDATA_DIR}"
PGLOG_FILE="${SPANEL_PGLOG:-$HOME/.postgresql.log}"
PYTHON_BIN="${SPANEL_PYTHON:-$ROOT_DIR/.venv/bin/python3}"
DEFAULT_DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:5432/spanel"
EFFECTIVE_DATABASE_URL="${SYSTUTOR_DATABASE_URL:-$DEFAULT_DATABASE_URL}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

eval "$(EFFECTIVE_DATABASE_URL="$EFFECTIVE_DATABASE_URL" "$PYTHON_BIN" - <<'PY'
from urllib.parse import urlparse
import os

url = os.environ["EFFECTIVE_DATABASE_URL"]
parsed = urlparse(url)
db = parsed.path.lstrip("/") or "spanel"
user = parsed.username or "postgres"
host = parsed.hostname or "127.0.0.1"
port = parsed.port or 5432
password = parsed.password or ""

def emit(name: str, value: str) -> None:
    safe = value.replace("'", "'\"'\"'")
    print(f"{name}='{safe}'")

emit("PGDATABASE_VALUE", db)
emit("PGUSER_VALUE", user)
emit("PGHOST_VALUE", host)
emit("PGPORT_VALUE", str(port))
emit("PGPASSWORD_VALUE", password)
PY
)"

export PGPASSWORD="$PGPASSWORD_VALUE"

is_local_pg_host() {
  case "$PGHOST_VALUE" in
    127.0.0.1|localhost|::1)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ "$PGDATABASE_VALUE" = "systutor" ]; then
  printf 'Refusing to use database "%s" for Spanel.\n' "$PGDATABASE_VALUE" >&2
  printf 'Point `SYSTUTOR_DATABASE_URL` to dedicated Spanel DB, for example:\n' >&2
  printf '  postgresql+psycopg://postgres@127.0.0.1:5432/spanel\n' >&2
  exit 1
fi

if is_local_pg_host && [ "$PGPORT_VALUE" = "5432" ] && [ "$PGDATA_DIR" != "$CANONICAL_PGDATA_DIR" ]; then
  printf 'Refusing to start non-canonical cluster on %s:%s.\n' "$PGHOST_VALUE" "$PGPORT_VALUE" >&2
  printf 'Canonical local cluster must be %s on 127.0.0.1:5432.\n' "$CANONICAL_PGDATA_DIR" >&2
  printf 'Use separate databases inside canonical cluster, or explicit alternate port for isolated PGDATA.\n' >&2
  exit 1
fi

ensure_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

wait_for_postgres() {
  for _ in $(seq 1 30); do
    if pg_isready -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  printf 'PostgreSQL did not become ready on %s:%s\n' "$PGHOST_VALUE" "$PGPORT_VALUE" >&2
  exit 1
}

ensure_database() {
  if psql -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${PGDATABASE_VALUE}'" | grep -q 1; then
    return 0
  fi
  createdb -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" "$PGDATABASE_VALUE"
}

ensure_schema_and_seed() {
  cd "$ROOT_DIR/vendor/systutor-core"
  SYSTUTOR_DATABASE_URL="$EFFECTIVE_DATABASE_URL" \
  "$PYTHON_BIN" - <<'PY'
from app.main import app
from systutor.api.seed import seed_demo_data
from systutor.core.database import Base, build_engine, build_session_factory

settings = app.state.settings
engine = build_engine(settings)
Base.metadata.create_all(bind=engine)

with build_session_factory(settings)() as db:
    seed_demo_data(db, settings, app.state.plugin_runtime.list_results())
PY
}

if ! command -v pg_isready >/dev/null 2>&1 || ! pg_isready -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" >/dev/null 2>&1; then
  if ! is_local_pg_host; then
    printf 'PostgreSQL not reachable on remote host %s:%s.\n' "$PGHOST_VALUE" "$PGPORT_VALUE" >&2
    exit 1
  fi

  ensure_command initdb
  ensure_command pg_ctl
  ensure_command createdb
  ensure_command psql

  if [ ! -f "$PGDATA_DIR/PG_VERSION" ]; then
    mkdir -p "$PGDATA_DIR"
    initdb -D "$PGDATA_DIR" -U "$PGUSER_VALUE" -A trust >/dev/null
  fi

  if ! pg_ctl -D "$PGDATA_DIR" status >/dev/null 2>&1; then
    pg_ctl -D "$PGDATA_DIR" -l "$PGLOG_FILE" -o "-h $PGHOST_VALUE -p $PGPORT_VALUE" start >/dev/null
  fi
fi

ensure_command pg_isready
ensure_command createdb
ensure_command psql
wait_for_postgres
ensure_database
ensure_schema_and_seed

cd "$ROOT_DIR/vendor/systutor-core"
exec env SYSTUTOR_DATABASE_URL="$EFFECTIVE_DATABASE_URL" bash "$ROOT_DIR/scripts/run-uvicorn.sh" --host 127.0.0.1 --port 8001 --reload
