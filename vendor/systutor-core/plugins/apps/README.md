# Apps plugin

Apps deploys tenant-owned Docker Compose applications from GitHub repositories
authorized through a GitHub App installation. Each SPanel installation registers
its own GitHub App from a manifest; there is no global OAuth client secret.

Runtime work is handled only by the serialized `apps` Dramatiq worker. HTTP
requests never invoke GitHub CLI, Docker or SSH. PostgreSQL is the durable source
of truth and broker messages contain only `operation_id`.

## Configuration

- `SPANEL_APPS_PUBLIC_URL`: absolute public origin (no path/query/fragment).
  HTTPS is required in production; loopback HTTP is allowed outside production.
  Every manifest/install callback is built from this value.
- `SPANEL_APPS_ENCRYPTION_KEY_FILE`: shared read-only file with exactly 32
  decoded bytes. The web process reads it lazily; `GET /github/status` never
  reads or decrypts it.
- `SPANEL_APPS_STATE_ROOT`: private directory for pending deployment env files.
- `SPANEL_APPS_KEY_VERSION`: encryption key version stored with ciphertext.
- `SYSTUTOR_REDIS_URL`: one-time manifest/install states and the Dramatiq broker.

Never put PEMs, client secrets, webhook secrets or the encryption key in `.env`
or `.env.docker`.

## Manifest flow

1. `POST /github/manifest/start` creates two independent one-time states bound to
   the actor, tenant and target owner.
2. The browser submits the returned manifest to the exact GitHub host
   (`github.com/settings/apps/new` or
   `github.com/organizations/<slug>/settings/apps/new`).
3. GitHub redirects to `/github/manifest/callback`. SPanel consumes the state,
   exchanges the code once, validates the RSA key and encrypts the PEM with AAD
   purpose + tenant + provider + key version. Client/webhook secrets returned by
   GitHub are discarded.
4. The admin installs the app and selects repositories. GitHub redirects to
   `/github/install/callback`. SPanel verifies the installation as the App with a
   short-lived JWT before persisting metadata.
5. A `github_sync` operation catalogues authorized repositories and tombstones
   repositories that are no longer authorized.

Installation tokens are created only in the worker, scoped to one repository for
inspect/deploy, never persisted and never included in API responses, logs, Redis
or Dramatiq messages.

## Local operation

```bash
# Web + worker from the repository root (Compose)
POSTGRES_USER=verify POSTGRES_PASSWORD=verify POSTGRES_DB=verify docker compose config

# Frontend
cd apps/web
npm test
npm run build
```

The local worker runs with one process, one thread and the `apps` queue:

```bash
dramatiq plugins.apps.backend.tasks --processes 1 --threads 1 --queues apps
```

For local manifest registration, set `SPANEL_APPS_PUBLIC_URL` to an HTTPS tunnel
or `http://localhost:<port>` outside production. GitHub must be able to reach the
manifest and install callbacks.

## CLI

`scripts/spanel_apps.py` is API-first and never imports the plugin runtime or
accesses PostgreSQL, Redis, Docker or SSH. The token comes from
`SPANEL_APPS_TOKEN`/`SPANEL_ACCESS_TOKEN` or secure stdin, never from argv.

```bash
export SPANEL_APPS_API_URL=https://panel.example
export SPANEL_APPS_TOKEN=...          # or pipe it through stdin
scripts/spanel_apps.py providers
scripts/spanel_apps.py repositories --installation <id>
scripts/spanel_apps.py add --installation <id> --repository <id> --name demo
scripts/spanel_apps.py inspect <app-id> --wait
scripts/spanel_apps.py deploy <app-id> --env-file ~/app.env --wait
scripts/spanel_apps.py deploy <app-id> --env 'SETTING=value
OTHER=value' --wait
scripts/spanel_apps.py status <operation-id>
scripts/spanel_apps.py logs|start|stop|restart|down <app-id>
```

`--env-file` must be a regular non-symlink file with mode `0600`. The CLI sends
`Idempotency-Key` on mutations and bounds every wait with `--timeout`.
`--env` accepts one multiline dotenv block for automation, but its plaintext is
visible in the process arguments and shell history; use `--env-file` for secrets.
Apps prepends encrypted, stable `APP_DB_NAME`, `APP_DB_USER` and
`APP_DB_PASSWORD` values and rejects client overrides. Compose maps these neutral
names to whichever database engine the repository uses.

Deployment preflight reports missing Compose interpolation variables by name
before image build. Successful deployments expose one service card per effective
container with image, creation time and declared environment names; secret values
are never returned by the API or stored in the deployment manifest.

## Real Docker E2E

The host image includes `scripts/e2e_apps_software_editores.py`. Run it inside
`apps-worker` to clone the real repository, execute the same Apps audit/deploy
runtime, probe the three services, and remove all isolated test resources. It
writes only redacted command logs and a safe JSON report under the Apps state
volume. The flow also exercises refresh/status, logs, restart, and post-restart
health verification so ownership regressions are detected by a real lifecycle.

## Production checklist

- Provide an HTTPS `SPANEL_APPS_PUBLIC_URL` and a 32-byte encryption key file
  mounted read-only into web and worker.
- Run plugin migrations before enabling Apps; `GET /github/status` reports an
  explicit error readiness state instead of a false "disconnected" when the
  catalog is missing.
- Expose worker health and ensure there are no orphan `queued` operations.
- Run the opt-in lab E2E (`SPANEL_LIVE_TESTS=1`) against a real installation
  before declaring Apps production-ready. Without it, report the real-lab step as
  a GAP; the test suite skips explicitly when the flag is off.
