#!/usr/bin/env bash
# Isolated PowerDNS Authoritative + PostgreSQL operator lab.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="${SCRIPT_DIR}/powerdns-lab"
COMPOSE_FILE="${LAB_DIR}/compose.yml"
PROJECT="spanel-powerdns-lab"
LABEL="com.spanel.powerdns-lab"
AUTH="${PROJECT}-auth"
DB="${PROJECT}-postgres"
NETWORK="${PROJECT}-net"
VOLUME="${PROJECT}-postgres-data"
DNS_PORT="${POWERDNS_LAB_DNS_PORT:-5300}"
STATE_DIR="${POWERDNS_LAB_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-lab}"
LAB_ADDRESS="${POWERDNS_LAB_ADDRESS:-192.0.2.53}"
OWNER_FILE="${STATE_DIR}/.owner"

fail() { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
usage() {
  cat <<'EOF'
Usage: scripts/powerdns-lab.sh COMMAND [OPTIONS]

Commands: plan, doctor, up, status, seed, verify, down
Options for down: --purge [--yes]

Environment:
  POWERDNS_LAB_DNS_PORT   loopback TCP/UDP port (default: 5300; may be 53)
  POWERDNS_LAB_STATE_DIR  absolute runtime state path
  POWERDNS_LAB_ADDRESS    pilot.test A-record address (default: 192.0.2.53)
EOF
}

validate_settings() {
  [[ "$DNS_PORT" =~ ^[0-9]+$ ]] && (( DNS_PORT >= 1 && DNS_PORT <= 65535 )) || fail "invalid POWERDNS_LAB_DNS_PORT"
  [[ "$STATE_DIR" = /* ]] || fail "POWERDNS_LAB_STATE_DIR must be absolute"
  [[ "$LAB_ADDRESS" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "POWERDNS_LAB_ADDRESS must be IPv4"
}

compose() {
  POWERDNS_LAB_STATE_DIR="$STATE_DIR" POWERDNS_LAB_DNS_PORT="$DNS_PORT" \
    docker compose --project-name "$PROJECT" --file "$COMPOSE_FILE" "$@"
}

require_live_tools() {
  local tool
  for tool in docker dig; do command -v "$tool" >/dev/null 2>&1 || fail "$tool is not in PATH"; done
  docker info >/dev/null 2>&1 || fail "cannot talk to the Docker daemon"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
}

resource_label() {
  local kind="$1" name="$2"
  case "$kind" in
    container) docker inspect --type container -f "{{index .Config.Labels \"${LABEL}\"}}" "$name" 2>/dev/null || true ;;
    network) docker network inspect -f "{{index .Labels \"${LABEL}\"}}" "$name" 2>/dev/null || true ;;
    volume) docker volume inspect -f "{{index .Labels \"${LABEL}\"}}" "$name" 2>/dev/null || true ;;
  esac
}

resource_exists() {
  case "$1" in
    container) docker inspect --type container "$2" >/dev/null 2>&1 ;;
    network) docker network inspect "$2" >/dev/null 2>&1 ;;
    volume) docker volume inspect "$2" >/dev/null 2>&1 ;;
  esac
}

guard_resource() {
  local kind="$1" name="$2"
  if resource_exists "$kind" "$name" && [[ "$(resource_label "$kind" "$name")" != true ]]; then
    fail "refusing foreign ${kind} named ${name} (ownership label missing)"
  fi
}

guard_all() {
  guard_resource container "$AUTH"
  guard_resource container "$DB"
  guard_resource network "$NETWORK"
  guard_resource volume "$VOLUME"
}

listener_lines() {
  local proto="$1" port="$2"
  command -v ss >/dev/null 2>&1 || return 0
  if [[ "$proto" = tcp ]]; then ss -H -ltnp "sport = :${port}" 2>/dev/null || true
  else ss -H -lunp "sport = :${port}" 2>/dev/null || true; fi
}

port_busy() { [[ -n "$(listener_lines "$1" "$2")" ]]; }

report_port() {
  local proto="$1" port="$2" lines
  lines="$(listener_lines "$proto" "$port")"
  if [[ -n "$lines" ]]; then
    printf '%s port %s: IN USE\n%s\n' "${proto^^}" "$port" "$lines"
  else
    printf '%s port %s: available\n' "${proto^^}" "$port"
  fi
}

auth_running_on_selected_port() {
  resource_exists container "$AUTH" || return 1
  [[ "$(resource_label container "$AUTH")" = true ]] || return 1
  [[ "$(docker inspect -f '{{.State.Running}}' "$AUTH" 2>/dev/null || true)" = true ]] || return 1
  local ports
  ports="$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$AUTH")"
  [[ "$ports" == *"127.0.0.1"* && "$ports" == *"\"${DNS_PORT}\""* ]]
}

preflight_selected_port() {
  auth_running_on_selected_port && return 0
  local failed=0 proto
  for proto in tcp udp; do
    if port_busy "$proto" "$DNS_PORT"; then report_port "$proto" "$DNS_PORT" >&2; failed=1; fi
  done
  (( failed == 0 )) || fail "selected DNS port is occupied; no lab resources were changed"
}

ensure_state() {
  if [[ -e "$STATE_DIR" && ! -f "$OWNER_FILE" ]]; then
    [[ -d "$STATE_DIR" && -z "$(find "$STATE_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "state directory exists without lab ownership marker"
  fi
  mkdir -p "$STATE_DIR"
  if [[ -f "$OWNER_FILE" ]]; then
    [[ "$(cat "$OWNER_FILE")" = "$PROJECT" ]] || fail "foreign state ownership marker"
  else
    printf '%s\n' "$PROJECT" > "$OWNER_FILE"
  fi
  chmod 700 "$STATE_DIR"
  local secret
  for secret in postgres-password api-key; do
    if [[ ! -f "$STATE_DIR/$secret" ]]; then
      if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32 > "$STATE_DIR/$secret"
      else head -c 48 /dev/urandom | base64 > "$STATE_DIR/$secret"; fi
    fi
    chmod 600 "$STATE_DIR/$secret"
  done
  local config_tmp="$STATE_DIR/.pdns.conf.tmp"
  umask 077
  {
    cat <<'EOF'
daemon=no
guardian=no
disable-syslog=yes
setuid=pdns
setgid=pdns
launch=gpgsql
gpgsql-host=pdns-postgres
gpgsql-port=5432
gpgsql-dbname=pdns
gpgsql-user=pdns
EOF
    printf 'gpgsql-password=%s\n' "$(cat "$STATE_DIR/postgres-password")"
    cat <<'EOF'
local-address=0.0.0.0
local-port=53
api=yes
EOF
    printf 'api-key=%s\n' "$(cat "$STATE_DIR/api-key")"
    cat <<'EOF'
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
webserver-allow-from=0.0.0.0/0
EOF
  } > "$config_tmp"
  chmod 600 "$config_tmp"
  mv -f -- "$config_tmp" "$STATE_DIR/pdns.conf"
}

wait_healthy() {
  local name="$1" i status
  for i in $(seq 1 90); do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || true)"
    [[ "$status" = healthy ]] && return 0
    [[ "$status" = exited || "$status" = dead ]] && fail "$name stopped before becoming healthy"
    sleep 2
  done
  fail "timeout waiting for $name healthcheck"
}

cmd_plan() {
  cat <<EOF
PowerDNS lab plan (read-only)
  project: $PROJECT
  containers: $AUTH, $DB
  network: $NETWORK (private)
  volume: $VOLUME (preserved by normal down)
  DNS: 127.0.0.1:$DNS_PORT TCP and UDP
  API/PostgreSQL host ports: none
  state: $STATE_DIR (secret values are never displayed)
  zone: pilot.test; ns1 and ns2 share $LAB_ADDRESS (not physical redundancy)
EOF
}

cmd_doctor() {
  info "dependency diagnostics (read-only)"
  local tool
  for tool in bash docker dig ss; do
    if command -v "$tool" >/dev/null 2>&1; then echo "$tool: found"; else echo "$tool: missing"; fi
  done
  report_port tcp 53
  report_port udp 53
}

cmd_up() {
  require_live_tools
  guard_all
  preflight_selected_port
  ensure_state
  compose up -d
  wait_healthy "$DB"
  wait_healthy "$AUTH"
  info "healthy; DNS is on 127.0.0.1:${DNS_PORT} (TCP/UDP); API remains private"
}

cmd_status() {
  require_live_tools
  guard_all
  echo "PowerDNS lab resources (no secrets):"
  docker ps -a --filter "label=${LABEL}=true" --filter "name=^/${PROJECT}-" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  resource_exists volume "$VOLUME" && echo "volume $VOLUME: present" || echo "volume $VOLUME: absent"
}

cmd_seed() {
  require_live_tools; guard_all
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$DB" 2>/dev/null || true)" = healthy ]] || fail "lab database is not healthy; run up"
  docker exec -i -u postgres "$DB" psql -v ON_ERROR_STOP=1 -U pdns -d pdns >/dev/null <<SQL
BEGIN;
INSERT INTO domains(name,type) VALUES ('pilot.test','NATIVE') ON CONFLICT (name) DO UPDATE SET type='NATIVE';
DELETE FROM records WHERE domain_id=(SELECT id FROM domains WHERE name='pilot.test');
INSERT INTO records(domain_id,name,type,content,ttl,prio,disabled,auth)
SELECT id,'pilot.test','SOA','ns1.pilot.test hostmaster.pilot.test 2026111201 3600 600 1209600 300',300,NULL::INT,false,true FROM domains WHERE name='pilot.test'
UNION ALL SELECT id,'pilot.test','NS','ns1.pilot.test',300,NULL::INT,false,true FROM domains WHERE name='pilot.test'
UNION ALL SELECT id,'pilot.test','NS','ns2.pilot.test',300,NULL::INT,false,true FROM domains WHERE name='pilot.test'
UNION ALL SELECT id,'pilot.test','A','$LAB_ADDRESS',300,NULL::INT,false,true FROM domains WHERE name='pilot.test'
UNION ALL SELECT id,'ns1.pilot.test','A','$LAB_ADDRESS',300,NULL::INT,false,true FROM domains WHERE name='pilot.test'
UNION ALL SELECT id,'ns2.pilot.test','A','$LAB_ADDRESS',300,NULL::INT,false,true FROM domains WHERE name='pilot.test';
COMMIT;
SQL
  docker exec "$AUTH" pdns_control rediscover >/dev/null
  info "pilot.test reconciled; ns1/ns2 share one server and are not physically redundant"
}

check_query() {
  local transport="$1" type="$2" name="$3" expected="$4" output args=(+norecurse +comments +answer)
  [[ "$transport" = TCP ]] && args+=(+tcp)
  output="$(dig @127.0.0.1 -p "$DNS_PORT" "${args[@]}" "$name" "$type")"
  grep -Eq 'flags:.* aa[ ;]' <<<"$output" || fail "$transport $type answer is not authoritative"
  grep -Fq "$expected" <<<"$output" || fail "$transport $type answer lacks expected data"
  echo "PASS $transport $type $name (authoritative)"
}

cmd_verify() {
  require_live_tools; guard_all
  local transport
  for transport in UDP TCP; do
    check_query "$transport" SOA pilot.test ns1.pilot.test
    check_query "$transport" NS pilot.test ns2.pilot.test
    check_query "$transport" A pilot.test "$LAB_ADDRESS"
  done
  docker exec "$AUTH" python3 -c 'import urllib.request; key=open("/run/secrets/pdns_api_key").read().strip(); request=urllib.request.Request("http://127.0.0.1:8081/api/v1/servers/localhost", headers={"X-API-Key": key}); urllib.request.urlopen(request, timeout=5).read()'
  echo "PASS private authenticated PowerDNS API"
}

purge_state() {
  [[ -f "$OWNER_FILE" && "$(cat "$OWNER_FILE")" = "$PROJECT" ]] || fail "refusing to purge unowned runtime state"
  rm -f -- "$STATE_DIR/postgres-password" "$STATE_DIR/api-key" "$STATE_DIR/pdns.conf" "$OWNER_FILE"
  rmdir -- "$STATE_DIR" || fail "state directory contains foreign files; left in place"
}

cmd_down() {
  local purge="$1" yes="$2"
  require_live_tools; guard_all
  resource_exists container "$AUTH" && docker rm -f "$AUTH" >/dev/null
  resource_exists container "$DB" && docker rm -f "$DB" >/dev/null
  resource_exists network "$NETWORK" && docker network rm "$NETWORK" >/dev/null
  if (( purge )); then
    if (( ! yes )); then
      [[ -t 0 ]] || fail "down --purge requires confirmation or --yes"
      read -r -p "Purge the owned database volume and runtime secrets? [y/N] " answer
      [[ "$answer" = y || "$answer" = Y ]] || fail "purge cancelled"
    fi
    guard_resource volume "$VOLUME"
    resource_exists volume "$VOLUME" && docker volume rm "$VOLUME" >/dev/null
    [[ ! -e "$STATE_DIR" ]] || purge_state
  fi
  if (( purge )); then info "lab stopped and purged"; else info "lab stopped; database volume preserved"; fi
}

main() {
  validate_settings
  local command="${1:-}" purge=0 yes=0; [[ -n "$command" ]] || { usage; exit 2; }; shift || true
  case "$command" in
    plan) (($# == 0)) || fail "plan takes no arguments"; cmd_plan ;;
    doctor) (($# == 0)) || fail "doctor takes no arguments"; cmd_doctor ;;
    up|status|seed|verify) (($# == 0)) || fail "$command takes no arguments"; "cmd_$command" ;;
    down)
      while (($#)); do case "$1" in --purge) purge=1;; --yes) yes=1;; *) fail "unknown down option: $1";; esac; shift; done
      (( yes == 0 || purge == 1 )) || fail "--yes requires --purge"; cmd_down "$purge" "$yes" ;;
    -h|--help|help) usage ;;
    *) fail "unknown command: $command" ;;
  esac
}
main "$@"
