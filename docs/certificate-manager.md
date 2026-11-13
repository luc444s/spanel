# Mail TLS certificate manager

Implements A.SPEC 0035 and 0036: mail certificate inventory, API in
the `mail` plugin, **TLS Certificates** modal in Mail, on-demand issuance
through a privileged agent, continuous verification, alerts, transactional
installation and rollback.

Certbot remains the owner of issuance and renewal. Spanel does **not**
implement ACME, does not store private keys, and does not recreate Docker Mailserver.

English | [Español](certificate-manager.es.md)

## Division of responsibilities

| Component | Can | Cannot |
|---|---|---|
| Web / `domains` API | read `state.json`, write `requests/`, read `jobs/` | read `privkey.pem`, run Certbot, touch Docker |
| `spanel-cert-agent` (root) | validate, run Certbot, install the pair, generate SNI, reload | accept flags, paths or environment from the request |
| Host Certbot | issue and renew | only invoked with a fixed argv |

`cf-mail` remains the global default certificate. In this phase it is not
regenerated or reinstalled from the panel.

## Paths

| Path | Owner / mode | Use |
|---|---|---|
| `/var/lib/spanel/cert-manager/state.json` | `root:spanel-cert` `0640` | Inventory, metadata and paths only |
| `/var/lib/spanel/cert-manager/requests/` | `spanel:spanel-cert` `0770` | The web writes; the agent reads and deletes |
| `/var/lib/spanel/cert-manager/jobs/` | `root:spanel-cert` `0750` | Result of each request |
| `/etc/spanel/cert-agent.toml` | `root:root` `0600` | Allowlist, Certbot and allowed commands |
| `/etc/ssl/spanel/mail/<hostname>/fullchain.pem` | `root:root` `0644` | Installed chain |
| `/etc/ssl/spanel/mail/<hostname>/privkey.pem` | `root:root` `0600` | Installed key |

The web only writes `requests/`. The agent publishes `state.json` and `jobs/` without
key content. A `state.json` containing `PRIVATE KEY` is rejected in its
entirety and the API responds `503`.

## Agent configuration

```toml
# /etc/spanel/cert-agent.toml
allowlist = ["mail.systutor.com", "mail.legalattorney.pe"]
default_certificate = "cf-mail"
default_cert_path = "/etc/ssl/cf-mail.fullchain.crt"
default_key_path = "/etc/ssl/cf-mail.key"
install_root = "/etc/ssl/spanel/mail"
state_path = "/var/lib/spanel/cert-manager/state.json"
alerts_path = "/var/lib/spanel/cert-manager/alerts.json"
requests_dir = "/var/lib/spanel/cert-manager/requests"
jobs_dir = "/var/lib/spanel/cert-manager/jobs"
generations_root = "/var/lib/spanel/cert-manager/generations"
backup_root = "/var/lib/spanel/cert-manager/backups"
locks_dir = "/var/lib/spanel/cert-manager/locks"
renew_days = 30

[verification]
ports = [993, 465, 587]
ca_file = "/etc/ssl/certs/ca-certificates.crt"
timeout_seconds = 10
audit_interval_seconds = 600

[alerts]
warning_days = 21
critical_days = 7
# External channel, independent of the monitored mail service. Choose one:
webhook_command = ["/usr/local/bin/spanel-alert-webhook"]
# webhook_url = "https://alertas.example.com/spanel"
timeout_seconds = 10

[certbot]
executable = "/usr/bin/certbot"
challenge = "webroot"          # auto | webroot | dns-01
webroot_path = "/var/www/html"
# dns_plugin = "cloudflare"    # only with challenge = "dns-01"
lineage_root = "/etc/letsencrypt/live"

[postfix]
sni_map_path = "/etc/postfix/sni_map"
postmap_command = ["postmap", "/etc/postfix/sni_map"]
reload_command = ["postfix", "reload"]

[dovecot]
local_name_path = "/etc/dovecot/conf.d/99-spanel-local_name.conf"
reload_command = ["dovecot", "reload"]
```

Equivalent environment variables (they take precedence over the TOML):

`SPANEL_CERT_AGENT_CONFIG`, `SPANEL_CERT_ALLOWLIST`, `SPANEL_CERT_STATE_PATH`,
`SPANEL_CERT_REQUESTS_DIR`, `SPANEL_CERT_JOBS_DIR`, `SPANEL_CERT_INSTALL_ROOT`,
`SPANEL_CERT_DEFAULT_NAME`, `SPANEL_CERT_DEFAULT_CERT_PATH`,
`SPANEL_CERT_DEFAULT_KEY_PATH`, `SPANEL_CERT_RENEW_DAYS`,
`SPANEL_CERTBOT_EXECUTABLE`, `SPANEL_CERTBOT_CHALLENGE`,
`SPANEL_CERTBOT_WEBROOT`, `SPANEL_CERTBOT_DNS_PLUGIN`,
`SPANEL_CERTBOT_LINEAGE_ROOT`, `SPANEL_CERTBOT_CA_FILE`,
`SPANEL_POSTFIX_SNI_MAP`, `SPANEL_POSTFIX_POSTMAP_CMD`,
`SPANEL_POSTFIX_RELOAD_CMD`, `SPANEL_DOVECOT_LOCAL_NAME`,
`SPANEL_DOVECOT_RELOAD_CMD`, `SPANEL_CERT_ALERTS_PATH`,
`SPANEL_CERT_GENERATIONS_ROOT`, `SPANEL_CERT_BACKUP_ROOT`,
`SPANEL_CERT_LOCKS_DIR`, `SPANEL_CERT_PROBE_PORTS`,
`SPANEL_CERT_VERIFY_CA_FILE`, `SPANEL_CERT_PROBE_TIMEOUT`,
`SPANEL_CERT_AUDIT_INTERVAL`, `SPANEL_CERT_ALERT_WARN_DAYS`,
`SPANEL_CERT_ALERT_CRITICAL_DAYS`, `SPANEL_CERT_ALERT_WEBHOOK_CMD`,
`SPANEL_CERT_ALERT_WEBHOOK_URL`, `SPANEL_CERT_ALERT_TIMEOUT`.

Without `sni_map_path`, `postmap_command`, `local_name_path` or the `reload_command`
entries, the agent **fails closed before running Certbot**. It does not invent paths or
commands.

The web plugin reads the state/requests/jobs paths from
`SPANEL_CERT_STATE_PATH`, `SPANEL_CERT_REQUESTS_DIR`, `SPANEL_CERT_JOBS_DIR`,
`SPANEL_CERT_DEFAULT_NAME`, `SPANEL_CERT_RENEW_DAYS` and
`SPANEL_CERT_STATE_MAX_AGE`. The default maximum age is 900 s; past
that threshold the inventory is marked `stale` and the UI shows "Status unavailable".

## Adding a new domain (0041)

To request the TLS certificate for a domain that the panel does not list yet:

1. Add the hostname to the agent allowlist **without rewriting the rest of the
   TOML** (idempotent; preserves comments, paths, `[postfix]` and `[dovecot]`;
   fails if the configuration does not exist, and in `--dry-run` does not write):

   ```bash
   python3 scripts/install_cert_agent.py --add-allowlist mail.nuevo.com
   ```

2. Refresh the agent state so it publishes the added hostname:

   ```bash
   spanel-cert-agent refresh
   ```

3. Open **Mail → TLS Certificates → Add certificate**, enter the
   hostname in the form `mail.<domain>` and request it. The action appears only
   with `mail.certificates.manage`; a tenant can only request
   `mail.<their-primary-domain>` and any other hostname responds `404`.
   The platform administrator (`core.platform.admin`) can request
   any valid `mail.*`.

The agent allowlist is the **final authority**: if the hostname is not in
`/etc/spanel/cert-agent.toml`, the agent fails the job and the UI shows the error.
The certificate is only associated with a tenant if `<domain>` is its primary
domain; the request can already be queued and polled (`/jobs/{job_id}`) with
`202`/`200` before the hostname appears in `state.json`.

## Installation

```bash
# Code (example)
install -d /opt/spanel/scripts
cp -a scripts/spanel_cert_agent /opt/spanel/scripts/
install -d /opt/spanel/docs
cp docs/certificate-manager.md /opt/spanel/docs/

# Configuration and directories
install -d -m 0750 -o root -g spanel-cert /var/lib/spanel/cert-manager/jobs
install -d -m 0770 -o spanel -g spanel-cert /var/lib/spanel/cert-manager/requests
install -d -m 0755 /etc/ssl/spanel/mail
install -m 0600 -o root -g root cert-agent.toml /etc/spanel/cert-agent.toml

# Units
install -m 0644 scripts/systemd/spanel-cert-agent.service /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-agent.path /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-agent.timer /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-audit.service /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-audit.timer /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-deploy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now spanel-cert-agent.path
systemctl enable --now spanel-cert-agent.timer
systemctl enable --now spanel-cert-audit.timer

# Certbot deploy hook (A.SPEC 0036)
install -m 0755 scripts/spanel_cert_agent/certbot-deploy-hook.sh \
  /etc/letsencrypt/renewal-hooks/deploy/spanel-cert-sync
```

`certbot.timer` remains the only renewal scheduler. The deploy
hook does **not** run `certbot renew` or `certbot certonly`: it only derives the
hostname from `RENEWED_LINEAGE` and calls `spanel-cert-agent sync --hostname`.
`spanel-cert-deploy.service` is the oneshot fallback that syncs all
lineages whose fingerprint changed.

### Docker deployment

The idempotent installer prepares everything on the host:

```bash
python3 scripts/install_cert_agent.py --dry-run
python3 scripts/install_cert_agent.py
```

- Detects Certbot's `mail.*` lineages (or uses `--allowlist`).
- Copies existing certificates to `/etc/ssl/spanel/mail/<hostname>/`.
- Writes `/etc/spanel/cert-agent.toml` without `[postfix]` or `[dovecot]`, so
  issuance fails closed and does not touch the SNI map.
- Installs the systemd units, publishes `state.json` and enables the timers.
- `--force-config` rewrites the configuration and `--refresh-certs` copies the
  certificates again.

To check whether the agent is installed, enabled and publishing recent
state:

```bash
python3 scripts/install_cert_agent.py --check
python3 scripts/install_cert_agent.py --check --json
```

Exits with `0` if configuration, units, timers and `state.json` are fine, and
with `1` if anything is missing. The check writes nothing.

The app reads the state and writes requests in `/var/lib/spanel/cert-manager`.
`docker-compose.yml` mounts that directory in the `app` container; without the
mount the API responds `state_missing` and the UI shows "Status unavailable".
The container runs as root, so there is no need to create the `spanel` user
on the host: the agent's directories and permissions are enough.

The agent runs on the host because it needs Certbot, writes to
`/etc/ssl/spanel/mail`, and reloads Postfix/Dovecot. With Docker Mailserver, the
reload is done with `docker exec` and the SNI map paths must be the ones the
container already mounts (`/tmp/docker-mailserver/...`). If the SNI map or the
`local_name` is not configured, the agent fails closed and does not issue.

### Docker Mailserver adapter

`scripts/configure_cert_agent_dms.py` wires the agent to an existing Docker
Mailserver without overwriting hand-made entries:

```bash
sudo python3 scripts/configure_cert_agent_dms.py            # read-only plan
sudo python3 scripts/configure_cert_agent_dms.py --json
sudo python3 scripts/configure_cert_agent_dms.py --apply    # write + recreate
```

It inspects the container to find the `/tmp/docker-mailserver` mount, reads the
allowlist from `/etc/spanel/cert-agent.toml` and splits the current SNI state:

- hostnames in the allowlist move to `postfix/sni-spanel` and
  `spanel-local_name.conf`, both owned by the agent from then on;
- the remaining hostnames stay in the original `tls_server_sni_maps` file and
  `dovecot.cf`, which keep working untouched;
- `postfix-main.cf` is updated to load both maps, and `dovecot.cf` gets an
  `!include_try` for the managed block;
- the `[postfix]` and `[dovecot]` sections are written into the agent config
  using `docker exec <container> ...`.

`--apply` backs up every file it touches with a `.spanel-dms-<timestamp>.bak`
suffix, runs `postmap`, then recreates the service with
`docker compose up -d --force-recreate` (detected from the container labels). A
plain restart is not enough: Docker Mailserver generates
`/etc/postfix/main.cf` only when the container is created, so the new
`tls_server_sni_maps` is ignored otherwise. It then verifies that every
hostname is still present in `postconf`/`doveconf`, and rolls the files back
(including the generated `.db`) if validation fails. Without `--apply` the script only prints the plan. It is idempotent: a
second run reports "no changes required".

### Manual check

```bash
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent refresh \
  --config /etc/spanel/cert-agent.toml
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent status \
  --config /etc/spanel/cert-agent.toml --json
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent verify \
  --config /etc/spanel/cert-agent.toml --json
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent audit \
  --config /etc/spanel/cert-agent.toml --json
```

`run-once` is the unit that systemd runs. It processes all pending
requests, writes a job for each one and refreshes `state.json`.

## Issuance behavior

| Action | Certbot |
|---|---|
| `ensure` (existing lineage) | `certbot renew --cert-name <hostname>` |
| `ensure` (no lineage) | `certbot certonly --cert-name <hostname> -d <hostname>` |
| `force` | `certbot certonly --force-renewal --cert-name <hostname> -d <hostname>` |

The challenge method comes from the configuration (`auto`, `webroot`, `dns-01`), not
from the request. The agent rejects extra fields, any flag
(`--pre-hook`, `--post-hook`, `--deploy-hook`, `--force-renewal` in `ensure`),
paths and environment variables. It only runs Certbot for hostnames in the
allowlist.

Before installing, it validates:

- that the private key matches the certificate;
- that the SAN contains the hostname;
- that the dates are valid and it is not expired;
- the chain if `ca_file` is configured.

Only then does it copy `fullchain.pem` (`0644`) and `privkey.pem` (`0600`) to
`/etc/ssl/spanel/mail/<hostname>/`, regenerate the Postfix SNI map, the Dovecot
`local_name` block and run the configured reloads. The Docker Mailserver
container is not recreated.

`cf-mail` has no installation path in the agent: it is read from
`default_cert_path`/`default_key_path` and published as `scope="global"`,
`deployment="default"`. No SNI certificate replaces it for connections without
SNI.

## Continuous verification (0036)

`spanel-cert-audit.timer` runs `spanel-cert-agent audit` every
`verification.audit_interval_seconds` (10 min by default). The audit:

- validates key/certificate, chain, SAN, dates and hash of each installed lineage;
- connects to 993/465/587 with SNI (`server_hostname=<hostname>`,
  `check_hostname=True`, configured CA) and checks the SAN and serial/fingerprint
  against the inventory;
- connects **without SNI** to the same ports and requires the serial to be that of
  `cf-mail`; if an SNI certificate responds, it is a critical failure;
- writes `health` to `state.json` and `alerts.json`, without touching certificates or
  reloading Postfix/Dovecot.

`verify` runs the same probes without writing state. Verification is
considered failed if any port, the chain, the SAN, the key or the
validity fails.

Health status (metadata only):

```json
{
  "generated_at": "2026-10-27T12:00:00Z",
  "agent": {"state": "ok", "last_run_at": "...", "interval_seconds": 600},
  "default": {"name": "cf-mail", "serial": "...", "no_sni_serial": "...", "ok": true},
  "certificates": [
    {
      "hostname": "mail.systutor.com",
      "serial": "...", "served_serial": "...", "no_sni_serial": "...",
      "days_left": 62, "sni": {"993": true, "465": true, "587": true},
      "no_sni": true, "ok": true,
      "last_verified_at": "...", "last_ok_at": "...",
      "consecutive_failures": 0, "last_error": null
    }
  ]
}
```

`consecutive_failures` is incremented per hostname and returns to 0 when it recovers
`ok`. A `generated_at` greater than `2 × audit_interval_seconds` is considered
old and alerts. The state never contains keys, tokens or PEM.

## Alerts

| Condition | Severity |
|---|---|
| `days_left <= 21` | warning |
| `days_left <= 7` or `<= 0` | critical |
| Incompatible SAN/hostname or key | critical |
| Served serial ≠ stored serial | critical |
| Postfix (465/587) or Dovecot (993) fails | critical |
| Agent has not run for > 2 intervals | critical |
| `cf-mail` without SNI does not match | critical |
| Failed installation | critical |

Alerts are recorded in `alerts.json` and in the systemd journal, and are
delivered via `alerts.webhook_command` (JSON over stdin) or `alerts.webhook_url`.
The channel does **not** use the monitored SMTP: if mail is down, the alert still
gets out. They are deduplicated by code/hostname and resent at most every 6 hours
or when the severity changes.

## Transactional installation and rollback

`install` (and the deploy hook's `sync`) follows this order:

```text
1. non-blocking lock per hostname
2. validate key, chain, SAN, dates and EKU of the new material
3. copy to a staging generation
4. back up the active generation, SNI map, Dovecot snippet and previous health
5. activate the generation (atomic symlink + atomic copy to stable paths)
6. regenerate the SNI map and local_name snippet
7. postmap if applicable
8. postfix reload + verify 465/587
9. dovecot reload + verify 993
10. verify all SNI certificates and the no-SNI certificate
11. commit: prune old generations (3 are kept)
```

If any step 5-10 fails, the generation, map and snippet are restored; **both**
daemons are reloaded and 465/587/993 and no-SNI are verified. A failure in
pruning or post-commit metrics does **not** revert the active certificate.
A failed installation enters exponential backoff (60 s up to 1 h) and alerts;
there are no infinite retries. Two simultaneous runs are serialized by lock
and the second exits without changes.

`rollback` explicitly reverts to the previous generation, regenerates the map and
the snippet, reloads Postfix and Dovecot, and verifies. `cf-mail` is neither installed nor
reverted from the panel.

The agent does not touch accounts, passwords, mailboxes or maildir, does not recreate the
container, and never runs `certbot renew`/`certbot certonly` in the
`sync`/`install`/`rollback`/`audit`/`verify` paths.

## API

Namespace `/api/v1/plugins/mail/certificates`.

| Method | Path | Permission |
|---|---|---|
| GET | `/certificates` | `mail.certificates.read` |
| GET | `/certificates/health` | `mail.certificates.read` |
| GET | `/certificates/alerts` | `mail.certificates.read` |
| GET | `/certificates/{hostname}` | `mail.certificates.read` |
| GET | `/certificates/{hostname}/jobs/{job_id}` | `mail.certificates.read` |
| POST | `/certificates/{hostname}/request` | `mail.certificates.manage` |
| POST | `/certificates/refresh` | `mail.certificates.manage` |
| POST | `/certificates/{hostname}/verify` | `mail.certificates.manage` + platform |
| POST | `/certificates/{hostname}/sync` | `mail.certificates.manage` + platform |
| POST | `/certificates/{hostname}/install` | `mail.certificates.manage` + platform |
| POST | `/certificates/{hostname}/rollback` | `mail.certificates.manage` + platform |

`POST .../request` accepts `{"action":"ensure"|"force","reason":"..."}`, writes
`requests/<uuid>.json` atomically with mode `0600` and responds `202`:

```json
{
  "job_id": "uuid",
  "hostname": "mail.systutor.com",
  "state": "queued",
  "poll_url": "/api/v1/plugins/mail/certificates/mail.systutor.com/jobs/uuid"
}
```

A second request with an active job for the same hostname responds `409`
with the existing `job_id`. `cf-mail` responds `409`: it is not regenerated in this
phase.

`GET /certificates` returns `items`, `stale`, `generated_at`, `agent_last_seen`
and `error`. A tenant sees only `mail.<domain>` for its Hosting domains;
hostnames without an associated domain and `cf-mail` are only visible to the
infrastructure administrator (`core.platform.admin`). A hostname from another tenant responds
`404`, not `403`, to avoid leaking its existence.

`GET /certificates/health` returns `agent_state`, `agent_last_run_at`,
`interval_seconds`, `stale`, `default` (admin only), `certificates` with
`last_verified_at`, `last_ok_at`, `consecutive_failures`, `served_serial`,
`sni`/`no_sni` and the `critical_count`/`warning_count` counters.
`GET /certificates/alerts` returns only alerts for visible hostnames;
global alerts (for example, a stale agent) and `cf-mail` are admin-only.

The `verify|sync|install|rollback` actions are platform administrator only:
a tenant with `mail.certificates.manage` receives `403`; another tenant's
hostname responds `404`. All enqueue a job and respond `202`, and the
result is polled with `/jobs/{job_id}`. Each action is recorded in the audit log
(`domains.certificate.verified|synced|installed|rolled_back`).

The existing `GET /{domain}/certificate` endpoint (proxy web certificate) does not
change.

## Permissions

| Permission | Who | Scope |
|---|---|---|
| `mail.certificates.read` | infrastructure admin and tenants | own domains; admin sees everything |
| `mail.certificates.manage` | infrastructure admin only | request, force, refresh, verify, install, sync and revert |

`mail.certificates.manage` is **not** in `tenant_admin_permissions`. A
tenant can query but cannot run Certbot or infrastructure actions.
The 0036 actions additionally require `core.platform.admin`.

## UI

In **Mail**, the **TLS Certificates** button opens the modal of the same name
(subtitle **Spanel · Mail infrastructure**). It shows only the
certificates for the domain of the tenant being viewed (`tenant_only=true`,
even for the platform admin); Domains keeps only the Traefik web
certificates.

The list has no per-row actions: double-clicking a certificate opens a
nested modal with its alerts, health, serials, SNI probes and actions.
**Regenerate** uses `ensure`; **Force** asks for confirmation with a Let's Encrypt
rate limit warning. Buttons are disabled without
`mail.certificates.manage`, with an active job, for `cf-mail` or when the state
is stale. Critical alerts block only destructive actions. An agent failure
is shown once and is not retried in a loop.

Since 0036 the modal also shows **Agent status**, **Last
verification**, per-certificate health (`ok`, served serial, SNI ports) and
critical alerts. The admin has **Verify**, **Sync** and
**Revert**. If there is a critical alert (san/serial/port/`cf-mail`/stale
agent), destructive actions are disabled until it is resolved.

## Operation

- One certificate per hostname; multiple SANs are accepted in the model.
- The agent allowlist must match the inventory hostnames.
- The timer refreshes `state.json` every 10 minutes even if there are no requests.
- Agent logs do not contain keys. Jobs only store messages and
  metadata.
- If the agent is down, the UI shows "Status unavailable" and does not enable
  destructive actions.
- `state.json` and `jobs/` are kept for auditing.

## Rollback (0036 scope)

1. Reverting the commit removes timers, hook, endpoints and new actions;
   existing certificates and `cf-mail` are not touched.
2. Uninstall units and hook and keep state/jobs/backups for auditing:

   ```bash
   systemctl disable --now spanel-cert-audit.timer spanel-cert-deploy.service
   rm /etc/systemd/system/spanel-cert-audit.{service,timer}
   rm /etc/systemd/system/spanel-cert-deploy.service
   rm /etc/letsencrypt/renewal-hooks/deploy/spanel-cert-sync
   systemctl daemon-reload
   ```

3. If an installation was left half-done, restore the last generation:

   ```bash
   PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent rollback \
     --hostname mail.systutor.com --config /etc/spanel/cert-agent.toml
   ```

   The agent restores the generation, SNI map and Dovecot snippet, reloads both
   daemons and verifies 465/587/993 and no-SNI.
4. `cf-mail` is not modified: there is no rollback for the default.
5. Keep `state.json`, `alerts.json`, `jobs/`, `generations/` and `backups/`
   for auditing.

## Rollback (0035 scope)

1. Reverting the commit removes the modal, the API, the agent package and the
   units. Existing certificates and `cf-mail` are not touched.
2. Uninstall units and keep state/jobs:

   ```bash
   systemctl disable --now spanel-cert-agent.path spanel-cert-agent.timer
   rm /etc/systemd/system/spanel-cert-agent.{path,service,timer}
   systemctl daemon-reload
   ```

3. If a new SNI certificate was installed, restore the Postfix map and the
   Dovecot block from the generation backups (`backups/<hostname>/`) or
   from `<file>.spanel-bak`, reload Postfix and Dovecot. Do not recreate the
   container.
4. `cf-mail` is not modified in this phase: there is no rollback for the
   default certificate.

## Tests

```bash
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/mail/backend/tests \
  vendor/systutor-core/plugins/hosting/backend/tests -q

PYTHONPATH=scripts .venv/bin/python -m pytest \
  scripts/spanel_cert_agent/tests -q
```

The agent tests use `tmp_path`, a fake command runner, a fake TLS
connector and do not touch the network, Docker, root, `/etc` or a real Certbot.
