# A.SPEC 0072 — Connect Cloudflare from the Domains panel

> `risk: high` — relaxes the platform-only OAuth start/disconnect authorization
> introduced by A.SPEC 0071 to the owning tenant, and adds the first frontend
> surface that crosses the browser into an external OAuth consent boundary.
> No token or client secret reaches the browser. Human approver: `luc444s`.

## WHY

A.SPEC 0071 delivered the Cloudflare OAuth client, callback, encrypted token
persistence and read-only discovery, but only through the operator CLI. A tenant
administrator who already has `domains.dns.cloudflare` cannot connect their own
Cloudflare account from the panel: they must ask the platform operator to run a
terminal command. The future DNS interface also needs a visible connection state
before it can show zones hosted by Cloudflare.

A single platform OAuth client is enough for every tenant. What is missing is a
tenant-scoped way to start the same authorization flow, open Cloudflare's consent
page, learn when the backend callback has completed, and disconnect.

## WHAT

Add one coherent Cloudflare connection surface to the existing Domains page.

### Tenant-scoped authorization

`POST /dns/cloudflare/oauth/start` and `POST /dns/cloudflare/disconnect` stop
requiring `TenantContext.is_platform`. They are authorized by
`domains.dns.cloudflare` for the **current tenant only**; the tenant always comes
from the authenticated context, never from the request body or query. The
platform client registration remains an operator CLI action: tenants do not
create OAuth clients.

`domains.dns.cloudflare` becomes a tenant-admin permission and its description
no longer says "platform administrator only for connect/disconnect". Existing
platform administrators keep working unchanged.

### Panel surface

In `vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx`, above
the DNS surface and only when the user has `domains.dns.cloudflare`, render one
Cloudflare connection card:

- disconnected: **Connect Cloudflare** button;
- connecting: disabled button plus a "waiting for Cloudflare authorization"
  message and, when the popup was blocked, a manual authorization link;
- connected: account name, granted scope names, discovered zone count and
  **Disconnect** action.

Clicking **Connect Cloudflare**:

1. calls `POST /dns/cloudflare/oauth/start` and receives `authorization_url`,
   `flow_id` and `expires_at`;
2. opens `authorization_url` in a new browser tab/window; when the popup is
   blocked it shows a manual "open the authorization page" link instead of
   navigating away from the panel;
3. polls `GET /dns/cloudflare/status` every two seconds until the connection is
   active or the flow expiry is reached (never longer than five minutes);
4. on success shows a toast, refreshes the connection card and invalidates the
   DNS zone list so Cloudflare-backed zones appear. A timeout or a denied flow
   shows a localized error and leaves any previous connection untouched.

Clicking **Disconnect** asks for confirmation, calls
`POST /dns/cloudflare/disconnect` with `{"apply": true}`, and refreshes the
card. It is idempotent: disconnecting twice reports success.

The card never renders record content and never receives tokens. The OAuth
callback continues to be a minimal backend-rendered HTML page; the panel only
polls the authenticated status endpoint.

### Presentation contracts

New pure helpers under
`vendor/systutor-core/plugins/domains/frontend/cloudflare/logic.ts` decide:

- when the card is visible (`domains.dns.cloudflare`);
- whether a status payload means connected (`status === "active"` and
  `connected === true`);
- a stable summary line (`account_name`, scope count, zone count) from the
  status payload;
- whether polling must stop (connected, terminal flow state, deadline, or a
  terminal HTTP error);
- the user-facing message for `401/403`, `404`, `409`, `422`, `429`, `502`,
  `503` and `504`, without echoing upstream bodies.

English strings are the source keys; Spanish translations are added to
`apps/web/src/i18n/es/domains.ts`.

### API client

Add `vendor/systutor-core/plugins/domains/frontend/cloudflare/api.ts` using the
existing `@systutor/shell/api/client` conventions and base prefix
`/api/v1/plugins/domains/dns/cloudflare`:

```text
startCloudflareOAuth()   POST /oauth/start
getCloudflareStatus()    GET  /status        (404 => not connected)
listCloudflareZones()    GET  /zones
disconnectCloudflare()   POST /disconnect
```

No zone/record mutation call is added.

## SCOPE

- Tenant-scoped start/disconnect authorization for the existing Cloudflare flow.
- One connection card in the existing Domains page.
- Polling, connected/disconnected/error presentation and zone-list invalidation.
- Tenant-admin permission registration and operator documentation.
- Focused backend router tests and pure frontend logic tests.

## OUT OF SCOPE

- Creating, editing, deleting or importing Cloudflare DNS records.
- Binding Cloudflare zones to locally registered domains or changing PowerDNS.
- Registering/rotating OAuth clients from the panel or per tenant.
- Background reconciliation, scheduled refresh, webhooks or notifications.
- Any change to the callback HTML, PKCE, encryption, client or discovery code.
- New pages, navigation entries or icons outside the Domains page.
- Record-content display.

## CONTRACT

### Preconditions

1. A.SPEC 0071 is deployed: OAuth client registered, callback reachable at the
   configured HTTPS origin, encryption key available.
2. The user has an authenticated tenant context and `domains.dns.cloudflare`.
3. The browser can reach the public HTTPS origin in the registered redirect URI.
4. The Cloudflare administrator completes consent before the five-minute flow
   expiry.

### Postconditions

1. A tenant administrator can start, observe and finish the OAuth flow from the
   panel without a terminal, and the connection is stored for their tenant.
2. Start/disconnect for a tenant identity without `core.platform.admin` returns
   the same normalized results as before for platform identities.
3. No token, client secret, authorization code, PKCE verifier or record content
   appears in any HTTP response consumed by the panel, in the DOM or in logs.
4. A failed or expired flow leaves the previous active connection unchanged.
5. Disconnect remains idempotent and best-effort up to Cloudflare revocation.
6. Every existing Domains, DNS, PowerDNS and Cloudflare route keeps its
   behavior except the two authorization checks named above.

## INVARIANTS

```yaml
invariants:
  - Tenant identity for start, status and disconnect comes only from the authenticated TenantContext, never from callback query, request body or path.
  - users.is_superadmin is never consulted; platform access remains core.platform.admin and tenant access remains the plugin permission.
  - A tenant can only read or remove its own Cloudflare connection; another tenant's connection is reported as absent.
  - No OAuth, bootstrap or DNS record secret appears in frontend types, API responses, DOM, logs, audit or errors; record content is never persisted or rendered.
  - Cloudflare discovery stays read-only; no record or zone mutation endpoint is added.
  - A.SPEC 0071 behavior is preserved except that start/disconnect no longer require platform context; callback authorization, PKCE, encryption, refresh and redaction remain unchanged.
  - Existing PowerDNS CLI commands, Domains publication, certificates, parent-zone grouping and tenant isolation remain unchanged.
  - Apps plugin code and kernel public APIs are not modified.
```

## VERIFICATION

Run exactly:

```bash
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_dns_router.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m ruff check plugins/domains/backend/cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_router.py
cd vendor/systutor-core && ../../.venv/bin/python -m pyright plugins/domains/backend/cloudflare_router.py
cd apps/web && node node_modules/vitest/vitest.mjs run src/plugins/domains-cloudflare.logic.test.ts
cd apps/web && node node_modules/typescript/bin/tsc --noEmit
cd apps/web && node node_modules/vite/bin/vite.js build
git diff --check
```

The browser-consent acceptance remains an operator deployment check because it
requires the human Cloudflare administrator; deterministic tests use the
injected fake OAuth/API transport from A.SPEC 0071 and pure frontend policy
tests.

## ROLLBACK

Revert this A.SPEC's commit. The panel card disappears and start/disconnect
return to the platform-only rule of A.SPEC 0071. No migration or stored data is
created by this A.SPEC, so tenant connections, encrypted tokens and the OAuth
client remain valid; operators can still disconnect through the CLI. If a tenant
connection must be removed while the panel is rolled back, use
`cloudflare disconnect --tenant ... --apply`.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0072-cloudflare-connect-button.md
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/api.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/logic.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/types.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/CloudflareConnectionCard.tsx
    - vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx
    - apps/web/src/i18n/es/domains.ts
    - apps/web/src/plugins/domains-cloudflare.logic.test.ts
    - vendor/systutor-core/plugins/domains/backend/cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_router.py
    - vendor/systutor-core/plugins/domains/permissions/domains.json
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
  prohibited:
    - vendor/systutor-core/plugins/domains/backend/cloudflare/
    - vendor/systutor-core/plugins/domains/backend/cloudflare_schemas.py
    - vendor/systutor-core/plugins/domains/backend/dns/
    - vendor/systutor-core/plugins/domains/frontend/dns/
    - vendor/systutor-core/plugins/domains/migrations/
    - apps/web/src/features
    - apps/web/src/shared
    - apps/web/src/i18n/es.ts
    - apps/web/src/plugins/domains-dns.logic.test.ts
    - vendor/systutor-core/src/systutor
    - vendor/systutor-core/plugins/apps
    - docker-compose.yml
    - docker-compose.powerdns.yml
    - .env.docker.example
    - installer
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Domains page Cloudflare connection card
    - Cloudflare OAuth start/disconnect authorization
    - Domains tenant-admin permission set
  indirect:
    - Cloudflare connection state refreshed by the panel
    - DNS zone list after a successful connection
  must_not_affect:
    - Cloudflare callback, PKCE, encryption, refresh and discovery contracts
    - Existing PowerDNS management, Domains publication and certificates
    - Tenant isolation and kernel RBAC/tenancy APIs
    - Apps credentials and OAuth
    - Existing frontend pages and navigation
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0071
  must_compose_with:
    - A.SPEC 0068
  systemic_invariants:
    - One platform OAuth client authorizes many tenants; tenants never create or see OAuth clients.
    - Cloudflare credentials stay in the backend; the browser only carries the opaque state and authorization code handled by Cloudflare.
    - Cloudflare discovery remains read-only.
  composition_checks:
    - cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_dns_router.py -q
```

This A.SPEC explicitly supersedes only the platform-only authorization of
`start`/`disconnect` from A.SPEC 0071. Every encrypted-storage, callback and
read-only rule of 0071 still applies.

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/frontend/cloudflare
```

## Traceability

- Requirement: A tenant administrator connects, observes and disconnects their Cloudflare account from the Domains panel, using the tenant-scoped backend flow.
- owner: domains plugin
- approver: luc444s
- Commit: 8be4438
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
