#!/usr/bin/env bash
# A.SPEC 0067 real proof: operator CLI against the private A.SPEC 0066 lab.
# Credentials arrive only through POWERDNS_LAB_SSH_* and are never printed.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${POWERDNS_LAB_SSH_HOST:-}"
PORT="${POWERDNS_LAB_SSH_PORT:-}"
USER="${POWERDNS_LAB_SSH_USER:-}"
PASSWORD="${POWERDNS_LAB_SSH_PASSWORD:-}"
LAB_PORT="${POWERDNS_LAB_CLI_PORT:-5300}"
EVIDENCE_DIR="${POWERDNS_CLI_EVIDENCE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-cli-live}"
EVIDENCE="$EVIDENCE_DIR/latest.log"
REMOTE_DIR=""
LOCAL_TMP=""
CONTAINER="spanel-powerdns-lab-auth"
CLI_DIR="/tmp/spanel-cli-proof"
ZONE="spanel-cli-$(date +%s)-$$.test"
FIRST_NS="ns1.${ZONE}"
SECOND_NS="ns2.${ZONE}"

fail() { echo "FAIL: $*" >&2; exit 1; }
for value in HOST PORT USER PASSWORD; do
  [[ -n "${!value}" ]] || fail "POWERDNS_LAB_SSH_${value} is required"
done
command -v sshpass >/dev/null 2>&1 || fail "sshpass is required"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "POWERDNS_LAB_SSH_PORT must be numeric"
[[ "$LAB_PORT" =~ ^[0-9]+$ ]] || fail "POWERDNS_LAB_CLI_PORT must be numeric"

mkdir -p "$EVIDENCE_DIR"
chmod 700 "$EVIDENCE_DIR"
: > "$EVIDENCE"
chmod 600 "$EVIDENCE"
KNOWN_HOSTS="$EVIDENCE_DIR/known_hosts.tmp"
: > "$KNOWN_HOSTS"
chmod 600 "$KNOWN_HOSTS"
LOCAL_TMP="$(mktemp -d "${TMPDIR:-/tmp}/spanel-cli-local.XXXXXXXX")"

ssh_options=(
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$KNOWN_HOSTS"
  -o IdentitiesOnly=yes
  -o PreferredAuthentications=password
  -o PubkeyAuthentication=no
)
ssh_cmd=(sshpass -e ssh -p "$PORT" "${ssh_options[@]}" -- "$USER@$HOST")
remote() { SSHPASS="$PASSWORD" "${ssh_cmd[@]}" "$@"; }
record() { printf 'PASS %s\n' "$1" | tee -a "$EVIDENCE"; }

cleanup() {
  local rc=$?
  if [[ "$REMOTE_DIR" == /tmp/spanel-cli-proof.* ]]; then
    remote "docker exec '$CONTAINER' rm -rf -- '$CLI_DIR' >/dev/null 2>&1 || true
      if test -x '$REMOTE_DIR/scripts/powerdns-lab.sh'; then
        POWERDNS_LAB_STATE_DIR='$REMOTE_DIR/state' POWERDNS_LAB_DNS_PORT='$LAB_PORT' \
          '$REMOTE_DIR/scripts/powerdns-lab.sh' down --purge --yes >/dev/null 2>&1 || true
      fi
      rm -rf -- '$REMOTE_DIR'" >/dev/null 2>&1 || true
  fi
  rm -f -- "$KNOWN_HOSTS"
  if [[ "$LOCAL_TMP" == */spanel-cli-local.* ]]; then
    rm -rf -- "$LOCAL_TMP"
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

lab() {
  remote "cd '$REMOTE_DIR' && POWERDNS_LAB_STATE_DIR='$REMOTE_DIR/state' POWERDNS_LAB_DNS_PORT='$LAB_PORT' scripts/powerdns-lab.sh $*"
}
cli() {
  remote "docker exec \
    -e SPANEL_PDNS_API_URL=http://127.0.0.1:8081 \
    -e SPANEL_PDNS_API_KEY_FILE='$CLI_DIR/api-key' \
    -e SPANEL_PDNS_SERVER_ID=localhost \
    -e SPANEL_PDNS_TIMEOUT_SECONDS=5 \
    '$CONTAINER' python3 '$CLI_DIR/scripts/spanel_dns.py' $*"
}
zone_version() {
  cli "zones show '$ZONE' --json" \
    | sed -n 's/.*"version": "\([^"]*\)".*/\1/p' | head -n 1
}
cli_expect() {
  local expected="$1" command="$2" rc=0
  cli "$command" >/dev/null 2>&1 || rc=$?
  [[ "$rc" -eq "$expected" ]] || fail "CLI returned $rc, expected $expected: $command"
}
check_dig() {
  local transport="$1" type="$2" name="$3" expected="$4" output
  local args=(+norecurse +comments +answer)
  [[ "$transport" = TCP ]] && args+=(+tcp)
  output="$(remote "dig @127.0.0.1 -p '$LAB_PORT' ${args[*]} '$name' '$type'")"
  grep -Eq 'flags:.* aa[ ;]' <<<"$output" || fail "$transport $type answer is not authoritative"
  grep -Fq -- "$expected" <<<"$output" || fail "$transport $type answer lacks expected data"
  echo "PASS $transport $type $name"
}

REMOTE_DIR="$(remote "mktemp -d /tmp/spanel-cli-proof.XXXXXXXX")"
[[ "$REMOTE_DIR" == /tmp/spanel-cli-proof.* ]] || fail "remote mktemp returned an unsafe path"
remote "mkdir -p '$REMOTE_DIR/scripts/powerdns-lab' '$REMOTE_DIR/vendor/systutor-core/plugins/domains/backend/dns' '$REMOTE_DIR/scripts/tests'"
tar -C "$ROOT" -czf - \
  scripts/powerdns-lab.sh \
  scripts/powerdns-lab/compose.yml \
  scripts/powerdns-lab/init/001-schema.sql \
  scripts/powerdns-lab/README.md \
  scripts/spanel_dns.py \
  vendor/systutor-core/plugins/domains/backend/__init__.py \
  vendor/systutor-core/plugins/domains/backend/dns \
  | remote "tar -xzf - -C '$REMOTE_DIR'"
remote "chmod 755 '$REMOTE_DIR/scripts/powerdns-lab.sh'"
record "transferred only CLI, DNS package and lab files to a unique remote directory"

lab "plan" >/dev/null
lab "doctor" >/dev/null
lab "up"
record "fresh A.SPEC 0066 lab is healthy"

remote "docker cp '$REMOTE_DIR' '$CONTAINER:$CLI_DIR'"
remote "docker exec '$CONTAINER' sh -c 'install -m 600 /run/secrets/pdns_api_key $CLI_DIR/api-key'"
record "CLI runs inside the private lab network with an owner-only key copy"

cli "doctor --json" >/dev/null
record "CLI doctor: authenticated PowerDNS health and private key file"

cli_expect 3 "zones create '$ZONE' --ns1 '$FIRST_NS' --ns2 '$SECOND_NS' --ns1-ipv4 192.0.2.53 --ns2-ipv4 192.0.2.53 --json"
cli "zones create '$ZONE' --ns1 '$FIRST_NS' --ns2 '$SECOND_NS' --ns1-ipv4 192.0.2.53 --ns2-ipv4 192.0.2.53 --apply --json" >/dev/null
record "zone create is a plan by default and applies only with --apply"

cli "zones list --json" | grep -Fq "\"$ZONE\"" || fail "created zone is not listed"
cli "zones show '$ZONE' --json" >/dev/null
record "zone list and show read the created zone"

version="$(zone_version)"
[[ -n "$version" ]] || fail "could not obtain the zone version"
cli_expect 3 "rrsets plan '$ZONE' --name www --type A --ttl 300 --value 192.0.2.80 --json"
cli "rrsets apply '$ZONE' --name www --type A --ttl 300 --value 192.0.2.80 --expected-version '$version' --apply --json" >/dev/null
record "A RRset planned and applied with the current version"

cli_expect 4 "rrsets apply '$ZONE' --name www --type A --ttl 300 --value 192.0.2.81 --expected-version '$version' --apply --json"
record "stale version is rejected with conflict and no mutation"

version="$(zone_version)"
cli_expect 3 "rrsets plan '$ZONE' --name @ --type TXT --ttl 300 --value spanel-cli-proof --json"
cli "rrsets apply '$ZONE' --name @ --type TXT --ttl 300 --value spanel-cli-proof --expected-version '$version' --apply --json" >/dev/null
record "TXT RRset planned and applied as one complete RRset"

for transport in UDP TCP; do
  check_dig "$transport" SOA "$ZONE" "$FIRST_NS"
  check_dig "$transport" NS "$ZONE" "$SECOND_NS"
  check_dig "$transport" A "www.$ZONE" "192.0.2.80"
  check_dig "$transport" TXT "$ZONE" "spanel-cli-proof"
done
record "real UDP and TCP SOA/NS/A/TXT answers are authoritative"

cli "zone export '$ZONE'" > "$LOCAL_TMP/export.zone"
remote "cat > '$REMOTE_DIR/export.zone'" < "$LOCAL_TMP/export.zone"
remote "docker cp '$REMOTE_DIR/export.zone' '$CONTAINER:$CLI_DIR/export.zone'"
cli "zone export '$ZONE'" > "$LOCAL_TMP/export-first.zone"
cmp -s "$LOCAL_TMP/export.zone" "$LOCAL_TMP/export-first.zone" || fail "zone export is not deterministic"
record "zone export is deterministic"

version="$(zone_version)"
cli "rrsets delete '$ZONE' --name www --type A --expected-version '$version' --confirm-name 'www.$ZONE' --apply --json" >/dev/null
version="$(zone_version)"
cli "rrsets delete '$ZONE' --name @ --type TXT --expected-version '$version' --confirm-name '$ZONE' --apply --json" >/dev/null
record "A and TXT RRsets deleted with exact confirmation"

version="$(zone_version)"
cli "zones delete '$ZONE' --expected-version '$version' --confirm-zone '$ZONE' --apply --json" >/dev/null
record "zone deleted with exact confirmation"

cli "zone import '$ZONE' --file '$CLI_DIR/export.zone' --apply --json" >/dev/null
cli "zone export '$ZONE'" > "$LOCAL_TMP/export-second.zone"
if ! cmp -s "$LOCAL_TMP/export.zone" "$LOCAL_TMP/export-second.zone"; then
  diff -u "$LOCAL_TMP/export.zone" "$LOCAL_TMP/export-second.zone" >&2 || true
  fail "re-import did not restore the same zone"
fi
record "zone deleted and re-imported deterministically"

for transport in UDP TCP; do
  check_dig "$transport" SOA "$ZONE" "$FIRST_NS"
  check_dig "$transport" A "www.$ZONE" "192.0.2.80"
  check_dig "$transport" TXT "$ZONE" "spanel-cli-proof"
done
record "re-imported zone answers over UDP and TCP"

remote "dbports=\$(docker inspect -f '{{json .HostConfig.PortBindings}}' spanel-powerdns-lab-postgres); test \"\$dbports\" = null || test \"\$dbports\" = '{}'; ports=\$(docker inspect -f '{{json .HostConfig.PortBindings}}' '$CONTAINER'); case \"\$ports\" in *8081*|*5432*) exit 1;; esac"
record "no API or database host port was added"

version="$(zone_version)"
cli "zones delete '$ZONE' --expected-version '$version' --confirm-zone '$ZONE' --apply --json" >/dev/null
lab "down --purge --yes" >/dev/null
if remote "docker exec '$CONTAINER' true" >/dev/null 2>&1; then
  fail "lab container survived purge"
fi
remote "! docker inspect '$CONTAINER' >/dev/null 2>&1"
record "final zone and only the labelled lab were purged"

remote "rm -rf -- '$REMOTE_DIR'"
remote "test ! -e '$REMOTE_DIR'"
REMOTE_DIR=""
rm -rf -- "$LOCAL_TMP"
LOCAL_TMP=""
record "unique remote and local temporary directories removed"

echo "Redacted evidence: $EVIDENCE"
