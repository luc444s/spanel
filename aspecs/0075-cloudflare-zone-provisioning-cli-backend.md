# A.SPEC 0075 — Provision Cloudflare zones and origin DNS from CLI and backend

> `risk: high` — creates public Cloudflare zones and mutates address records.
> Human approver: `luc444s`.

## WHY

Spanel can discover Cloudflare zones and manage individual records, but adding a
domain still leaves the operator to create or locate the zone and point it at
the Spanel origin manually. The first reliable delivery surface must be the
operator CLI, with plan/apply semantics, before the same shared capability is
used by the Add domain interface.

## WHAT

Add one tenant-scoped Cloudflare provisioning capability shared by CLI and
FastAPI.

### OAuth permission upgrade

The private OAuth client requires these Cloudflare scopes:

```text
Zone Read
Zone Write
DNS Read
DNS Write
offline_access
```

Registration/repair updates the existing client without rotating its secret.
The client status reports `needs_repair=true` when any required scope or
`offline_access` is absent. Existing tenant connections without `Zone Write`
remain readable but provisioning fails with a normalized permission error until
the client is repaired and the tenant reconnects.

### Shared provisioning service

Create a cohesive module below `backend/cloudflare/` that owns validation,
planning and apply orchestration. Given `tenant_id`, `domain`, public IPv4,
`www`, and `replace`:

1. normalize the domain as a lowercase absolute DNS hostname without a trailing
   dot and validate IPv4 with the standard library;
2. require an active tenant Cloudflare connection containing `Zone Write`;
3. list the tenant's zones and choose the longest existing zone suffix;
4. when no parent zone exists, plan creation of a full Cloudflare zone named
   exactly as the requested domain in the connected account;
5. plan an apex/owner `A` record to the supplied IPv4 with `ttl=1` (Auto) and
   `proxied=false`;
6. with `www`, plan `www.<domain>` as a `CNAME` to `<domain>` with `ttl=1` and
   `proxied=false`;
7. treat an identical desired record as no drift and never duplicate it;
8. reject conflicting `A`, `AAAA`, or `CNAME` records at either managed owner
   unless explicit `replace=true` was included in both plan and apply;
9. with replace, delete only the conflicting address/alias records at the two
   explicitly managed owners, then create the desired records;
10. re-read and return the resulting zone and managed records.

Planning is read-only and returns stable JSON containing zone action, selected
zone, nameservers, desired records, conflicts, actions and drift. Record content
is included only for the requested public IPv4/CNAME and conflict preview; no
token or secret is returned or audited.

Apply recomputes the plan immediately and never trusts a client-supplied plan.
If it creates a zone and any subsequent record write fails, it best-effort
deletes that newly created zone. Existing zones are never deleted as
compensation. Every successful apply is audited without credentials and emits
`domains.dns.cloudflare_provisioned` after commit. Retries with the same desired
state are idempotent.

This flow never changes registrar delegation. A newly created zone is normally
`pending`; its assigned Cloudflare nameservers are returned prominently.

### Cloudflare client

The existing injected `CloudflareClient` remains the only Cloudflare transport.
Add:

```text
POST   /zones
DELETE /zones/{zone_id}
```

Zone creation sends the connected account ID and `type=full`. Responses use the
existing normalized `CloudflareZone`; upstream bodies remain opaque on errors.

### CLI first

Extend `scripts/spanel_dns.py` through the existing parser/service path:

```text
cloudflare provision plan DOMAIN --tenant TENANT --ipv4 ADDRESS [--www]
  [--replace] [--db-url URL] [--json]

cloudflare provision apply DOMAIN --tenant TENANT --ipv4 ADDRESS [--www]
  [--replace] [--db-url URL] [--json] --apply
```

`plan` returns exit `3` when drift exists and `0` when already converged.
`apply` without `--apply` prints the same plan and returns `3` on drift without
mutation. Successful apply returns `0`. Human output states whether the zone is
reused/created, every managed record, `DNS only`, and nameservers/delegation
instructions for a new zone. JSON is stable and contains `proxied: false`.

### FastAPI backend

Add tenant-scoped endpoints using `domains.dns.cloudflare`:

```text
POST /dns/cloudflare/provision/plan
POST /dns/cloudflare/provision/apply
```

Both derive tenant and actor exclusively from `TenantContext`; tenant IDs are
not accepted in request bodies. Request fields are `domain`, `ipv4`, `www=false`,
and `replace=false`. The apply response includes `applied=true`. Existing record
CRUD and discovery contracts remain unchanged.

## SCOPE

- OAuth scope upgrade and repair/reconnect detection.
- Cloudflare zone create/delete transport.
- Shared provisioning plan/apply service.
- CLI plan/apply commands.
- Thin FastAPI plan/apply routes and schemas.
- Secret-free audit/event, documentation and focused tests.

## OUT OF SCOPE

- Frontend changes or automatic invocation from Add domain.
- Registrar APIs, nameserver delegation, DNSSEC or zone deletion commands.
- IPv6 origin provisioning, Cloudflare orange-cloud proxy, Workers, rules or SSL
  settings.
- Scheduled reconciliation, background jobs or persistence of desired records.
- Inferring the public IP from an external service or network interface.
- Public-suffix inference when no Cloudflare parent zone exists: the requested
  domain itself is the zone to create.
- Changes to PowerDNS, Traefik, certificates or local domain reservation.

## CONTRACT

### Preconditions

1. The platform OAuth client has been repaired with `Zone Write`.
2. The tenant has reconnected and granted all required scopes.
3. The tenant connection account may create zones when creation is needed.
4. The operator supplies the intended public IPv4 explicitly.

### Postconditions

1. CLI and FastAPI use the same planner/apply implementation and Cloudflare
   client.
2. The requested domain resolves through DNS-only records toward the supplied
   origin after Cloudflare delegation is active.
3. Existing conflicting records are untouched unless replace was explicit.
4. A repeated apply produces no duplicate zone or record.
5. Newly assigned nameservers are returned; registrar state is never claimed or
   changed.

## INVARIANTS

```yaml
invariants:
  - CloudflareClient remains the only module that performs Cloudflare HTTP calls.
  - Every provisioned record has proxied=false; no path accepts proxied input.
  - Plan is read-only and apply recomputes current Cloudflare state.
  - Existing records are never overwritten or deleted without explicit replace.
  - Tenant credentials, OAuth secrets and record content unrelated to the requested owners never enter output or audit.
  - Existing PowerDNS, domain reservation, Traefik, certificate and Cloudflare CRUD behavior remains unchanged.
  - No users.is_superadmin authorization path is introduced.
```

## VERIFICATION

Run exactly from the repository root:

```bash
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_client.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_cloudflare_provisioning.py plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_cli.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_dns_client.py plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_dns_router.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m ruff check plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py plugins/domains/backend/dns/cli.py plugins/domains/backend/dns/cli_commands.py plugins/domains/backend/tests/test_cloudflare_*.py
cd vendor/systutor-core && ../../.venv/bin/python -m pyright plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py plugins/domains/backend/dns/cli.py plugins/domains/backend/dns/cli_commands.py
python3 scripts/spanel_dns.py cloudflare provision --help
git diff --check
```

Real Cloudflare apply is an operator acceptance test because it mutates public
DNS. Deterministic tests use the injected transport and fake tenant credentials.

## ROLLBACK

Stop invoking provisioning routes/commands. Existing DNS records and zones are
not removed automatically. If an applied record must be rolled back, restore it
through the existing Cloudflare DNS CRUD surface. Re-register the OAuth client
without `Zone Write` only after all tenants have reconnected under the reduced
scope; this code rollback does not revoke grants remotely.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0075-cloudflare-zone-provisioning-cli-backend.md
    - scripts/spanel_dns.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/client.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/config.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/service.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/provisioning.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_schemas.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli_commands.py
    - vendor/systutor-core/plugins/domains/events/domains.json
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_client.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_service.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_provisioning.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_router.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_cloudflare_cli.py
  prohibited:
    - apps/**
    - installer/**
    - docker-compose.yml
    - docker-compose.powerdns.yml
    - .env*
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/apps/**
    - vendor/systutor-core/plugins/domains/frontend/**
    - vendor/systutor-themes/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Cloudflare OAuth scope consent
    - Cloudflare public zones and DNS records selected by explicit apply
    - Domains Cloudflare CLI and tenant API
  indirect:
    - Existing tenant Cloudflare connections require reconnect before provisioning
    - Public DNS activation still depends on registrar delegation
  must_not_affect:
    - PowerDNS and local DNS ownership
    - Domain reservation and Traefik publication
    - Existing frontend and installer
    - Kernel RBAC and tenancy contracts
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0071
    - A.SPEC 0074
  must_compose_with:
    - Existing Cloudflare OAuth refresh and tenant isolation
    - Existing record CRUD and unified DNS modal
  systemic_invariants:
    - Cloudflare remains an external DNS provider; local domain ownership does not imply registrar delegation.
    - Future Add domain UI must call this backend service rather than duplicate provisioning policy.
  composition_checks:
    - cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_cloudflare_router.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: transport, OAuth lifecycle, provisioning policy, CLI presentation and HTTP routing remain separate responsibilities
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/backend/cloudflare/provisioning.py
```

Do not append provisioning policy to the existing oversized Cloudflare service;
it may expose only the narrow credential/audit helpers required by the dedicated
provisioning module.

`cloudflare/service.py` and `dns/cli_commands.py` are pre-existing legacy
exceptions to the line thresholds. This A.SPEC may add only thin integration
helpers to them; it must keep all new provisioning policy in `provisioning.py`
and does not require an unrelated file-splitting refactor.

## Traceability

- Requirement: CLI-first Cloudflare zone/origin DNS automation with proxy off.
- owner: Domains plugin
- approver: luc444s
- Commits: backend `083809d`; CLI `650d759`
- Deployment: backend capability only; frontend integration remains pending

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established
