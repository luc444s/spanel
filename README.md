# PanelTutor

Multi-tenant administration system for email, web domains and WordPress/static sites on Docker. It lets you manage mail accounts, websites and domains for multiple tenants through a modern web interface.

English | [Español](README.es.md)

## Key Features

- **Multi-tenant**: Full isolation between domains/organizations
- **Docker Mailserver**: Direct integration with DMS over SSH
- **Account management**: Create, list and change email passwords
- **RBAC**: Role-based permission system (superadmin, admin, user)
- **Modern UI**: React, Tailwind CSS and shadcn/ui frontend
- **Plugin System**: Modular plugin-based architecture
- **WordPress and static hosting**: creation by type, users, configuration, lifecycle and adoption of existing sites
- **Domains and proxy**: primary and extra domains published by Traefik, with certificate status
- **File manager**: binary explorer with ZIP support for WordPress and static sites
- **Database access**: site-specific credential and phpMyAdmin launch, without exposing it to the Internet
- **Audit**: Event log for traceability

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Browser — React SPA (apps/web, Vite + TypeScript)            │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTPS
┌───────────────────────────▼──────────────────────────────────┐
│ Traefik — proxy network                                      │
│ TLS · HTTP→HTTPS · routes the panel and tenant sites         │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP :3000 (internal only)
┌───────────────────────────▼──────────────────────────────────┐
│ PanelTutor app — FastAPI                                     │
│ Auth (JWT) · RBAC · Tenants · Audit · React static bundle    │
│ Plugins: Mail, Hosting, Domains, Apps, Files, Tenants        │
└───────┬───────────────────┬───────────────────┬──────────────┘
        │                   │                   │
┌───────▼────────┐  ┌───────▼────────┐  ┌───────▼──────────────┐
│ PostgreSQL     │  │ Redis          │  │ apps-worker          │
│ data/metadata  │  │ cache + queues │  │ Dramatiq (async)     │
└────────────────┘  └────────────────┘  └───────┬──────────────┘
                                                │
┌───────────────────────────────────────────────▼──────────────┐
│ Docker daemon — used by the app and apps-worker              │
│ local socket or SSH (sshpass)                                │
├──────────────────────────────────────────────────────────────┤
│ WordPress/static sites · client Compose apps · phpMyAdmin    │
│ Docker Mailserver (DMS) + mail certificate agent             │
└──────────────────────────────────────────────────────────────┘
```

## Prerequisites

- **Docker** and **Docker Compose** (v2+)
- **Node.js** 20+ (for local frontend development)
- **Python** 3.12+ (for local backend development)
- **PostgreSQL** 16+ (database)
- **Redis** 7+ (cache and queues)

## Operational scripts

The operator tool catalog, with risk level, execution context and safe
examples, is in [`scripts/README.md`](scripts/README.md).
Read it before running scripts that act on Docker, Hosting, Apps, domains,
certificates or tenants.

## Quick Installation

### With Docker (Recommended)

```bash
# 1. Clone the repository
git clone <repository-url>
cd mailadmin

# 2. Configure environment variables
cp .env.docker.example .env.docker
# Edit .env.docker with the server's real values
# Never commit .env.docker

# 3. Ensure the external Traefik network exists and Traefik is attached to it
docker network inspect proxy >/dev/null 2>&1 || docker network create proxy

# 4. Start services
docker compose up -d --build

# 5. Access through Traefik at the Host configured in the labels
#    (spanel.systutor.com by default). Port 3000 is not published.
```

### Local Development

```bash
# 1. Clone the repository
git clone <repository-url>
cd mailadmin

# 2. Configure the Python environment
python -m venv .venv
source .venv/bin/activate
pip install -e vendor/systutor-core

# 3. Configure environment variables
cp .env.example .env
# Edit .env with real local values
# Never commit .env

# 4. Install frontend dependencies
cd apps/web
npm install
cd ../..

# 5. Start development services
npm run dev
```

## Configuration

### Main Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SYSTUTOR_DATABASE_URL` | PostgreSQL connection URL | - |
| `SYSTUTOR_REDIS_URL` | Redis connection URL | - |
| `SYSTUTOR_JWT_SECRET_KEY` | Secret for signing JWT | - |
| `SYSTUTOR_SEED_ADMIN_EMAIL` | Initial admin email | - |
| `SYSTUTOR_SEED_ADMIN_PASSWORD` | Initial admin password | - |
| `SYSTUTOR_CORS_ORIGINS` | Allowed CORS origins | - |
| `MAIL_SERVER_HOST` | Mail server host | - |
| `MAIL_SERVER_PORT` | Server SSH port | `22` |
| `MAIL_SERVER_USER` | SSH user | `root` |
| `MAIL_SERVER_PASSWORD` | SSH password | - |
| `MAIL_DMS_CONTAINER` | DMS container name | `mailserver` |
| `MAIL_ACCOUNTS_CACHE_ENABLED` | Redis cache for the mail list | `true` |
| `MAIL_ACCOUNTS_CACHE_TTL` | TTL (seconds) of the cached mail list | `60` |
| `SPANEL_DOCKER_TRANSPORT` | How the panel reaches the Docker daemon (`ssh` or `local`); in local Docker it must be declared explicitly | `ssh` |
| `SPANEL_DOCKER_SSH_HOST` | Docker host for the hosting plugin | - |
| `SPANEL_DOCKER_SSH_USER` | SSH user for the Docker host | - |
| `SPANEL_HOSTING_SITES_DIR` | Stable per-site directory on the Docker host | `/opt/spanel/sites` |
| `SPANEL_HOSTING_STATIC_IMAGE` | Image for static sites | `nginx:alpine` |
| `SPANEL_PHPMYADMIN_URL` | Legacy URL included in the credentials response (the UI no longer uses it) | - |
| `SPANEL_PHPMYADMIN_INTERNAL_URL` | Internal phpMyAdmin URL, reachable from the app container on a shared Docker network | `http://spanel-phpmyadmin:8080` |
| `SPANEL_PHPMYADMIN_SESSION_TTL` | Duration of the tab session (seconds, maximum 3600) | `1500` |
| `SPANEL_PHPMYADMIN_TIMEOUT` | HTTP timeout to the internal service (seconds) | `15` |
| `SPANEL_PHPMYADMIN_ARBITRARY` | `true` sends `pma_servername` at login (multi-database hub) | `false` |
| `SPANEL_PHPMYADMIN_DB_NETWORK` | Internal network shared by phpMyAdmin and the MariaDB instances; empty = disabled | - |
| `SPANEL_HOSTING_FILES_ROOT` | Authorized internal root for the WordPress site file manager | `/var/www/html` |
| `SPANEL_FILEMANAGER_HELPER_IMAGE` | PHP + ZipArchive image used for the static site file manager | `wordpress:cli` |

### Docker Transport (hosting and file manager)

`SPANEL_DOCKER_TRANSPORT` decides how the `hosting` and
`filemanager` plugins reach the Docker daemon. The default value is still `ssh`,
so in a containerized deployment it must be declared explicitly:

```bash
SPANEL_DOCKER_TRANSPORT=local
```

With `local`, each command runs as `sh -c "docker ..."` inside the
app container, against the host daemon through
`/var/run/docker.sock` (already mounted in `docker-compose.yml`). It does not use
`SPANEL_DOCKER_SSH_*` or `MAIL_SERVER_PASSWORD`. If the variable is missing, the panel
tries SSH and site and file operations fail even if the socket is
available.

### Mail Server Configuration

The system is designed to work with **Docker Mailserver (DMS)** running on a remote server. Communication takes place over SSH:

```bash
# Example configuration in .env.docker or in the server's secret manager
MAIL_SERVER_HOST=<mail-server-host>
MAIL_SERVER_PORT=22
MAIL_SERVER_USER=<ssh-user>
MAIL_SERVER_PASSWORD=<ssh-password>
MAIL_DMS_CONTAINER=<dms-container-name>
MAIL_USE_SSH=true

# The mail list is cached in Redis (read-through, TTL) and invalidated when an account is created
MAIL_ACCOUNTS_CACHE_ENABLED=true
MAIL_ACCOUNTS_CACHE_TTL=60
```

With the cache enabled, `GET /mail/accounts` does not make a round trip to the mail server while
the entry is alive, and two consecutive requests produce a single call to the
server. `MAIL_ACCOUNTS_CACHE_ENABLED=false` disables the cache entirely.

## Plugins

### Mail Plugin (`mail`)

Manages email accounts for each tenant.

**Endpoints:**
- `GET /api/v1/plugins/mail/mail/accounts` - List tenant accounts
- `POST /api/v1/plugins/mail/mail/accounts` - Create a new account
- `PUT /api/v1/plugins/mail/mail/accounts/{email}/password` - Change password

**Permissions:**
- `mail.accounts.read` - View accounts
- `mail.accounts.create` - Create accounts
- `mail.accounts.password.update` - Change passwords

### Tenant Plugin (`tenant`)

Tenant management (superadmin only).

**Endpoints:**
- `GET /api/v1/plugins/tenant/tenants` - List tenants
- `POST /api/v1/plugins/tenant/tenants` - Create tenant
- `PUT /api/v1/plugins/tenant/tenants/{tenant_id}` - Update tenant
- `GET /api/v1/plugins/tenant/tenants/{tenant_id}/users` - List tenant users
- `POST /api/v1/plugins/tenant/tenants/{tenant_id}/users` - Assign user
- `DELETE /api/v1/plugins/tenant/tenants/{tenant_id}/users/{user_id}` - Remove user

**Permissions:**
- `tenant.tenants.read` - View tenants
- `tenant.tenants.manage` - Manage tenants

### Hosting Plugin (`hosting`)

Manages WordPress sites and static sites that live in Docker containers on
a remote (SSH) or local host. Ownership is a row in `hosting_sites`, not a Docker
label: another tenant sees 404, just as if the site did not exist.

**Endpoints** (all with `TenantContext` and an ownership guard):

- `GET /api/v1/plugins/hosting/sites` — `hosting.sites.read`
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`; accepts
  `{"kind": "wordpress"|"static", "name": "...", "domain": "..."}`. WordPress
  returns the admin password once; a static site returns `null`.
- `POST /api/v1/plugins/hosting/sites/{id}/{start|stop|restart}` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/logs` — `hosting.sites.read`
- `GET|PATCH /api/v1/plugins/hosting/sites/{id}/config` — `hosting.sites.read` / `manage`
- `POST /api/v1/plugins/hosting/sites/{id}/secrets/salts` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/users` — `hosting.users.read`
- `POST /api/v1/plugins/hosting/sites/{id}/users/{wp_user_id}/password` — `hosting.users.password`
- `POST /api/v1/plugins/hosting/sites/{id}/database` — `hosting.sites.manage`
- `DELETE /api/v1/plugins/hosting/sites/{id}` — `hosting.sites.delete` (platform)

A `static` site provisions `spanel-<slug>-web` and its own volume with
`nginx:alpine`, `index.html`, `styles.css` and `app.js`. It does not create a database,
WordPress users or a password. The configuration, database,
users, salts and phpMyAdmin routes respond `409 hosting_site_wrong_kind` for a
static site; lifecycle, logs, deletion and the file manager work for both.

Existing sites are adopted with an operator CLI, never over HTTP:

```bash
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run
```

#### Database

The site dialog's `Database` button reveals the site's **own** credential
(the user WordPress already uses, not root) and offers to open phpMyAdmin.
The response uses `Cache-Control: no-store`, the host is validated as a
container, and the password never appears in the URL, log or audit trail. If
`SPANEL_PHPMYADMIN_URL` is empty, the credential is still shown. The button
opens a new tab under the panel origin: the server logs in with
the site's own user and issues a single-use ticket.
`SPANEL_PHPMYADMIN_INTERNAL_URL` must be reachable from the app
container (internal Docker network shared with phpMyAdmin). If it is not reachable, the
dialog shows a controlled error. No new port or subdomain is published.

phpMyAdmin runs separately as a hub for all databases:

```bash
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal --dry-run
./scripts/phpmyadmin_up.sh --down --yes
```

The script creates the internal `spanel-db` network if missing, connects the legacy MariaDB
and phpMyAdmin to it (without removing them from their networks) and mounts
`scripts/phpmyadmin_arbitrary.php`, which only accepts database container
names (`mariadb`, `spanel-*-db`, `*-db`). It never creates databases or touches volumes, and
always publishes on `127.0.0.1`. Each database user sees only their own database. To
access from another machine, use an SSH tunnel or `tailscale serve`; never publish
the port on `0.0.0.0` or expose it with Traefik. `SPANEL_PHPMYADMIN_URL` is
kept only for compatibility; the proxy uses `SPANEL_PHPMYADMIN_INTERNAL_URL`
and the tab does not contact the phpMyAdmin port directly. The app and
phpMyAdmin must share an existing Docker network (for example
`mariadb_internal`): the app can be added with a deployment `docker-compose.override.yml`:

```yaml
services:
  app:
    networks: [default, proxy, mariadb_internal]
networks:
  mariadb_internal:
    external: true
```

#### One phpMyAdmin for all databases

With `SPANEL_PHPMYADMIN_ARBITRARY=true` and `SPANEL_PHPMYADMIN_DB_NETWORK=spanel-db`
each site's login sends `pma_servername=<db-container>` and, if that
container is not yet on `spanel-db`, the panel connects it once before
logging in (idempotent: if it is already there, it does not run Docker). Provisioning a new
WordPress site connects its MariaDB to the same network; if that connection fails, the
creation fails and cleans up containers and volumes as usual. The audit entry
`hosting.site.phpmyadmin_access` includes `database`, `host` and, only if there was a
connection, `network`; never the password. With either of the two variables
off or empty, the login does not send `pma_servername` and no
`docker network connect` runs: the behavior is the previous one.

Configure `SPANEL_PHPMYADMIN_INTERNAL_URL=http://<container-name>:8080`
in the app environment. In local Docker deployments use
`SPANEL_DOCKER_TRANSPORT=local`; there is no need to publish another port.

#### File manager

Enable the `filemanager` plugin and assign `filemanager.files.read` (browse and
download) or `filemanager.files.manage` (upload, edit, create, rename,
delete, compress and extract). The detail view of a running site shows
**File manager**, for both WordPress and static sites. The plugin and
Hosting check the user, tenant, permissions and site ownership on every operation;
the browser only sends relative paths. The WordPress root is
configurable with `SPANEL_HOSTING_FILES_ROOT` (default `/var/www/html`); the
static site root is always `/usr/share/nginx/html`. Neither is chosen from the
client. Button or drag-and-drop uploads, downloads, ZIP and the minimal
editor use a local/SSH binary channel to the container. The editor rejects
concurrent changes and does not open binaries. ZIPs are validated against absolute
paths, `..`, links and size limits before extracting.

WordPress containers must include the PHP `zip` extension (the official
image includes it). Static sites use `nginx:alpine`, which has no PHP:
for them the same fixed script runs in a disposable `wordpress:cli`
container that mounts the site volume with `--volumes-from`. The image
can be changed with `SPANEL_FILEMANAGER_HELPER_IMAGE`. It is best to pre-pull it
on the Docker host so the first operation does not wait for a pull:

```bash
docker pull wordpress:cli
```

The helper runs as root inside an ephemeral, networkless container, with access
only to the site volumes. MariaDB permissions and other
containers are not modified.

### Domains Plugin (`domains`)

Manages a site's **extra domains** (those that live in Traefik's file provider)
and shows the primary domains read-only. The primary domain is written by Hosting;
the extra domain is written by this plugin; the mail domain belongs
to Tenant/Mail. A Host never has two owners, and the backend refuses to cross that
boundary.

**Endpoints:**

- `GET /api/v1/plugins/domains` — `domains.domains.read`; includes the tenant's primary,
  extra and reserved domains (`published=false`), and `proxy_available` /
  `proxy_error` if Traefik does not respond
- `POST /api/v1/plugins/domains` — `domains.domains.manage`; with `site_id`
  publishes the route, with `{domain}` and no site only **reserves** ownership in
  `tenant_domains` (no YAML, no certificates, no Docker)
- `POST /api/v1/plugins/domains/{domain}/link` — `domains.domains.manage`;
  `{site_id, www}` publishes a reserved domain on a tenant site using the same
  writer as site creation
- `GET /api/v1/plugins/domains/check?domain=...` — `domains.domains.read`
- `GET /api/v1/plugins/domains/{domain}/certificate` — `domains.domains.read`;
  a reserved domain responds `state="unpublished"` without touching the network
- `DELETE /api/v1/plugins/domains/{domain}` — `domains.domains.manage`; removes
  the route of a published extra domain, or releases the reservation of an unpublished domain

Transport to Traefik is `auto` (`SSH`, Docker or local directory) and is
configured with `SPANEL_PROXY_*`. If no transport is viable, operations
respond `503 domains.proxy_unavailable` instead of writing blindly. The operator
CLI remains available:

```bash
python3 scripts/proxy_domain.py doctor
python3 scripts/proxy_domain.py list
python3 scripts/proxy_domain.py add \
  --site app-frontend --site-port 80 --domain app.example.com \
  --path-prefix /api --path-site app-backend --path-port 8000
```

The optional route preserves the prefix: `https://app.example.com/api/...` is
sent to `app-backend:8000`, while the rest of the domain still reaches the
frontend. Containers connect to the proxy network without publishing their
ports on the host.

## Development

### Project Structure

```
spanel/
├── apps/
│   └── web/                    # React frontend
│       ├── src/
│       │   ├── components/     # UI components
│       │   ├── pages/          # Pages
│       │   └── shared/         # Layout, hooks, utils
│       └── package.json
├── scripts/                    # Operator CLIs (adoption, phpMyAdmin, reset)
├── vendor/
│   └── systutor-core/          # Framework core
│       ├── src/systutor/       # SDK / kernel / contracts
│       ├── app/                # FastAPI app
│       └── plugins/            # mail, tenant, hosting, domains, filemanager
├── docker-compose.yml
├── Dockerfile
└── entrypoint.sh
```

### Available Scripts

```bash
# Development (frontend + backend)
npm run dev

# Frontend only
npm run frontend

# Backend only
npm run services

# Database
npm run db

# Adopt an existing WordPress site (operator)
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run

# Internal phpMyAdmin, pointing to an existing MariaDB
./scripts/phpmyadmin_up.sh --host <mariadb> --network <network> --dry-run
```

### Testing

```bash
# Plugin backend (from the repo root)
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest vendor/systutor-core/plugins/mail/backend/tests -q

PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
    vendor/systutor-core/plugins/hosting/backend/tests \
    vendor/systutor-core/plugins/domains/backend/tests \
    vendor/systutor-core/plugins/filemanager/backend/tests -q

# Frontend (from apps/web)
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

### Build for Production

```bash
# Frontend
cd apps/web
npm run build

# Full Docker
docker compose build
```

## API

### Authentication

```bash
# Login
curl -X POST http://localhost:3000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<admin-email>", "password": "<admin-password>"}'

# Response: {"access_token": "<jwt-token>", "token_type": "bearer"}
```

### Endpoint Usage

```bash
# List tenant mail accounts
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts

# Create a mail account
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "ventas", "password": "<mailbox-password>"}' \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts

# Change password
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "<new-mailbox-password>"}' \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts/ventas@example.com/password

# Site's own credential for phpMyAdmin (explicit POST, no-store)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/plugins/hosting/sites/<site-id>/database
```

## Security

- **Passwords**: Never stored in PostgreSQL, only in DMS
- **Passwords**: Never logged or included in HTTP responses
- **Passwords**: Sent over SSH stdin, never as CLI arguments
- **Database**: the panel reveals the site's own credential (never root) with `no-store`; the database host is validated as a container and the password is not written to logs or audit records
- **phpMyAdmin**: listens only on `127.0.0.1`, on an internal network, without autologin; remote access goes through an SSH tunnel or tailnet, never through a public URL
- **Multi-tenant**: Each tenant only sees/edits its own accounts and sites
- **RBAC**: Permissions verified on every endpoint
- **JWT**: Access tokens with configurable expiration
- **CORS**: Configurable origins

## Deployment

### Dokploy

**Not supported yet.** Do not use Dokploy's one-click deployment with the
current Compose file.

PanelTutor assumes a Docker host it controls directly, not a generic application
platform. A Dokploy deployment would need, at minimum:

- an external `proxy` network shared with Traefik;
- write access to `/var/run/docker.sock` (root-equivalent);
- host paths and volumes under `/var/lib/spanel` and `/opt/spanel`;
- the `SPANEL_*` variables from `.env.docker`;
- building this repository's `Dockerfile`, because the frontend is baked into
  the image.

`docker-compose.yml` runs PostgreSQL and Redis as services in the same Compose
stack; it does not assume managed database services. A supported Dokploy
profile has not been published.

### Manual

```bash
# Configure the Apps Manifest Flow idempotently. Detects a single
# Traefik Host(...) label, creates/reuses secrets/apps_key and updates only the
# SPANEL_APPS_* variables in .env.docker. Use --public-url if there are multiple hosts.
python3 scripts/configure_apps.py --dry-run
python3 scripts/configure_apps.py --restart

# Build image
docker compose build

# Start in the background
docker compose up -d

# View logs
docker compose logs -f app

# Stop
docker compose down
```

The configurator never runs `down`, does not recreate PostgreSQL/Redis, and starts
`apps-worker` only after `app` responds healthy. For an origin different
from the detected one:

```bash
python3 scripts/configure_apps.py \
  --public-url https://panel.example.com \
  --key-path /opt/spanel/secrets/apps_key \
  --restart
```

In Compose deployments, Apps generates `APP_DB_NAME`,
`APP_DB_USER` and `APP_DB_PASSWORD` once per application, encrypts them at rest and prepends them to the
temporary environment. They are neutral names: each repository decides how to map them to its
engine. The CLI accepts a private file or a single plain dotenv block:

```bash
python3 scripts/spanel_apps.py deploy APP_ID --env-file ~/app.env --wait
python3 scripts/spanel_apps.py deploy APP_ID --env 'SETTING=value
OTHER=value' --wait
```

The `--env` block remains visible in argv/history; for secrets, a `0600`
file is recommended. No client can overwrite the three
managed variables.

## Troubleshooting

### The mail plugin does not load

1. Verify that the mail server SSH is reachable
2. Verify the `MAIL_*` variables in `.env.docker` or in the server's secret manager
3. Check the logs: `docker compose logs app`

### Database connection error

1. Verify that PostgreSQL is running: `docker compose ps postgres`
2. Verify `SYSTUTOR_DATABASE_URL`
3. Check the PostgreSQL logs: `docker compose logs postgres`

### Frontend does not compile

```bash
cd apps/web
rm -rf node_modules
npm install
npm run build
```

## License

PanelTutor is distributed under the **GNU Affero General Public License v3.0** (AGPL-3.0).

Copyright (C) 2026 luc444s

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU Affero General Public License as published by the Free Software
Foundation, either version 3 of the License or (at your option) any later
version.

See the full text in [LICENSE](./LICENSE).
