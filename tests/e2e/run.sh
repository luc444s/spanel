#!/bin/bash
# SP-0020: escenarios 2, 3b, 4, 5 contra API 8001
set -euo pipefail

API="${SPANEL_API:-http://127.0.0.1:8001}"
ADMIN_EMAIL="${SPANEL_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASS="${SPANEL_ADMIN_PASSWORD:-ChangeMe123!}"

fail() { echo "FAIL [$1]: $2"; exit 1; }

if ! curl -s -m 3 "$API/api/v1/system/health" | grep -q '"status":"ok"'; then
  echo "API caida, reiniciando..."
  cd "$(dirname "$0")/../.."
  npm run services:stop >/dev/null 2>&1 || true
  sleep 2
  nohup npm run services:no-reload >/dev/null 2>&1 &
  sleep 10
fi

TOKEN=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

auth=(-H "Authorization: Bearer $TOKEN")

# [2] Spanel detecta el WP
echo "== [2] deteccion/adopt =="
curl -s "${auth[@]}" "$API/api/v1/plugins/docker_infra/containers?all_containers=true" \
  | python3 -c "import json,sys; names=[c['name'] for c in json.load(sys.stdin)]; sys.exit(0 if 'spanel-test-wp' in names else 1)" \
  || fail 2 "spanel-test-wp no descubierto"
echo "PASS [2] container descubierto"

SITE_ID=$(curl -s -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"container_name":"spanel-test-wp","name":"testwp"}' \
  "$API/api/v1/plugins/hosting/sites/adopt" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))")
if [ -z "$SITE_ID" ]; then
  SITE_ID=$(curl -s "${auth[@]}" "$API/api/v1/plugins/hosting/sites" \
    | python3 -c "import json,sys; print([s['id'] for s in json.load(sys.stdin) if s['container_name']=='spanel-test-wp'][0])")
fi
[ -n "$SITE_ID" ] || fail 2 "adopt fallo"
echo "PASS [2] adoptado stack=wordpress id=$SITE_ID"

# [3b] mail server detectado + correos de prueba
echo "== [3b] mail =="
curl -s "${auth[@]}" "$API/api/v1/plugins/docker_infra/containers?all_containers=true" \
  | python3 -c "import json,sys; names=[c['name'] for c in json.load(sys.stdin)]; sys.exit(0 if 'spanel-test-mail' in names else 1)" \
  || fail 3 "spanel-test-mail no descubierto"
echo "PASS [3b] mailserver externo detectado"

curl -s -X POST "${auth[@]}" "$API/api/v1/plugins/mail/server/ensure" >/dev/null || fail 3 "mail server spanel"
curl -s -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"domain":"spanel-test.local","user":"x"}' \
  "$API/api/v1/plugins/mail/domains" >/dev/null || fail 3 "dominio mail"
MB1=$(curl -s -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"domain":"spanel-test.local","user":"t1","password":"TestPass123"}' \
  "$API/api/v1/plugins/mail/mailboxes" | python3 -c "import json,sys; print(json.load(sys.stdin).get('email',''))")
MB2=$(curl -s -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"domain":"spanel-test.local","user":"t2","password":"TestPass123"}' \
  "$API/api/v1/plugins/mail/mailboxes" | python3 -c "import json,sys; print(json.load(sys.stdin).get('email',''))")
[ -n "$MB1" ] && [ -n "$MB2" ] || fail 3 "buzones no creados"

python3 - "$MB1" "$MB2" <<'PY'
import smtplib, sys
from email.mime.text import MIMEText
src, dst = sys.argv[1], sys.argv[2]
msg = MIMEText("prueba SP-0020")
msg["Subject"] = "Spanel e2e"
msg["From"] = src
msg["To"] = dst
try:
    with smtplib.SMTP("100.67.5.50", 25, timeout=15) as s:
        s.sendmail(src, [dst], msg.as_string())
except Exception as exc:
    print(f"SMTP fail: {exc}")
    sys.exit(1)
print("mail enviado OK")
PY
[ $? -eq 0 ] || fail 3 "envio smtp"
echo "PASS [3b] mail operativo"

# [4] SSO sin credenciales
echo "== [4] SSO =="
curl -s -X PATCH "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"admin_email":"admin@example.com"}' \
  "$API/api/v1/plugins/hosting/sites/$SITE_ID" >/dev/null
curl -s -X POST "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"fqdn":"test.spanel.ts.net"}' \
  "$API/api/v1/plugins/proxy/domains" >/dev/null
SSO_URL=$(curl -s -X POST "${auth[@]}" "$API/api/v1/plugins/hosting/sites/$SITE_ID/sso" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('url','') or '')")
[ -n "$SSO_URL" ] || fail 4 "sso no genero url: $(curl -s -X POST "${auth[@]}" "$API/api/v1/plugins/hosting/sites/$SITE_ID/sso" 2>&1)"
echo "PASS [4] magic link generado (validacion completa requiere dominio SP-0011)"

# [5] buzones desde panel
echo "== [5] buzones =="
COUNT=$(curl -s "${auth[@]}" "$API/api/v1/plugins/mail/mailboxes" \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
[ "$COUNT" -ge 2 ] || fail 5 "mailboxes no listados"
echo "PASS [5] $COUNT buzones via Spanel"

echo "== SP-0020: 5 escenarios OK =="
