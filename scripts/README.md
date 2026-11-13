# SPanel operational scripts

This directory collects operator tools, one-off migrations,
labs and E2E tests. This document is the index for deciding **which
script to use, where to run it and what it can modify**.

Run commands from the repository root, unless a section says
otherwise:

```bash
cd /path/to/mailadmin
python3 scripts/<script>.py --help
```

English | [Español](README.es.md)

## Safety rules

- Review `--help` first and use `--dry-run`, plan mode or execution without
  `--apply`/`--execute` when available.
- Do not confuse **Hosting** with **Apps**. Hosting registers existing WordPress or
  static sites; Apps manages Docker Compose projects through their
  runtime or API.
- Scripts that use the database load the normal SPanel configuration. Verify that
  it points to the correct environment before running them.
- Docker scripts act on the daemon selected by the environment
  (`DOCKER_HOST` or the `SPANEL_DOCKER_SSH_*` variables, depending on the tool).
- Do not pass secrets as arguments unless the CLI itself requires it. Arguments
  can remain visible in history and in the process list.
- Make a backup before any operation marked as **destructive**.

### Levels used in this document

| Level | Meaning |
|---|---|
| **Read** | Diagnostics; should not change state. |
| **Plan by default** | Does not write until `--apply`, `--execute`, `--fix` or `--yes` is added. |
| **Write** | May modify the database, configuration, routes or Docker resources. |
| **Destructive** | May delete, replace or restore resources. Requires special review. |

## Quick guide

| Need | Script |
|---|---|
| Deploy or update Spanel | [`deploy.sh`](#deploysh) |
| Grant a permission to a tenant operator | [`grant_tenant_permission.py`](#grant_tenant_permissionpy) |
| Adopt a specific WordPress | [`hosting_adopt.py`](#hosting_adoptpy) |
| Discover and sync all published sites | [`hosting_sync.py`](#hosting_syncpy) |
| Complete the domain of an adopted site | [`hosting_set_domain.py`](#hosting_set_domainpy) |
| Take a site down without deleting its container | [`hosting_down.py`](#hosting_downpy) |
| Fully delete a managed WordPress | [`hosting_delete_wordpress.py`](#hosting_delete_wordpresspy) |
| Back up or restore a site | [`site_backup.py`](#site_backuppy) / [`site_restore.py`](#site_restorepy) |
| Operate Apps over HTTP | [`spanel_apps.py`](#spanel_appspy) |
| Audit or deploy Compose directly | [`app_deploy.py`](#app_deploypy) |
| Publish or remove a domain in Traefik | [`proxy_domain.py`](#proxy_domainpy) |
| Refresh/adopt the observed domain inventory | [`domains_sync.py`](#domains_syncpy) / [`domains_adopt.py`](#domains_adoptpy) |
| Diagnose/repair routes and TLS | [`spanel-repair.py`](#spanel-repairpy) |
| Manage mail certificates | [`install_cert_agent.py`](#install_cert_agentpy), [`configure_cert_agent_dms.py`](#configure_cert_agent_dmspy) and [`spanel_cert_agent/`](#spanel_cert_agent) |

## Deployment

### `deploy.sh`

**Write operation.** Runs the canonical `spanel` Compose deployment with the
PowerDNS override. It creates the application encryption key atomically with
mode `600` when absent, validates it when present, and never rotates it.

```bash
./scripts/deploy.sh
```

Requires `.env.docker`, `.env.powerdns`, and either `SPANEL_APPS_PUBLIC_URL` or
`SPANEL_DOMAIN`. To select another key location, set
`SPANEL_APPS_ENCRYPTION_KEY_PATH` in `.env.docker`.

### `grant_tenant_permission.py`

**Write operation; dry-run available.** Creates a durable tenant role and
assigns explicit permissions without granting `core.platform.admin`. For the
operator allowed to register the shared Cloudflare OAuth client:

```bash
python3 scripts/grant_tenant_permission.py \
  --tenant TENANT_SLUG \
  --email operator@example.com \
  --role cloudflare_registrar \
  --permission domains.cloudflare.register \
  --dry-run

# Repeat without --dry-run after reviewing the plan.
```

In the production container, use `/app/scripts/grant_tenant_permission.py`.

## Hosting and WordPress

### `hosting_adopt.py`

**Plan by default with `--dry-run`; writes without that option.** Adopts a single
existing WordPress container into a tenant. Verifies that it is WordPress and creates
a `hosting_sites` row plus its audit entry. Does not start, stop or modify the
container.

```bash
python3 scripts/hosting_adopt.py \
  --container spanel-demo-wp --tenant demo --domain demo.example --dry-run

# After reviewing:
python3 scripts/hosting_adopt.py \
  --container spanel-demo-wp --tenant demo --domain demo.example
```

It cannot be used to adopt Apps resources or static sites.

### `hosting_sync.py`

**Plan by default; writes only with `--apply`.** Scans running
containers, reads their Traefik labels, infers domain and type,
resolves the tenant and reconciles `hosting_sites`. It can adopt WordPress
and static sites or complete data for existing rows. It never starts, stops or
deletes containers.

```bash
# Review everything
python3 scripts/hosting_sync.py

# Review only one and resolve ambiguous data
python3 scripts/hosting_sync.py \
  --only sitio-web \
  --assign sitio-web=demo \
  --domain sitio-web=www.example.com \
  --kind sitio-web=static

# Apply exactly the same selection
python3 scripts/hosting_sync.py \
  --only sitio-web \
  --assign sitio-web=demo \
  --domain sitio-web=www.example.com \
  --kind sitio-web=static \
  --apply
```

Use `--ignore` to exclude a container and `--retire-old` to retire old
rows configured by the script; the latter does not delete the container.

### `hosting_set_domain.py`

**Plan by default with `--dry-run`; writes without that option.** Lists sites or
assigns the primary domain to an already adopted container. Checks uniqueness,
extra domains and the tenant's allowed suffix.

```bash
python3 scripts/hosting_set_domain.py --list
python3 scripts/hosting_set_domain.py \
  --container sitio-web --domain sitio.example.com --dry-run
```

### `hosting_down.py`

**Plan by default; destructive for the record with `--execute`.** Logically takes
a site down: removes its managed route and deletes the Hosting row,
but preserves containers, images, volumes, data and the domain reservation.
Requires exact confirmation.

```bash
python3 scripts/hosting_down.py --container sitio-web
python3 scripts/hosting_down.py \
  --container sitio-web --execute --confirm sitio-web
```

If the route lives in the container's own labels, the script blocks the takedown. The
replacement flow can preserve it temporarily with
`--keep-labeled-route`.

### `hosting_delete_wordpress.py`

**Plan by default; destructive with `--execute`.** Deletes a managed
WordPress site, its container and volume, and deletes only its database indicated by
`WORDPRESS_DB_NAME`. Does not delete the MariaDB container, volume or image.
It also cleans up secrets and, unless `--keep-domain`, releases the domain.

```bash
python3 scripts/hosting_delete_wordpress.py --container spanel-demo-wp
python3 scripts/hosting_delete_wordpress.py \
  --container spanel-demo-wp --execute --confirm spanel-demo-wp
```

### `reset_wp_password.py`

**Read with `--listar` or `--dry-run`; writes in normal mode.** Lists
users or changes a single `wp_users` row without stopping WordPress. If no
password is provided, it generates one and shows it once.

```bash
python3 scripts/reset_wp_password.py spanel-demo-wp --listar
python3 scripts/reset_wp_password.py spanel-demo-wp --usuario admin --dry-run
python3 scripts/reset_wp_password.py spanel-demo-wp --usuario admin
```

## Site backups

### `site_backup.py`

**Non-destructive write.** Generates a verifiable ZIP of a WordPress site
(files and database) or a static site (files). Detects the type and avoids
storing credentials in the manifest.

```bash
python3 scripts/site_backup.py --container sitio-web --dry-run
python3 scripts/site_backup.py --container sitio-web --out /root/backups
python3 scripts/site_backup.py --verify /root/backups/wp-sitio-web-FECHA.zip
```

See `--help` for database overrides. Avoid `--db-pass` in shared
shells; it is preferable for the script to resolve credentials from the
container.

### `site_restore.py`

**Destructive.** Validates a ZIP produced by `site_backup.py`, first creates a
safety backup of the current state, and restores files and, for WordPress, the
database. Attempts an automatic rollback if it fails.

```bash
python3 scripts/site_restore.py \
  --zip /root/backups/wp-sitio-web-FECHA.zip \
  --container sitio-web \
  --yes
```

### `site_backup_link.py`

**Reads a backup; temporarily opens an HTTP/HTTPS server.** Serves a
valid ZIP through a single-use link and ends after the download or the
TTL. Outside loopback it requires a public origin and, usually, TLS.

```bash
python3 scripts/site_backup_link.py \
  --zip /root/backups/wp-sitio-web-FECHA.zip --ttl 600
```

## Apps

### `spanel_apps.py`

**Write depending on the subcommand.** Recommended CLI for operating Apps through the API.
Does not access PostgreSQL, Redis, Docker or SSH directly. Requires the URL and a
token per environment or standard input:

```bash
export SPANEL_APPS_API_URL=https://panel.example.com
export SPANEL_APPS_TOKEN='...'
python3 scripts/spanel_apps.py list
python3 scripts/spanel_apps.py services UUID_APLICACION
python3 scripts/spanel_apps.py --json status UUID_OPERACION
```

Available subcommands: `providers`, `repositories`, `add`, `inspect`,
`deploy`, `status`, `list`, `services`, `refresh`, `logs`, `start`, `stop`,
`restart`, `down`, `domain-list`, `domain-assign`, `domain-remove`,
`domain-probe` and `delete`. Use `python3 scripts/spanel_apps.py <command> --help`
for each one's arguments.

### `app_deploy.py`

**Write/destructive depending on the subcommand.** Direct runtime tool
for auditing and deploying a Docker Compose repository without going through the API.
Supports `inspect`, `deploy`, `status`, `logs` and `down`, with local or
SSH transport. `down --purge-data` deletes data and requires confirmation.

```bash
python3 scripts/app_deploy.py inspect --source-dir /path/to/checkout
python3 scripts/app_deploy.py deploy --source-dir /path/to/checkout \
  --project demo --dry-run
python3 scripts/app_deploy.py status --project demo
```

Projects created by Apps carry the label `io.spanel.app.owner`. This CLI
has no adoption command for third-party Compose projects.

### `configure_apps.py`

**Plan with `--dry-run`; writes in normal mode.** Configures exclusively
`SPANEL_APPS_*` variables, creates or validates the encryption key and, with `--restart`,
recreates `app` and `apps-worker` in a safe order. It never runs `docker compose
down` or prints the key.

```bash
python3 scripts/configure_apps.py --public-url https://panel.example.com --dry-run
python3 scripts/configure_apps.py --public-url https://panel.example.com --restart
```

### `migrate_env.py`

**Plan with `--dry-run`; writes to the database in normal mode.** Migrates the
`app.env` of an existing deployment to Apps' reusable encrypted
storage, excluding managed credentials. It must be run **inside the
`app` container**, where the plugins and configuration are available.

```bash
docker compose exec app python3 /app/scripts/migrate_env.py \
  --slug mi-app --dry-run
```

### `refresh_routes.py`

**Plan with `--dry-run`; writes in normal mode.** Re-renders Domains
routes for Apps without using the UI or redeploying. It must be run
**inside the `app` container**. `--restart-proxy` also restarts Traefik.

```bash
docker compose exec app python3 /app/scripts/refresh_routes.py --dry-run
docker compose exec app python3 /app/scripts/refresh_routes.py --slug mi-app
```

### Apps E2E scripts

| Script | Level | Purpose |
|---|---|---|
| `e2e_apps_api.py` | **Destructive** | Black-box E2E against real login, API, worker and Docker; creates and cleans up an application. |
| `e2e_apps_software_editores.py` | **Destructive** | Isolated E2E directly against the Software para Editores repository; generates redacted diagnostic artifacts. |

They are not day-to-day administration tools. Use them only in a prepared
lab and review their variables/requirements with `--help` and their tests in
`scripts/tests/`.

### `configure_powerdns.sh`

Automates the authoritative lab and the panel's private deployment. The plan
is read-only; `apply` starts PowerDNS, generates mode-`0600` `.env.powerdns`,
mounts the key by path (never by value), rebuilds the panel and verifies the
authenticated API from the `app` container.

```bash
scripts/configure_powerdns.sh plan
scripts/configure_powerdns.sh apply
scripts/configure_powerdns.sh status
scripts/configure_powerdns.sh verify
```

## Domains, proxy and static sites

### `domains_sync.py`

**Plan by default; writes only with `--apply`.** Reads one complete snapshot of
managed Traefik YAML, validates site and tenant ownership, and atomically
refreshes the PostgreSQL observation used by the platform-wide Domains view. It
never edits Traefik. Use `--json` for automation.

```bash
python3 scripts/domains_sync.py
python3 scripts/domains_sync.py --apply --json
```

### `domains_adopt.py`

**Plan by default; writes only with `--apply`.** Claims administrative ownership
of an already-published domain when it is free and the selected tenant/site
matches its managed YAML. It also refreshes the observation, but never rewrites
the YAML.

```bash
python3 scripts/domains_adopt.py --domain example.com --tenant demo
python3 scripts/domains_adopt.py --domain example.com --tenant demo --site SITE_ID --apply
```

`benchmark_domains_global.py` measures synchronization and projected global
reads separately for local or Docker transport.

### `proxy_domain.py`

**Read or write depending on the subcommand.** Diagnoses Traefik and manages
domains/routes/certificates through `doctor`, `list`, `add`, `status` and
`remove`. The `internal` profile uses the lab; `public` uses the real proxy.

```bash
python3 scripts/proxy_domain.py --profile public doctor
python3 scripts/proxy_domain.py --profile public add \
  --site sitio-web --domain sitio.example.com
python3 scripts/proxy_domain.py --profile public status --domain sitio.example.com
```

### `spanel-static.py`

**Write/destructive depending on the subcommand.** Creates and manages
HTML/CSS/JS sites on nginx with a volume and HTTPS route. Provides `create`, `list`,
`status`, `delete` and `doctor`, locally or over SSH.

```bash
python3 scripts/spanel-static.py create \
  --name pagina-demo --domain pagina-demo.spanel
python3 scripts/spanel-static.py list
python3 scripts/spanel-static.py delete --name pagina-demo
```

Detailed documentation: [`spanel_static/README.md`](spanel_static/README.md).

### `spanel-profiles.py`

**Write/destructive depending on the subcommand.** Provisions and verifies deterministic static
profiles (`plain`, `react`, `boot`, `astro`) with `up`, `verify`,
`status` and `down`. It is oriented toward lab/validation, not customer
sites.

```bash
python3 scripts/spanel-profiles.py status
python3 scripts/spanel-profiles.py up --only astro
python3 scripts/spanel-profiles.py verify
python3 scripts/spanel-profiles.py down
```

### `spanel-repair.py`

**Plan by default; writes with `--fix`.** Detects incorrect Traefik routes,
default certificate, missing `certResolver` and orphaned files. With
`--fix` it can patch YAML, move orphans and restart Traefik subject to a
cooldown.

```bash
python3 scripts/spanel-repair.py
python3 scripts/spanel-repair.py --json
python3 scripts/spanel-repair.py --fix --only-host sitio.example.com
```

### `traefik-lab.sh`

**Write/destructive within the lab.** Creates an isolated Traefik, a local ACME
CA and internal DNS. Does not touch the real Traefik. `plan` changes nothing;
`down --purge` also deletes the lab's data and CA.

```bash
./scripts/traefik-lab.sh plan
./scripts/traefik-lab.sh up
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down
```

`adopt` only inspects the real Traefik; it does not register or modify it.
Detailed documentation: [`traefik-lab/README.md`](traefik-lab/README.md).

### `probe_docker.py`

**Read.** Tests local/SSH configuration and connectivity with Docker,
runs `docker ps` and identifies WordPress candidates. It is a diagnostic; it does not
adopt or modify containers.

```bash
python3 scripts/probe_docker.py
python3 scripts/probe_docker.py --env-file otro.env
```

## phpMyAdmin

### `phpmyadmin_up.sh`

**Limited write/destructive.** Starts phpMyAdmin as an internal hub for
existing databases, creates the internal network if missing and connects MariaDB without removing it from
its networks. `--down` deletes only the phpMyAdmin container.

```bash
./scripts/phpmyadmin_up.sh --host mariadb --dry-run
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
./scripts/phpmyadmin_up.sh --down --yes
```

`phpmyadmin_arbitrary.php` is not a CLI: it is the configuration mounted by the
script. It restricts accepted hosts to `mariadb` or names ending in
`-db`.

## Mail certificates and scheduled tasks

### `install_cert_agent.py`

**Plan with `--dry-run`; writes as root in normal mode.** Discovers Certbot
lineages, generates the privileged agent configuration and installs its systemd
units. It is idempotent.

```bash
sudo python3 scripts/install_cert_agent.py --dry-run
sudo python3 scripts/install_cert_agent.py --allowlist mail.example.com
sudo python3 scripts/install_cert_agent.py --check --json
```

The full architecture is in
[`../docs/certificate-manager.md`](../docs/certificate-manager.md).

### `configure_cert_agent_dms.py`

**Plan by default; writes and restarts only with `--apply`.** Adapts the agent
to an existing Docker Mailserver: finds the `/tmp/docker-mailserver` mount,
splits managed hostnames (`sni-spanel`, `spanel-local_name.conf`) from legacy
ones, updates `postfix-main.cf`, `dovecot.cf` and the agent config, and verifies
that no hostname is lost. Backups are left as `.spanel-dms-<stamp>.bak` and the
operation is idempotent.

```bash
sudo python3 scripts/configure_cert_agent_dms.py --json
sudo python3 scripts/configure_cert_agent_dms.py --apply
```

### `spanel_cert_agent/`

**Privileged write depending on the subcommand.** Certificate agent
package. Its entry point is:

```bash
PYTHONPATH=scripts python3 -m spanel_cert_agent --help
PYTHONPATH=scripts python3 -m spanel_cert_agent \
  --config /etc/spanel/cert-agent.toml status
```

Subcommands: `run-once`, `refresh`, `status`, `verify`, `audit`, `sync`,
`install` and `rollback`. They are normally invoked by the systemd units, not by an
interactive operator. `certbot-deploy-hook.sh` is the Certbot hook and is also not
run manually.

### `mail_cert.py`

**Read or critical write depending on the subcommand.** Previous/specific flow
for internal docker-mailserver certificates. `status`, `check` and `audit`
diagnose; `issue` and `renew` can issue the certificate and recreate the
DMS container from its inspection, with rollback if verification fails.

```bash
python3 scripts/mail_cert.py status
python3 scripts/mail_cert.py audit --json
python3 scripts/mail_cert.py issue --dry-run
python3 scripts/mail_cert.py renew --all --yes
```

`mail-cert.service` and `mail-cert.timer` are units specific to the original
environment and contain concrete paths/hosts. Review and adjust them before
installing them; for new installations the generic agent is preferred.

### `install_routes_timer.py`

**Plan with `--dry-run`; writes as root in normal mode.** Installs,
updates or removes the systemd timer that runs `refresh_routes.py` inside the
`app` container.

```bash
sudo python3 scripts/install_routes_timer.py --dry-run
sudo python3 scripts/install_routes_timer.py --interval-minutes 15
sudo python3 scripts/install_routes_timer.py --uninstall
```

## Tenants and database domains

### `sync_tenant_domains.py`

**Plan by default; writes with `--apply`.** Removes redundant rows
from `tenant_domains` that duplicate their own tenant's primary domain and
reports collisions. Always produces a JSON report. Does not touch Docker, proxy,
certificates, users or sites.

```bash
python3 scripts/sync_tenant_domains.py
python3 scripts/sync_tenant_domains.py --apply
```

### `unify_tenants.py`

**Plan by default; writes with `--apply`; `--purge` is destructive.**
Consolidates domains, users and sites into a target tenant, deactivates the
sources and saves a reversible JSON plan. Does not touch containers or the proxy.

```bash
python3 scripts/unify_tenants.py \
  --target-slug demo --from-slug antiguo --move-all-sites

python3 scripts/unify_tenants.py \
  --target-slug demo --from-slug antiguo --move-all-sites --apply

python3 scripts/unify_tenants.py \
  --rollback logs/unify-tenants-FECHA.json --apply
```

## Auxiliary files

### Internal packages

They are not run file by file; they implement the documented entry points:

| Directory | Entry point |
|---|---|
| `spanel_static/` | `python3 scripts/spanel-static.py` |
| `spanel_profiles/` | `python3 scripts/spanel-profiles.py` |
| `spanel_cert_agent/` | `PYTHONPATH=scripts python3 -m spanel_cert_agent` |

### systemd units

`scripts/systemd/` contains templates for:

| Unit | Function |
|---|---|
| `spanel-cert-agent.path/service/timer` | Process requests and refresh certificate state. |
| `spanel-cert-audit.service/timer` | Periodically audit certificates. |
| `spanel-cert-deploy.service` | Certificate deployment hook. |
| `spanel-repair.service/timer` | Run periodic route/TLS repair. |
| `spanel-routes-refresh.service/timer` | Reconcile Apps routes. |

Do not copy a unit to production without reviewing paths, user, permissions and
host variables. The installers generate or adapt the units when
applicable.

### Tests

`scripts/tests/` and `spanel_cert_agent/tests/` contain automated tests;
they are not operational commands. Tests with `_live` or the E2E tests may require
real Docker, SSH, credentials and lab resources. Do not run them in
production without reading the file first.

Example of unit tests without a lab:

```bash
python3 -m pytest scripts/tests/test_hosting_sync.py -q
python3 -m pytest scripts/tests/test_spanel_apps.py -q
```

## Maintaining this index

When adding, renaming or deleting an entry point inside `scripts/`:

1. document its purpose, execution context and risk level here;
2. include a safe or plan example;
3. keep `--help` as the exact reference for arguments;
4. link a specialized guide if the flow needs more detail;
5. update or add tests for the script.
