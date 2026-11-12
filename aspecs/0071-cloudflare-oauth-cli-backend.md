# A.SPEC 0071 — Connect Cloudflare OAuth from CLI and backend

> `risk: high` — introduces an external OAuth authorization boundary, stores
> refresh/access credentials, adds tenant-scoped persistence and an unauthenticated
> callback endpoint, and registers a remote OAuth client through a one-time
> privileged bootstrap token. Human approver: `luc444s`.

## WHY

PanelTutor can operate its own PowerDNS zones but cannot inspect DNS already
hosted by Cloudflare. The future **DNS and Domains** interface needs a secure,
tenant-scoped Cloudflare connection before it can show the orange-cloud provider
or mutate records. The operator must first be able to bootstrap the OAuth client,
authorize it through the browser, and prove discovery from the existing DNS CLI.

Cloudflare self-managed clients support Authorization Code, not Device Flow.
Therefore the CLI cannot safely complete a headless login by itself: it creates
and monitors a bounded flow while the PanelTutor backend owns the exact HTTPS
callback, code exchange, encrypted token persistence and refresh.

## WHAT

Add one Cloudflare OAuth/discovery capability to the Domains plugin.

### Configuration and encryption

Host configuration is:

```text
SPANEL_CLOUDFLARE_ENCRYPTION_KEY_FILE=/run/secrets/apps_key
SPANEL_CLOUDFLARE_PUBLIC_URL=https://spanel.lab
SPANEL_CLOUDFLARE_TIMEOUT_SECONDS=10
```

The encryption key file must be a regular file containing exactly 32 bytes and
must not be group/world accessible. AES-256-GCM envelopes use random nonces and
AAD containing purpose, tenant/flow identity and key version. Client secrets,
access tokens, refresh tokens, authorization codes, PKCE verifiers and bootstrap
tokens never appear in plaintext database columns, logs, audits, exceptions,
HTTP responses, CLI JSON or test output.

No plaintext environment-variable fallback exists for any secret. The default
key path deliberately reuses the already-mounted host encryption key file, but
the Domains plugin owns its cipher implementation and does not import Apps.

### OAuth client registration

Extend `scripts/spanel_dns.py` with:

```text
cloudflare oauth register
  --account-id ACCOUNT_ID
  --bootstrap-token-file PATH
  --redirect-uri https://spanel.lab/api/v1/plugins/domains/dns/cloudflare/oauth/callback
  [--db-url URL]
  [--apply]
```

The bootstrap token file must be owner-only. It is read only for the request and
never persisted. Registration resolves scope IDs from
`GET /client/v4/oauth/scopes`, requiring the supported scopes corresponding to:

- Zone Read;
- DNS Read;
- DNS Write.

It creates a **private** Cloudflare OAuth client using Authorization Code,
`client_secret_basic`, exact redirect URI and required scopes. Dry-run prints a
redacted plan and makes no remote or SQL mutation. `--apply` calls the Cloudflare
API, encrypts the returned one-time client secret immediately, persists one
platform OAuth-client configuration, and prints only client ID, redirect URI and
scope names. Re-registration is a `409`-style CLI conflict unless an explicit
future rotation command is specified; rotation is out of scope here.

Cloudflare endpoints are fixed trusted constants:

```text
API:           https://api.cloudflare.com/client/v4
Authorization: https://dash.cloudflare.com/oauth2/auth
Token:         https://dash.cloudflare.com/oauth2/token
Revoke:        https://dash.cloudflare.com/oauth2/revoke
```

Redirect URIs must be HTTPS, credential-free, query-free, fragment-free, and
their origin must exactly equal `SPANEL_CLOUDFLARE_PUBLIC_URL`. The lab callback
is exactly:

```text
https://spanel.lab/api/v1/plugins/domains/dns/cloudflare/oauth/callback
```

### Tenant authorization flow

Add CLI commands:

```text
cloudflare connect --tenant TENANT_ID [--db-url URL] [--wait-seconds 300]
cloudflare status  --tenant TENANT_ID [--db-url URL]
cloudflare disconnect --tenant TENANT_ID [--db-url URL] [--apply]
cloudflare zones list --tenant TENANT_ID [--db-url URL]
cloudflare records list ZONE --tenant TENANT_ID [--db-url URL]
```

`connect` creates a random single-use state and S256 PKCE verifier/challenge,
persists only encrypted verifier plus state hash with a five-minute expiry, and
prints/JSON-emits the Cloudflare authorization URL. It then waits for flow state
without ever receiving the OAuth token. `--wait-seconds 0` returns immediately.

The public callback validates the state hash, expiry, unused status, redirect
URI and PKCE transaction before exchanging the code with the encrypted client
secret. It marks the flow consumed before returning a minimal HTML success or
failure page. Replay, unknown, expired, denied or already-consumed callbacks do
not exchange a code and reveal no tenant/account existence.

Successful exchange encrypts access/refresh tokens, records granted scopes and
expiry, discovers the selected Cloudflare account via the authorized API, and
upserts one active connection per tenant. Reconnecting replaces credentials only
after the new authorization and discovery both succeed. A failed reconnect keeps
the previous active connection.

`disconnect` is dry-run unless `--apply`; apply calls Cloudflare's revocation
endpoint best-effort, removes local encrypted credentials transactionally,
consumes pending flows for that tenant, audits the result without secrets and is
idempotent.

### Discovery client and presentation

One injected, synchronous `CloudflareClient` owns all Cloudflare HTTP calls. It
uses finite timeouts, bearer authorization for resource APIs, refreshes once on
an expired/401 access token under a connection-row lock, stores rotated tokens,
and normalizes validation, authorization, rate-limit, unavailable and timeout
errors without response bodies or credentials.

Discovery supports paginated zone and DNS-record listing. It normalizes zone
name/status/account/name servers and record ID/name/type/TTL/proxied timestamps.
It does not create, edit or delete records in this A.SPEC.

After successful `connect`, human output is:

```text
SUCCESS Cloudflare connected
Account: <name>
Scopes: Zone Read, DNS Read, DNS Write
Zones discovered: N
  example.com  active  12 records
```

`zones list` gives the same summary. `records list` shows owner, type, TTL and
proxied state; TXT content and all other record content are redacted by default.
`--json` is machine-stable and likewise contains no record content or secrets.

### Persistence

Add plugin-owned tables:

```text
domains_cloudflare_oauth_client
  id=1, account_id, client_id, secret_ciphertext, secret_nonce,
  key_version, redirect_uri, scopes_json, created_at, updated_at

domains_cloudflare_connections
  id, tenant_id UNIQUE, cloudflare_account_id, account_name,
  access_ciphertext, access_nonce, refresh_ciphertext, refresh_nonce,
  key_version, scopes_json, expires_at, status, created_at, updated_at

domains_cloudflare_oauth_flows
  id, tenant_id, state_hash UNIQUE, verifier_ciphertext, verifier_nonce,
  key_version, redirect_uri, status, error_code, expires_at,
  consumed_at, created_at, updated_at
```

No DNS record content is persisted. Migration upgrade/downgrade tests prove all
constraints and that downgrade removes only these three tables.

### FastAPI surface and authorization

Register a separate thin router below the existing Domains DNS prefix:

```text
POST /dns/cloudflare/oauth/start
GET  /dns/cloudflare/oauth/callback       # public, state-bound
GET  /dns/cloudflare/status
POST /dns/cloudflare/disconnect
GET  /dns/cloudflare/zones
GET  /dns/cloudflare/zones/{zone_id}/records
```

All routes except callback require `domains.dns.cloudflare`; start/disconnect
also require `TenantContext.is_platform`. The callback has no user session and
is authorized exclusively by random, hashed, expiring, single-use state. Tenant
IDs are always derived from context or stored flow, never accepted from callback
query data. `users.is_superadmin` is never consulted.

Normalized status: `422` invalid request/configuration, `403` RBAC or missing
granted scope, `404` absent tenant connection/zone, `409` active/pending conflict,
`429` Cloudflare rate limit, `502` invalid upstream response, `503` unavailable,
`504` timeout.

Successful register/connect/disconnect actions are audited without secrets.
Publish `domains.dns.cloudflare_connected` and
`domains.dns.cloudflare_disconnected` only after persistence commits.

## SCOPE

- Cloudflare OAuth-client bootstrap through the existing DNS CLI.
- Authorization URL, HTTPS callback, exchange, refresh and revocation.
- Encrypted platform client secret and tenant token persistence.
- Tenant-scoped status, zone discovery and redacted record discovery in CLI/API.
- Models, migration, permission, events, focused tests and operator docs.

## OUT OF SCOPE

- Frontend, navigation rename, orange cloud button/icon or any `apps/web` file.
- Creating, editing, deleting, importing or exporting Cloudflare DNS records.
- Creating/deleting Cloudflare zones or changing nameservers, proxy settings,
  DNSSEC, rules, certificates, Workers or registrar data.
- Binding discovered Cloudflare zones to local registered domains.
- Public OAuth-client promotion/domain verification and multi-publisher SaaS.
- Client-secret rotation, scheduled reconciliation or background refresh jobs.
- Any change to PowerDNS behavior or existing DNS routes.
- Real-account authorization without the human Cloudflare administrator.

## CONTRACT

### Preconditions

1. `https://spanel.lab` resolves in the operator browser and its certificate is
   trusted.
2. The backend can reach `dash.cloudflare.com` and `api.cloudflare.com`.
3. The encryption key file exists with valid owner-only permissions.
4. Registration receives an owner-only bootstrap-token file with OAuth Clients
   Write for the supplied Cloudflare account.
5. The Cloudflare administrator completes browser consent within five minutes.

### Postconditions

1. The operator can dry-run then register one private Cloudflare OAuth client
   without persisting or printing the bootstrap token/client secret.
2. The CLI can initiate browser authorization and report SUCCESS plus discovered
   zone names/status/record counts after the backend callback completes.
3. Tenant credentials are encrypted at rest and never cross the browser/CLI.
4. API/CLI discovery is tenant-scoped, paginated, finite-time and redacts record
   content.
5. Disconnect revokes best-effort and removes local authorization idempotently.
6. No Cloudflare DNS mutation endpoint exists in this A.SPEC.

## INVARIANTS

```yaml
invariants:
  - Existing PowerDNS CLI commands and FastAPI routes retain their exact behavior.
  - Existing Domains publication, certificates, parent-zone grouping and tenant isolation remain unchanged.
  - No OAuth, bootstrap or DNS record secret appears in plaintext persistence, output, logs, audit or errors.
  - Callback authorization is independent of users.is_superadmin and cannot select a tenant from query input.
  - Cloudflare discovery is read-only even though consent includes DNS Write for the next layer.
  - Apps plugin code and kernel public APIs are not imported or modified.
```

## VERIFICATION

Run exactly:

```bash
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_client.py plugins/domains/backend/tests/test_cloudflare_oauth.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_cli.py plugins/domains/backend/tests/test_cloudflare_migration.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_dns_client.py plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_dns_router.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m ruff check plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py plugins/domains/backend/dns/cli.py plugins/domains/backend/dns/cli_commands.py plugins/domains/backend/tests/test_cloudflare_*.py plugins/domains/migrations/004_create_cloudflare_oauth.py
cd vendor/systutor-core && ../../.venv/bin/python -m pyright plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py
python3 scripts/spanel_dns.py cloudflare --help
git diff --check
```

The real `https://spanel.lab` browser consent is an operator deployment
acceptance because it requires the human Cloudflare administrator; deterministic
tests use an injected fake OAuth/API transport and prove the complete callback,
refresh, pagination, revocation, replay and redaction contracts.

## ROLLBACK

Before any real connection, stop rollout by withholding the bootstrap token.
After connection, run `cloudflare disconnect --tenant ... --apply`, revoke the
authorization in Cloudflare if upstream revocation failed, then execute the
tested migration downgrade. Remove the OAuth client from Cloudflare manually
only after all tenant grants are revoked. Keep audits; never restore token rows
from backup. Re-applying the migration creates empty tables and cannot replay an
old grant.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0071-cloudflare-oauth-cli-backend.md
    - scripts/spanel_dns.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/__init__.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/client.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/config.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/crypto.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/oauth.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/service.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/store.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_schemas.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli_commands.py
    - vendor/systutor-core/plugins/domains/backend/models.py
    - vendor/systutor-core/plugins/domains/backend/register.py
    - vendor/systutor-core/plugins/domains/migrations/004_create_cloudflare_oauth.py
    - vendor/systutor-core/plugins/domains/permissions/domains.json
    - vendor/systutor-core/plugins/domains/events/domains.json
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_client.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_oauth.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_service.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_cli.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_migration.py
  prohibited:
    - apps/web
    - docker-compose.yml
    - docker-compose.powerdns.yml
    - .env.docker.example
    - vendor/systutor-core/src/systutor
    - vendor/systutor-core/plugins/apps
    - vendor/systutor-core/plugins/domains/frontend
    - installer
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Domains DNS operator CLI
    - Domains plugin models and migration
    - Domains Cloudflare OAuth callback and discovery API
    - Domains RBAC, events and audit
  indirect:
    - Tenant database lifecycle
    - Host encryption-key availability
    - Cloudflare account authorization
  must_not_affect:
    - Existing PowerDNS management
    - Domains publication and certificates
    - Apps credentials and OAuth
    - Kernel RBAC and tenancy contracts
    - Existing frontend
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0067
  must_compose_with:
    - A.SPEC 0070
  systemic_invariants:
    - A zone has only one future write provider; this read-only layer creates no provider binding.
    - Cloudflare and PowerDNS credentials remain isolated and never reach the browser.
  composition_checks:
    - cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_cloudflare_service.py -q
```

This is a leaf A.SPEC, not the integration release for the complete Cloudflare
provider/frontend capability.

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/backend/cloudflare
```

## Traceability

- Requirement: Operator registers and authorizes Cloudflare, then receives SUCCESS and redacted DNS inventory through CLI/backend.
- owner: domains plugin
- approver: luc444s
- Commit: pending
- Deployment: local HTTPS lab at `https://spanel.lab`; production deferred

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest
- [ ] Composition checks passed when applicable
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
