#!/usr/bin/env bash
# Destructive proof, bounded to a unique remote directory and exact lab names.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${POWERDNS_LAB_SSH_HOST:-}"
PORT="${POWERDNS_LAB_SSH_PORT:-}"
USER="${POWERDNS_LAB_SSH_USER:-}"
PASSWORD="${POWERDNS_LAB_SSH_PASSWORD:-}"
LAB_PORT="${POWERDNS_LAB_LIVE_PORT:-5300}"
EVIDENCE_DIR="${POWERDNS_LAB_EVIDENCE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-lab-live}"
EVIDENCE="$EVIDENCE_DIR/latest.log"
REMOTE_DIR=""

fail() { echo "FAIL: $*" >&2; exit 1; }
for value in HOST PORT USER PASSWORD; do [[ -n "${!value}" ]] || fail "POWERDNS_LAB_SSH_${value} is required"; done
command -v sshpass >/dev/null 2>&1 || fail "sshpass is required"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "POWERDNS_LAB_SSH_PORT must be numeric"
[[ "$LAB_PORT" =~ ^[0-9]+$ ]] || fail "POWERDNS_LAB_LIVE_PORT must be numeric"

mkdir -p "$EVIDENCE_DIR"
chmod 700 "$EVIDENCE_DIR"
: > "$EVIDENCE"
chmod 600 "$EVIDENCE"
KNOWN_HOSTS="$EVIDENCE_DIR/known_hosts.tmp"
: > "$KNOWN_HOSTS"; chmod 600 "$KNOWN_HOSTS"

ssh_options=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN_HOSTS" -o IdentitiesOnly=yes -o PreferredAuthentications=password -o PubkeyAuthentication=no)
ssh_cmd=(sshpass -e ssh -p "$PORT" "${ssh_options[@]}" -- "$USER@$HOST")
scp_cmd=(sshpass -e scp -P "$PORT" "${ssh_options[@]}")
remote() { SSHPASS="$PASSWORD" "${ssh_cmd[@]}" "$@"; }
record() { printf 'PASS %s\n' "$1" | tee -a "$EVIDENCE"; }
diagnose_lab_failure() {
  echo "Bounded lab diagnostics (secret values redacted):" >&2
  remote "for name in spanel-powerdns-lab-postgres spanel-powerdns-lab-auth; do if docker inspect \"\$name\" >/dev/null 2>&1; then echo \"--- \$name health ---\"; docker inspect -f '{{if .State.Health}}status={{.State.Health.Status}}{{range .State.Health.Log}} exit={{.ExitCode}} output={{printf \"%.300s\" .Output}}{{end}}{{else}}status={{.State.Status}}{{end}}' \"\$name\"; echo \"--- \$name logs (last 80 lines) ---\"; docker logs --tail 80 \"\$name\" 2>&1; fi; done" \
    | sed -E 's/((password|api[-_ ]?key)[=: ]+)[^[:space:]]+/\1<redacted>/Ig' >&2 || true
}
up_or_diagnose() {
  if ! remote "$lab up"; then
    diagnose_lab_failure
    fail "lab up failed; cleanup remains armed"
  fi
}

cleanup() {
  local rc=$?
  if [[ "$REMOTE_DIR" == /tmp/spanel-powerdns-lab.* ]]; then
    remote "if test -x '$REMOTE_DIR/scripts/powerdns-lab.sh'; then POWERDNS_LAB_STATE_DIR='$REMOTE_DIR/state' POWERDNS_LAB_DNS_PORT='$LAB_PORT' '$REMOTE_DIR/scripts/powerdns-lab.sh' down --purge --yes >/dev/null 2>&1 || true; fi; rm -rf -- '$REMOTE_DIR'" >/dev/null 2>&1 || true
  fi
  rm -f -- "$KNOWN_HOSTS"
  exit "$rc"
}
trap cleanup EXIT INT TERM

REMOTE_DIR="$(remote "mktemp -d /tmp/spanel-powerdns-lab.XXXXXXXX")"
[[ "$REMOTE_DIR" == /tmp/spanel-powerdns-lab.* ]] || fail "remote mktemp returned an unsafe path"
remote "mkdir -p '$REMOTE_DIR/scripts/powerdns-lab/init' '$REMOTE_DIR/scripts/tests'"
SSHPASS="$PASSWORD" "${scp_cmd[@]}" \
  "$ROOT/scripts/powerdns-lab.sh" \
  "$ROOT/scripts/powerdns-lab/compose.yml" \
  "$ROOT/scripts/powerdns-lab/init/001-schema.sql" \
  "$ROOT/scripts/powerdns-lab/README.md" \
  "$USER@$HOST:$REMOTE_DIR/" >/dev/null
remote "mv '$REMOTE_DIR/powerdns-lab.sh' '$REMOTE_DIR/scripts/powerdns-lab.sh'; mv '$REMOTE_DIR/compose.yml' '$REMOTE_DIR/scripts/powerdns-lab/compose.yml'; mv '$REMOTE_DIR/001-schema.sql' '$REMOTE_DIR/scripts/powerdns-lab/init/001-schema.sql'; mv '$REMOTE_DIR/README.md' '$REMOTE_DIR/scripts/powerdns-lab/README.md'; chmod 755 '$REMOTE_DIR/scripts/powerdns-lab.sh'; chmod 644 '$REMOTE_DIR/scripts/powerdns-lab/compose.yml' '$REMOTE_DIR/scripts/powerdns-lab/init/001-schema.sql' '$REMOTE_DIR/scripts/powerdns-lab/README.md'"
record "transferred only lab files to unique remote directory"

lab="cd '$REMOTE_DIR' && POWERDNS_LAB_STATE_DIR='$REMOTE_DIR/state' POWERDNS_LAB_DNS_PORT='$LAB_PORT' scripts/powerdns-lab.sh"
remote "$lab plan"
record "plan is read-only"
remote "$lab doctor"
record "doctor reports TCP/UDP port 53"

fixture="spanel-pdns-port-fixture-$$"
remote "docker run -d --name '$fixture' -p '127.0.0.1:$LAB_PORT:5432/tcp' -p '127.0.0.1:$LAB_PORT:5432/udp' --entrypoint sleep postgres:16.10-bookworm@sha256:38471f330eb885e04de130b768d6db4e10469e2311879c7e5c699f6d2d8a1c74 120 >/dev/null"
if remote "$lab up" >/dev/null 2>&1; then fail "up accepted a conflicting TCP/UDP listener"; fi
remote "docker inspect '$fixture' >/dev/null && docker rm -f '$fixture' >/dev/null"
record "conflicting TCP/UDP owner remains present and up makes no lab"

remote "docker network create spanel-powerdns-lab-net >/dev/null"
if remote "$lab up" >/dev/null 2>&1; then fail "up adopted a foreign same-name network"; fi
remote "docker network inspect spanel-powerdns-lab-net >/dev/null && docker network rm spanel-powerdns-lab-net >/dev/null"
record "foreign same-name resource is refused and preserved"

up_or_diagnose
remote "$lab status"
up_or_diagnose
record "up/status/second up are healthy and idempotent"
remote "$lab seed"
remote "$lab seed"
record "seed is idempotent"
remote "$lab verify"
record "UDP/TCP SOA/NS/A and private API verified"

remote "dbports=\$(docker inspect -f '{{json .HostConfig.PortBindings}}' spanel-powerdns-lab-postgres); test \"\$dbports\" = null || test \"\$dbports\" = '{}'; ports=\$(docker inspect -f '{{json .HostConfig.PortBindings}}' spanel-powerdns-lab-auth); case \"\$ports\" in *8081*|*5432*) exit 1;; esac"
record "PostgreSQL and API have no published host ports"

remote "$lab down"
up_or_diagnose
remote "$lab verify"
record "normal down preserves seeded PostgreSQL state"
remote "$lab down --purge --yes"
remote "! docker inspect spanel-powerdns-lab-auth >/dev/null 2>&1; ! docker inspect spanel-powerdns-lab-postgres >/dev/null 2>&1; ! docker network inspect spanel-powerdns-lab-net >/dev/null 2>&1; ! docker volume inspect spanel-powerdns-lab-postgres-data >/dev/null 2>&1; test ! -e '$REMOTE_DIR/state'"
record "purge removed only exact labelled lab resources and state"

remote "rm -rf -- '$REMOTE_DIR'"
remote "test ! -e '$REMOTE_DIR'"
REMOTE_DIR=""
record "unique remote temporary directory removed"
echo "Redacted evidence: $EVIDENCE"
