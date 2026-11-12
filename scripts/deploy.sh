#!/usr/bin/env bash
set -euo pipefail

# Idempotent Spanel deployment. The encryption key is created once and is
# never rotated or overwritten by this script.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_DOCKER=${SPANEL_ENV_DOCKER:-"$ROOT/.env.docker"}
ENV_POWERDNS=${SPANEL_ENV_POWERDNS:-"$ROOT/.env.powerdns"}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "docker compose is required"
[[ -f "$ENV_DOCKER" ]] || fail "missing $ENV_DOCKER"
[[ -f "$ENV_POWERDNS" ]] || fail "missing $ENV_POWERDNS"

read_env_value() {
  python3 - "$1" "$2" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        print(value.strip().strip('"').strip("'"))
        break
PY
}

SPANEL_DOMAIN_VALUE=${SPANEL_DOMAIN:-$(read_env_value "$ENV_DOCKER" SPANEL_DOMAIN)}
if [[ -z "$SPANEL_DOMAIN_VALUE" ]]; then
  SPANEL_APPS_PUBLIC_URL_VALUE=${SPANEL_APPS_PUBLIC_URL:-$(
    read_env_value "$ENV_DOCKER" SPANEL_APPS_PUBLIC_URL
  )}
  [[ -n "$SPANEL_APPS_PUBLIC_URL_VALUE" ]] || fail \
    "SPANEL_DOMAIN or SPANEL_APPS_PUBLIC_URL is required in $ENV_DOCKER"
  SPANEL_DOMAIN_VALUE=$(python3 - "$SPANEL_APPS_PUBLIC_URL_VALUE" <<'PY'
import sys
from urllib.parse import urlsplit

value = sys.argv[1].rstrip("/")
parts = urlsplit(value)
if (
    parts.scheme != "https"
    or not parts.hostname
    or parts.username
    or parts.password
    or parts.path not in {"", "/"}
    or parts.query
    or parts.fragment
):
    raise SystemExit("ERROR: SPANEL_APPS_PUBLIC_URL must be an HTTPS origin")
print(parts.hostname)
PY
  )
fi
export SPANEL_DOMAIN=$SPANEL_DOMAIN_VALUE

KEY_PATH_VALUE=${SPANEL_APPS_ENCRYPTION_KEY_PATH:-$(
  read_env_value "$ENV_DOCKER" SPANEL_APPS_ENCRYPTION_KEY_PATH
)}
KEY_PATH_VALUE=${KEY_PATH_VALUE:-secrets/apps_key}
if [[ "$KEY_PATH_VALUE" = /* ]]; then
  KEY_PATH=$KEY_PATH_VALUE
else
  KEY_PATH="$ROOT/$KEY_PATH_VALUE"
fi

python3 - "$KEY_PATH" <<'PY'
import base64
import os
import secrets
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)

if path.is_symlink():
    raise SystemExit(f"ERROR: encryption key path must not be a symlink: {path}")

if not path.exists():
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(32)) + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        print(f"Created encryption key: {path}")

if not path.is_file():
    raise SystemExit(f"ERROR: encryption key is not a regular file: {path}")

mode = stat.S_IMODE(path.stat().st_mode)
if mode & 0o077:
    path.chmod(0o600)
    print(f"Restricted encryption key permissions to 600: {path}")

raw = path.read_bytes()
if len(raw) == 32:
    key = raw
else:
    try:
        text = raw.strip()
        key = base64.urlsafe_b64decode(text + b"=" * (-len(text) % 4))
    except Exception as exc:
        raise SystemExit(f"ERROR: invalid encryption key file: {path}") from exc
if len(key) != 32:
    raise SystemExit(f"ERROR: encryption key must resolve to exactly 32 bytes: {path}")
PY

echo "Deploying Spanel at https://$SPANEL_DOMAIN_VALUE"
cd "$ROOT"
exec docker compose \
  -f docker-compose.yml \
  -f docker-compose.powerdns.yml \
  --env-file "$ENV_DOCKER" \
  --env-file "$ENV_POWERDNS" \
  up -d --build "$@"
