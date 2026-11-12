# A.SPEC 0067 — Operate tenant DNS through one PowerDNS client in CLI and FastAPI

> `risk: high` — creates and deletes authoritative zones and records, adds
> tenant ownership persistence and exposes mutations through operator and HTTP
> surfaces. The API key remains a host secret and every write is bounded by
> ownership, explicit apply semantics and optimistic concurrency.

## WHY

A.SPEC 0066 proves a real PostgreSQL-backed PowerDNS server, but PanelTutor has
no stable software contract for operating it. Calling the PowerDNS API directly
from routers, scripts or a future frontend would duplicate validation, error
handling and secret management while making tenant isolation impossible to
audit.

The operator contract must exist and pass against the real lab before FastAPI
is exposed. FastAPI must then reuse the exact same PowerDNS client and DNS
domain service rather than executing CLI commands or reimplementing HTTP calls.

## WHAT

Add one cohesive DNS capability to the existing Domains plugin, delivered in
this mandatory internal order:

1. a reusable synchronous PowerDNS HTTP client and normalized DNS models;
2. an operator CLI proven against the A.SPEC 0066 lab;
3. PostgreSQL ownership/public-configuration persistence;
4. tenant-aware service and thin FastAPI router using the same client;
5. unit, API, migration and real-lab verification.

### Shared PowerDNS client

`plugins.domains.backend.dns.PowerDnsClient` is the only implementation allowed
to call PowerDNS. It uses the standard-library HTTP stack or an injected
transport, has a finite configurable timeout, sends `X-API-Key`, normalizes
PowerDNS errors and never includes the key or full administrative URL in an
exception, log, audit row or response.

Configuration is host-owned:

```text
SPANEL_PDNS_API_URL
SPANEL_PDNS_API_KEY_FILE
SPANEL_PDNS_SERVER_ID=localhost
SPANEL_PDNS_TIMEOUT_SECONDS=5
```

The API key is read from a regular file that must not be group/world readable.
There is no `SPANEL_PDNS_API_KEY` plaintext fallback. Configuration values are
not returned through HTTP.

The client supports server health, zone list/create/read/delete, RRset patching
and retrieval. It canonicalizes zone/owner names with a trailing dot at the
PowerDNS boundary and exposes normalized names without ambiguity internally.

### Operator CLI

Add `scripts/spanel_dns.py` with this exact command tree:

```text
doctor
nameservers show|plan|apply|verify
zones list|create|show|delete
rrsets list|plan|apply|delete
zone import|export
```

Common rules:

- human output by default and machine-stable JSON with `--json`;
- read-only commands never mutate;
- every mutating command is plan/dry-run by default and requires `--apply`;
- destructive deletion additionally requires the exact zone/name in a
  confirmation flag when stdin is not interactive;
- exit codes: `0` success/no drift, `2` arguments/validation, `3` drift or
  unapplied plan, `4` conflict, `5` PowerDNS unavailable, `6` ownership/data;
- errors and JSON never contain the API key;
- the entrypoint only parses/prints and delegates to shared DNS modules;
- no subprocess invocation of `curl`, `pdnsutil`, `psql` or the future HTTP API.

`doctor` verifies configuration-file permissions and authenticated PowerDNS
health. `nameservers verify` performs real UDP and TCP SOA/NS queries against a
specified resolver/port. The zone-file importer/exporter supports the record
types in this A.SPEC, preserves TXT data, emits deterministic output, rejects
`$INCLUDE`, paths, unsupported directives/types and records outside the origin.

### Persistence and authority

Add two plugin-owned tables:

```text
domains_dns_zones
  id, tenant_id, zone_name, kind, observed_version, created_at, updated_at
  UNIQUE(zone_name)

domains_dns_settings
  id=1, ns1_hostname, ns2_hostname,
  ns1_ipv4, ns1_ipv6, ns2_ipv4, ns2_ipv6,
  soa_contact, default_ttl, version, updated_at
```

- PostgreSQL is authoritative for tenant-to-zone ownership and public
  nameserver configuration.
- PowerDNS is authoritative for SOA, NS and all RRsets.
- URL and API key are never columns.
- Zone names are lowercase absolute DNS names without a trailing dot in SQL.
- A zone has exactly one tenant owner; ownership is never inferred from
  PowerDNS account text or client input.
- Public settings are mutable only through the platform-admin double gate.

The migration has executable upgrade and downgrade tests. Downgrade removes
only these two tables after the test has proved their upgrade contents can be
discarded; production downgrade remains an explicit operator decision.

### Public nameserver configuration

The configurable public contract contains `ns1`, `ns2`, optional IPv4/IPv6 for
each, SOA contact and default TTL. Both names may share an address, but every
show/plan/verify response declares that this is not physical redundancy.

`nameservers plan` reports settings drift and the managed zones whose apex
SOA/NS would change. `apply` persists the settings and reconciles apex SOA/NS
for the explicitly approved zone set. It never creates registrar glue, changes
delegation or modifies Cloudflare.

### Tenant-aware DNS service

The service:

- creates a zone only for a domain already registered to the active tenant;
- seeds SOA, both configured NS records and in-zone glue A/AAAA when applicable;
- lists/reads/deletes only zones owned by the active tenant;
- resolves a domain/subdomain to the longest owned zone suffix;
- returns `404` rather than ownership details for another tenant;
- validates A, AAAA, CNAME, MX, TXT, CAA, SRV and NS;
- rejects apex CNAME, CNAME coexistence, out-of-zone owners, invalid TTL and
  deletion of required apex SOA/NS through the generic RRset endpoint;
- applies complete RRsets, never individual ambiguous TXT/MX values;
- computes a SHA-256 version from canonical PowerDNS zone/RRset state;
- requires `expected_version` for every apply/delete after a plan/read;
- returns a normalized conflict when current state differs;
- serializes PanelTutor writes per ownership row and detects direct external
  PowerDNS changes by recomputing the fingerprint.

Create compensation deletes a newly created PowerDNS zone if ownership cannot
be committed. Delete is idempotent: an absent PowerDNS zone may still release
matching ownership, but never ownership of another tenant. A failed external
write never returns success. Retries with the same desired RRset are safe.

Every successful write records general audit metadata without record secret
values and publishes the declared Domains event after persistence. TXT content
is treated as potentially sensitive: audits contain owner, type and value
count/hash, never the raw TXT value.

### FastAPI surface

Register a separate thin router under the existing Domains plugin base:

```text
GET  /dns/doctor
GET  /dns/nameservers
POST /dns/nameservers/plan
POST /dns/nameservers/apply
POST /dns/nameservers/verify

GET    /dns/zones
POST   /dns/zones
GET    /dns/zones/{zone_name}
DELETE /dns/zones/{zone_name}
GET    /dns/resolve?domain=...

GET  /dns/zones/{zone_name}/rrsets
POST /dns/zones/{zone_name}/rrsets/plan
POST /dns/zones/{zone_name}/rrsets/apply
POST /dns/zones/{zone_name}/rrsets/delete

POST /dns/zones/{zone_name}/import
GET  /dns/zones/{zone_name}/export
```

Normalized errors:

- validation: `422`;
- unavailable/transport: `503`;
- timeout: `504`;
- optimistic conflict: `409`;
- missing or cross-tenant zone: `404`;
- permission failure: `403`.

### Permissions and events

Add:

```text
domains.dns.read
domains.dns.records
domains.dns.zones
domains.dns.nameservers

domains.dns.zone_created
domains.dns.zone_deleted
domains.dns.rrsets_changed
domains.dns.nameservers_changed
```

Tenant-admin defaults receive read, records and zone management. They never
receive nameserver configuration. Nameserver apply requires both
`domains.dns.nameservers` and `core.platform.admin` through
`TenantContext.is_platform`; `users.is_superadmin` is not consulted.

## SCOPE

Entra:

- reusable PowerDNS client, validation, versioning and zone-file support;
- CLI commands listed above;
- two Domains plugin models and migration;
- DNS schemas, service and router kept separate from the existing oversized
  generic `DomainsService` and router;
- plugin settings, permissions, events and registration;
- operator and plugin documentation;
- focused tests and a real CLI lab harness that starts and purges A.SPEC 0066
  through its public commands.

## OUT OF SCOPE

- Frontend or changes under `apps/web`.
- Public registrar delegation, glue creation or Cloudflare migration.
- DNSSEC, TSIG, AXFR, NOTIFY, secondary zones or catalog zones.
- A second PowerDNS instance or physical nameserver redundancy.
- Recursive DNS or PowerDNS Recursor.
- Scheduling, background reconciliation or automatic serial polling.
- Exposing the PowerDNS API on a host/public port.
- Per-record secrets classification beyond audit redaction of TXT values.
- Changes to kernel public APIs or `src/systutor`.

## CONTRACT

### Preconditions

1. A.SPEC 0066 is green and its lab can be started, verified and purged.
2. The PowerDNS API URL is reachable from the process running CLI/backend.
3. The key file exists with owner-only permissions.
4. Zone activation has an existing tenant-owned domain and valid public
   nameserver settings.

### Postconditions

1. The declared CLI tree operates against PowerDNS without shelling out and
   proves real zone/RRset create/read/update/delete behavior in the lab.
2. FastAPI uses the same `PowerDnsClient` and DNS service; no second HTTP client
   or command execution path exists.
3. Zone ownership and public settings survive process restart in PostgreSQL.
4. Every API read/write is permission checked and tenant scoped.
5. Every write is explicit, validated, version checked and auditable.
6. API key/administrative URL never appear in database rows, HTTP responses,
   audit rows, logs, CLI JSON or test evidence.
7. Existing Domains, Hosting, Apps, Traefik and certificate behavior remains
   unchanged.

The independent falsable truth is: the same tested implementation can operate
an authoritative zone through CLI and tenant-scoped FastAPI, detect concurrent
changes and prove the resulting answer against real PowerDNS.

## INVARIANTS

```yaml
invariants:
  - id: one-powerdns-client
    statement: CLI and FastAPI import the same PowerDnsClient; neither shells out or implements another PowerDNS HTTP path.
    proof: Import/AST contract tests and injected-transport tests exercise both callers.
  - id: strict-tenant-isolation
    statement: A tenant cannot discover, resolve, read, mutate or delete another tenant's zone.
    proof: API/service tests cover every zone and RRset operation with two tenants and assert 404/no foreign metadata.
  - id: host-secret-only
    statement: The API key exists only in the configured owner-only host file and is absent from persistence and all observable output.
    proof: Settings, redaction, model, audit, response and captured-output canary tests.
  - id: explicit-versioned-writes
    statement: Mutations require apply intent and expected current version; stale writes return conflict without mutation.
    proof: CLI dry-run and service/API stale-fingerprint tests, including direct fake-PowerDNS drift.
  - id: existing-domain-surfaces-unchanged
    statement: Existing domain inventory, route, certificate, Traefik publication and Hosting/Apps contracts retain their paths and behavior.
    proof: Full existing Domains backend suite and systutor-core quality gates.
  - id: no-public-pdns-api
    statement: This change does not add any host/public mapping for the PowerDNS API.
    proof: Change surface prohibits Compose changes and live harness reaches the API only from the lab container/network.
  - id: migration-reversible
    statement: Upgrade creates and downgrade removes only domains_dns_zones and domains_dns_settings.
    proof: Migration test executes upgrade, inserts rows, executes downgrade and checks all pre-existing tables remain.
```

## VERIFICATION

Run exactly from the repository root:

```bash
python3 -m py_compile scripts/spanel_dns.py vendor/systutor-core/plugins/domains/backend/dns/*.py vendor/systutor-core/plugins/domains/backend/dns_router.py vendor/systutor-core/plugins/domains/backend/dns_schemas.py
python3 -m pytest scripts/tests/test_spanel_dns.py -q
python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests/test_dns_client.py vendor/systutor-core/plugins/domains/backend/tests/test_dns_service.py vendor/systutor-core/plugins/domains/backend/tests/test_dns_router.py vendor/systutor-core/plugins/domains/backend/tests/test_dns_migration.py -q
python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
python3 scripts/spanel_dns.py --help
(cd vendor/systutor-core && ruff check plugins/domains/backend/dns plugins/domains/backend/dns_router.py plugins/domains/backend/dns_schemas.py plugins/domains/backend/tests/test_dns_client.py plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_dns_router.py plugins/domains/backend/tests/test_dns_migration.py)
(cd vendor/systutor-core && python3 -m pyright)
(cd vendor/systutor-core && python3 -m pytest tests -q)
git diff --check
```

Run the real operator proof using the active SSH configuration without
shell-sourcing or printing it:

```bash
python3 -m dotenv -f vendor/systutor-core/.env run -- bash -c '
  POWERDNS_LAB_SSH_HOST="$MAIL_SERVER_HOST" \
  POWERDNS_LAB_SSH_PORT="$MAIL_SERVER_PORT" \
  POWERDNS_LAB_SSH_USER="$MAIL_SERVER_USER" \
  POWERDNS_LAB_SSH_PASSWORD="$MAIL_SERVER_PASSWORD" \
  scripts/tests/powerdns_cli_backend_live.sh
'
```

The live proof must:

1. start a fresh A.SPEC 0066 lab and use only its private API from inside its
   container/network;
2. run CLI `doctor`;
3. create a uniquely named zone in dry-run then apply mode;
4. list/show it;
5. plan/apply A and TXT RRsets and reject a stale version;
6. export, delete and re-import the zone deterministically;
7. verify real SOA/NS/A/TXT answers by UDP and TCP `dig`;
8. delete the RRsets and zone with exact confirmation;
9. prove no API/database host port was added;
10. purge only its labelled lab resources and unique temporary files, retaining
    redacted PASS evidence.

The complete backend HTTP composition with browser/UI is deferred to A.SPEC
0069, but router tests here must prove shared-client wiring, permissions,
ownership, errors and concurrency without mocks that bypass the service.

## ROLLBACK

1. Disable DNS router registration and operator use.
2. Export every managed zone through the CLI and verify the export before any
   destructive database action.
3. Keep PowerDNS zones intact by default; removing plugin ownership must not
   imply deleting authoritative data.
4. Execute the migration downgrade only after explicit operator confirmation.
5. Remove the new permissions/events only after no role or audit consumer
   references them.

Verification executes migration downgrade against an isolated database. The
real lab lifecycle deletes only the unique test zone and labelled lab; it never
operates on public zones.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0067-powerdns-cli-backend.md
    - scripts/spanel_dns.py
    - scripts/tests/test_spanel_dns.py
    - scripts/tests/powerdns_cli_backend_live.sh
    - vendor/systutor-core/pyproject.toml
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/permissions/domains.json
    - vendor/systutor-core/plugins/domains/events/domains.json
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
    - vendor/systutor-core/plugins/domains/backend/models.py
    - vendor/systutor-core/plugins/domains/backend/settings.py
    - vendor/systutor-core/plugins/domains/backend/register.py
    - vendor/systutor-core/plugins/domains/backend/dns/**
    - vendor/systutor-core/plugins/domains/backend/dns_router.py
    - vendor/systutor-core/plugins/domains/backend/dns_schemas.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_dns_client.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_dns_service.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_dns_router.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_dns_migration.py
    - vendor/systutor-core/plugins/domains/migrations/003_create_dns_zone_ownership.py
  prohibited:
    - docker-compose.yml
    - .env
    - .env.*
    - secrets/**
    - scripts/powerdns-lab.sh
    - scripts/powerdns-lab/**
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/domains/frontend/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/apps/**
    - apps/**
    - installer/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Domains plugin DNS models, permissions, events, settings and router
    - PowerDNS authoritative zones and RRsets selected by explicit operations
    - operator CLI and DNS ownership tables
  indirect:
    - tenant-admin permission seed on plugin synchronization
    - audit log and event consumers
    - application dependency installation through plugin package metadata
  must_not_affect:
    - existing Domains inventory, routes, certificates and Traefik publication
    - Hosting and Apps domain ownership contracts
    - kernel authorization semantics and public APIs
    - PowerDNS API network exposure
    - Cloudflare zones, registrar delegation and DNSSEC
    - existing unrelated PostgreSQL tables and data
```

The correlated proofs are `existing-domain-surfaces-unchanged`, full existing
tests, the prohibited kernel/Compose/other-plugin surface,
`no-public-pdns-api`, migration downgrade tests and the absence of any
Cloudflare/registrar/DNSSEC operation.

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0066
  must_compose_with:
    - A.SPEC 0066 private API, lifecycle and labelled cleanup
    - A.SPEC 0068 frontend consumes only this FastAPI contract
    - A.SPEC 0069 proves lab plus API plus frontend plus dig as one system
  systemic_invariants:
    - PowerDNS remains RRset authority while PostgreSQL remains ownership authority.
    - Neither UI nor API ever receives the PowerDNS key or administrative URL.
    - Public migration remains manual opt-in and outside normal tests.
  composition_checks:
    - scripts/tests/powerdns_cli_backend_live.sh
    - python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
```

This is the backend capability leaf. Its real PowerDNS client/CLI truth is
proved now; browser and public-delegation composition are not claimed here.

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: PowerDNS transport, DNS policy, persistence orchestration, HTTP routing and CLI presentation remain separate cohesive responsibilities
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/backend/dns/client.py
    - vendor/systutor-core/plugins/domains/backend/dns/service.py
    - vendor/systutor-core/plugins/domains/backend/dns/validation.py
    - vendor/systutor-core/plugins/domains/backend/dns/zonefile.py
    - vendor/systutor-core/plugins/domains/backend/dns_router.py
    - vendor/systutor-core/plugins/domains/backend/dns_schemas.py
```

No DNS logic may be appended to the already oversized generic
`backend/service.py`; no new file may exceed 600 lines.

## Traceability

- Requirement: A.SPEC 0067 — operator CLI first, then tenant-safe FastAPI over
  the same PowerDNS client, ownership persistence and public NS configuration.
- owner: PanelTutor maintainers
- approver: project owner (human)
- Code: `scripts/spanel_dns.py`,
  `vendor/systutor-core/plugins/domains/backend/dns/**`, DNS router/models.
- Migration: `003_create_dns_zone_ownership.py`
- Test: focused CLI/client/service/router/migration tests and real lab harness.
- Commit: pending
- Deployment: backend capability only; public delegation remains disabled

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest and migration downgrade executed
- [ ] Composition checks passed
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
