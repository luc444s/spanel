#!/usr/bin/env bash
# Configure the private PowerDNS lab connection and deploy PanelTutor with it.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_SCRIPT="$ROOT/scripts/powerdns-lab.sh"
BASE_ENV="$ROOT/.env.docker"
PDNS_ENV="$ROOT/.env.powerdns"
BASE_COMPOSE="$ROOT/docker-compose.yml"
PDNS_COMPOSE="$ROOT/docker-compose.powerdns.yml"
STATE_DIR="${POWERDNS_LAB_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-lab}"
DNS_PORT="${POWERDNS_LAB_DNS_PORT:-5300}"
API_URL="http://spanel-powerdns-lab-auth:8081"
SERVER_ID="localhost"
TIMEOUT="5"
FORCE=0
BUILD=1
MANAGED_MARKER="# Managed by scripts/configure_powerdns.sh"

fail() { echo "error: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: scripts/configure_powerdns.sh COMMAND [OPTIONS]

Commands:
  plan      Show the intended lab, files and Compose deployment (read-only).
  apply     Start PowerDNS, write .env.powerdns and deploy app/apps-worker.
  status    Show labelled PowerDNS resources and PanelTutor service status.
  verify    Verify the private authenticated API from the app container.

Options:
  --state-dir PATH       PowerDNS runtime state (default: XDG/HOME state dir).
  --dns-port PORT        Loopback DNS TCP/UDP port (default: 5300).
  --api-url URL          Private PowerDNS origin on the Docker network.
  --server-id ID         PowerDNS server id (default: localhost).
  --timeout SECONDS      PowerDNS API timeout (default: 5).
  --powerdns-env PATH    Generated host environment file.
  --force                Replace a non-managed PowerDNS environment file.
  --no-build             Recreate services without rebuilding images.

The API key value is never copied into an environment file. Only its absolute
host path is written; Docker mounts that owner-only file read-only.
EOF
}

absolute_path() {
  local value="$1"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || fail "paths cannot contain newlines"
  realpath -m -- "$value"
}

validate() {
  [[ "$STATE_DIR" = /* ]] || fail "--state-dir must be absolute"
  [[ "$PDNS_ENV" = /* ]] || fail "--powerdns-env must resolve to an absolute path"
  [[ "$DNS_PORT" =~ ^[0-9]+$ ]] && (( DNS_PORT >= 1 && DNS_PORT <= 65535 )) \
    || fail "--dns-port must be between 1 and 65535"
  [[ "$TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "--timeout must be numeric"
  [[ "$API_URL" =~ ^https?://[^/@?#]+(:[0-9]+)?$ ]] \
    || fail "--api-url must be an HTTP(S) origin without credentials or path"
  [[ -n "$SERVER_ID" && "$SERVER_ID" != *['/?']* ]] || fail "invalid --server-id"
  [[ -x "$LAB_SCRIPT" ]] || fail "missing executable $LAB_SCRIPT"
  [[ -f "$BASE_COMPOSE" && -f "$PDNS_COMPOSE" ]] || fail "Compose files are missing"
}

compose() {
  docker compose \
    --project-directory "$ROOT" \
    --env-file "$BASE_ENV" \
    --env-file "$PDNS_ENV" \
    --file "$BASE_COMPOSE" \
    --file "$PDNS_COMPOSE" \
    "$@"
}

lab() {
  POWERDNS_LAB_STATE_DIR="$STATE_DIR" POWERDNS_LAB_DNS_PORT="$DNS_PORT" \
    "$LAB_SCRIPT" "$@"
}

write_environment() {
  local key_path="$STATE_DIR/api-key" parent temporary
  parent="$(dirname -- "$PDNS_ENV")"
  mkdir -p -- "$parent"
  if [[ -e "$PDNS_ENV" ]] && ! grep -Fxq "$MANAGED_MARKER" "$PDNS_ENV" && (( ! FORCE )); then
    fail "$PDNS_ENV is not managed by this script; use --force to replace it"
  fi
  temporary="$(mktemp "$parent/.env.powerdns.tmp.XXXXXXXX")"
  trap 'rm -f -- "${temporary:-}"' RETURN
  umask 077
  cat > "$temporary" <<EOF
$MANAGED_MARKER
# This file stores paths and connection metadata, never the API key value.
SPANEL_PDNS_API_URL=$API_URL
SPANEL_PDNS_API_KEY_PATH=$key_path
SPANEL_PDNS_SERVER_ID=$SERVER_ID
SPANEL_PDNS_TIMEOUT_SECONDS=$TIMEOUT
EOF
  chmod 600 "$temporary"
  mv -f -- "$temporary" "$PDNS_ENV"
  trap - RETURN
  echo "==> wrote private deployment metadata: $PDNS_ENV"
}

verify_private_api() {
  compose exec -T app python3 - <<'PY'
import os
import stat
import urllib.request

path = os.environ["SPANEL_PDNS_API_KEY_FILE"]
mode = os.stat(path).st_mode
if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
    raise SystemExit("PowerDNS API key mount is not an owner-only regular file")
key = open(path, encoding="utf-8").read().strip()
url = os.environ["SPANEL_PDNS_API_URL"].rstrip("/")
server = os.environ.get("SPANEL_PDNS_SERVER_ID", "localhost")
request = urllib.request.Request(
    f"{url}/api/v1/servers/{server}", headers={"X-API-Key": key}
)
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit("PowerDNS private API health check failed")
print("PASS app -> private authenticated PowerDNS API")
PY
}

cmd_plan() {
  lab plan
  cat <<EOF
PanelTutor PowerDNS deployment plan (read-only)
  base environment: $BASE_ENV
  generated metadata: $PDNS_ENV (mode 0600)
  API key mount source: $STATE_DIR/api-key
  Compose override: $PDNS_COMPOSE
  services: app, apps-worker
  build images: $([[ "$BUILD" -eq 1 ]] && echo yes || echo no)
EOF
}

cmd_apply() {
  command -v docker >/dev/null 2>&1 || fail "docker is required"
  [[ -f "$BASE_ENV" ]] || fail "$BASE_ENV is missing"
  lab up
  [[ -f "$STATE_DIR/api-key" ]] || fail "PowerDNS did not create its API key file"
  write_environment
  compose config --quiet
  local arguments=(up -d)
  (( BUILD )) && arguments+=(--build)
  arguments+=(app apps-worker)
  compose "${arguments[@]}"
  verify_private_api
  echo "==> PowerDNS configuration and PanelTutor deployment are ready"
}

cmd_status() {
  lab status
  if [[ -f "$PDNS_ENV" && -f "$BASE_ENV" ]]; then
    compose ps app apps-worker
  else
    echo "PanelTutor PowerDNS deployment: not configured ($PDNS_ENV missing)"
  fi
}

cmd_verify() {
  [[ -f "$BASE_ENV" && -f "$PDNS_ENV" ]] || fail "run apply first"
  lab status
  verify_private_api
}

main() {
  local command="${1:-}"
  [[ -n "$command" ]] || { usage; exit 2; }
  shift || true
  while (($#)); do
    case "$1" in
      --state-dir) (($# >= 2)) || fail "--state-dir requires a value"; STATE_DIR="$2"; shift 2 ;;
      --dns-port) (($# >= 2)) || fail "--dns-port requires a value"; DNS_PORT="$2"; shift 2 ;;
      --api-url) (($# >= 2)) || fail "--api-url requires a value"; API_URL="$2"; shift 2 ;;
      --server-id) (($# >= 2)) || fail "--server-id requires a value"; SERVER_ID="$2"; shift 2 ;;
      --timeout) (($# >= 2)) || fail "--timeout requires a value"; TIMEOUT="$2"; shift 2 ;;
      --powerdns-env) (($# >= 2)) || fail "--powerdns-env requires a value"; PDNS_ENV="$(absolute_path "$2")"; shift 2 ;;
      --force) FORCE=1; shift ;;
      --no-build) BUILD=0; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "unknown option: $1" ;;
    esac
  done
  PDNS_ENV="$(absolute_path "$PDNS_ENV")"
  validate
  case "$command" in
    plan) cmd_plan ;;
    apply) cmd_apply ;;
    status) cmd_status ;;
    verify) cmd_verify ;;
    help|-h|--help) usage ;;
    *) fail "unknown command: $command" ;;
  esac
}

main "$@"
