# A.SPEC 0073 — Register the Cloudflare OAuth client from the panel

> `risk: high` — receives a privileged Cloudflare bootstrap token over HTTP,
> creates an OAuth client and persists its secret. The token is used once and
> never stored. Dedicated registrar permission only. Human approver: `luc444s`.

## WHY

A.SPEC 0071 requires an operator terminal and a bootstrap-token file to create
the single platform Cloudflare OAuth client. A.SPEC 0072 added the tenant-facing
Connect button, but when no client exists the panel only reports a generic
`422`. An explicitly authorized operator should be able to paste the Cloudflare API token
in the panel, create the private client and then authorize normally, without
SSH or a CLI.

## WHAT

Add one permission-gated registration modal to the existing Cloudflare header
button.

### Backend surface

Two routes, both authorized by the dedicated
`domains.cloudflare.register` permission. Neither `core.platform.admin` nor
`users.is_superadmin` is required:

```text
GET  /dns/cloudflare/oauth/client     -> OAuthClientStatus
POST /dns/cloudflare/oauth/register   -> OAuthClientStatus
```

`OAuthClientStatus` contains only `registered`, `account_id`, `client_id`,
`redirect_uri` and `scopes`. It never contains the client secret.

`POST /oauth/register` accepts a JSON body:

```json
{ "bootstrap_token": "...", "account_id": "optional-32-chars" }
```

- The token is read from the body, used only for the Cloudflare requests and
  discarded; it is never persisted, returned, audited or logged.
- If `account_id` is omitted, the backend calls `GET /accounts` with the token.
  Exactly one account is used automatically; zero, several, or a token without
  account-list permission return a fixed `422` asking for the Account ID.
- Registration creates the same **private** Authorization Code client with
  `client_secret_basic`, the configured exact redirect URI and Zone Read/DNS
  Read/DNS Write scopes that the CLI creates. The returned secret is encrypted
  immediately with the Cloudflare cipher.
- An existing client returns `409`; rotation remains out of scope.
- Validation errors carry fixed messages and never echo the submitted token.
- Success is audited without the token or secret (existing
  `domains.dns.cloudflare_registered` audit).

### Panel surface

`CloudflareConnectButton` gains registrar awareness:

- Users without the registrar permission: unchanged Connect/Disconnect behavior. If the client is
  absent, the normalized `422` explains that an authorized operator must
  register it.
- Registrars: the button also queries `GET /oauth/client`. When
  no client is registered, clicking **Connect Cloudflare** opens a compact
  modal instead of starting OAuth.

The modal (`CloudflareRegisterDialog`) contains:

- title **Register Cloudflare** and a one-line explanation;
- a masked **API token** field (technical text input with visual masking,
  password-manager ignore hints and show/hide; never a password input);
- helper text: the token needs `Account → OAuth Clients → Edit`, is used once
  and is not stored;
- a link to the Cloudflare OAuth clients dashboard page;
- an optional **Account ID** field, hidden by default and revealed by
  "The token cannot detect my account";
- the exact redirect URI rendered read-only;
- Cancel and Register actions with pending state and normalized error text.

On success the modal closes, the client status is refreshed and a toast says
the client was registered; the administrator can then press **Connect
Cloudflare** to complete consent. Closing the modal clears the token from React
state.

### Pure helpers

Extend `cloudflare/logic.ts` with `canManageCloudflareClient(permissions)`,
`needsClientRegistration(status)` and `canRegisterClient(permissions, status)`,
covered by the existing policy test file.

## SCOPE

- Dedicated-permission client status and registration API.
- Automatic single-account resolution from the bootstrap token.
- Compact registration modal wired to the existing Cloudflare button.
- Fixed, secret-free error messages and Spanish translations.
- Focused backend and pure frontend tests.

## OUT OF SCOPE

- Public OAuth-client promotion or domain verification.
- Client-secret rotation, deletion or editing from the panel.
- Per-tenant OAuth clients.
- Storing the bootstrap token or any API token.
- Any change to the OAuth callback, PKCE, token storage or discovery.
- Frontend pages/navigation outside the existing Domains button.

## CONTRACT

### Preconditions

1. The user has `domains.dns.cloudflare` and the dedicated
   `domains.cloudflare.register` permission, assigned through a durable
   tenant role (for example with `scripts/grant_tenant_permission.py`).
2. The backend has a valid encryption key and public URL.
3. The submitted token has `OAuth Clients: Edit` on the target account.
4. The account has no Cloudflare OAuth client registered by this panel yet.

### Postconditions

1. An authorized registrar can register the client from the panel with only
   the token when the token can list exactly one account, or with token plus
   Account ID otherwise.
2. The token never appears in persistence, responses, audit, logs or the DOM
   after submission; the modal clears it on close.
3. An already-registered client returns `409` and is never overwritten.
4. The created client is private, uses the configured exact redirect URI and
   the three required scopes, and its secret is encrypted at rest.
5. Tenant-scoped Connect/Disconnect behavior from A.SPEC 0072 is unchanged.

## INVARIANTS

```yaml
invariants:
  - Registration endpoints require domains.cloudflare.register; neither core.platform.admin nor users.is_superadmin is consulted.
  - The bootstrap token is never persisted, audited, logged, echoed in validation errors or HTTP responses, and is cleared from the browser state on modal close.
  - The client secret is encrypted before persistence and never returned to the browser.
  - Registration never overwrites an existing client; conflict returns 409.
  - Tenant identity for OAuth start/status/disconnect still comes only from TenantContext.
  - No OAuth client is created per tenant; one private platform client remains the model.
  - Callback, PKCE, encryption, refresh and read-only discovery contracts are unchanged.
  - Existing Domains, DNS, PowerDNS and Apps behavior is unchanged.
```

## VERIFICATION

Run exactly:

```bash
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_cloudflare_client.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m ruff check plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py plugins/domains/backend/tests/test_cloudflare_router.py
cd vendor/systutor-core && ../../.venv/bin/python -m pyright plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py
cd apps/web && node node_modules/vitest/vitest.mjs run src/plugins/domains-cloudflare.logic.test.ts
cd apps/web && node node_modules/typescript/bin/tsc --noEmit
cd apps/web && node node_modules/vite/bin/vite.js build
git diff --check
```

A real Cloudflare registration is an operator acceptance check because it needs
a live privileged token; deterministic tests use the injected fake transport.

## ROLLBACK

Revert this commit. The modal and the two routes disappear; the CLI
`cloudflare oauth register` remains available. An already-created OAuth client
stays valid and usable by the tenant Connect button. No migration or stored
bootstrap token remains to clean up; delete the API token in Cloudflare if it
was created only for this flow.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0073-cloudflare-client-registration-modal.md
    - vendor/systutor-core/plugins/domains/backend/cloudflare/client.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/service.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_schemas.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_router.py
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/api.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/logic.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/types.ts
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/CloudflareConnectButton.tsx
    - vendor/systutor-core/plugins/domains/frontend/cloudflare/CloudflareRegisterDialog.tsx
    - apps/web/src/i18n/es/domains.ts
    - apps/web/src/plugins/domains-cloudflare.logic.test.ts
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
  prohibited:
    - vendor/systutor-core/plugins/domains/backend/cloudflare/crypto.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/config.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/oauth.py
    - vendor/systutor-core/plugins/domains/backend/dns/
    - vendor/systutor-core/plugins/domains/frontend/dns/
    - vendor/systutor-core/plugins/domains/migrations/
    - vendor/systutor-core/plugins/domains/permissions/domains.json
    - vendor/systutor-core/plugins/domains/plugin.json
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
    - Cloudflare OAuth client registration API
    - Domains Cloudflare header button and registration modal
  indirect:
    - Cloudflare client status shown to platform administrators
    - Tenant Connect flow after the client exists
  must_not_affect:
    - Cloudflare callback, PKCE, encryption, refresh and discovery
    - Tenant-scoped Connect/Disconnect behavior
    - Existing PowerDNS, Domains publication and certificates
    - Kernel RBAC/tenancy and Apps
    - Existing frontend pages and navigation
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0071
    - A.SPEC 0072
  must_compose_with: []
  systemic_invariants:
    - One private platform OAuth client remains the only app identity.
    - The bootstrap token is a one-shot operator credential, never a stored secret.
    - Tenants only ever see their own connection and never the OAuth client secret.
  composition_checks:
    - cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_service.py -q
```

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

- Requirement: A platform administrator registers the private Cloudflare OAuth client from the panel by pasting a privileged API token.
- owner: domains plugin
- approver: luc444s
- Commit: `26df7ec` (registration fixes through `bbfd3ed`)
- Deployment: local HTTPS lab at `https://spanel.lab`; private client registered and OAuth connection active 2026-11-12

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
