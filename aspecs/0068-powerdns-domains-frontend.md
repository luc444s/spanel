# A.SPEC 0068 — Integrate DNS zone management into the Domains frontend

> `risk: medium` — adds a tenant-scoped DNS interface to an existing, working
> Domains page. It mutates authoritative records but only through the audited
> A.SPEC 0067 API. The PowerDNS key and administrative URL never reach the
> browser.

## WHY

A.SPEC 0067 delivers a tested PowerDNS client, an operator CLI and a
tenant-scoped FastAPI surface. None of it is usable by a tenant administrator:
the Domains page only shows web domains, certificates and Traefik routes.

The DNS capability must be reachable where the user already manages domains,
without a new navigation section, without a second HTTP path and without
duplicating PowerDNS semantics in the browser. The frontend must preserve the
plan/apply and optimistic-version contract of the backend: a UI that writes
records without a visible plan or that hides version conflicts would make the
backend guarantees cosmetic.

## WHAT

Add a DNS capability to the existing Domains page:

- a global **Nameservers** action next to the header actions;
- a DNS status column with one state per domain;
- no per-row DNS button: double click or `Enter` on an owned domain row opens
  the DNS zone interface;
- an English header hint: **Double-click a domain to access its DNS zone.**

All DNS UI lives in a new `frontend/dns/` package. `DomainsPage.tsx` only gains
the column, the hint, the row interaction, the dialog mounts and the dialog
state.

The visual contract, including every wireframe, lives in
`docs/0068-domains-dns-frontend-ascii.md`. The implemented labels must match
that document.

### Domains page integration

Header, right side:

```text
Domains                                    [ Nameservers ]   [ Add domain ]
Double-click a domain to access its DNS zone.
```

Rules:

- the hint is rendered when the user has `domains.dns.read`;
- the hint is a translation key whose English source is exactly
  `Double-click a domain to access its DNS zone.`;
- **Nameservers** is rendered when the user has `domains.dns.read`;
- **Add domain** keeps its current permission and behavior.

New DNS column:

```text
DNS
● Managed
○ External
◌ No zone
⚠ Error
```

- `Managed`: the domain equals an owned zone or is inside one; the row opens
  the managed-zone dialog.
- `External`: no owned zone matches the longest suffix; the row opens the
  unmanaged-zone dialog (read-only explanation plus optional activation).
- `No zone` is only a presentation of `External` before the first zone exists;
  the dialog decides whether activation is possible.
- `Error`: the zone list could not be read; the row still opens a dialog with
  a retry action.

The status is computed from **one** `GET /dns/zones` query per page load and a
pure longest-suffix resolver. The frontend must not issue one request per row
and must not infer management from PowerDNS account text.

Row interaction:

- `onDoubleClick` on the row opens the dialog;
- `Enter` opens the dialog when the row itself has focus;
- the row is focusable and exposes an accessible label describing the action;
- double clicks and key presses that originate inside a button, link, input,
  select or textarea do not open the dialog;
- only rows owned by the active tenant are interactive. Platform users viewing
  another tenant through `?scope=all` keep those rows non-interactive, because
  the DNS API is scoped to the active tenant and must answer `404` for foreign
  zones;
- existing **Link** and **Remove** actions keep their behavior and do not open
  the DNS dialog.

### Zone dialog

Use the shared `Dialog` with `maxWidthClassName="max-w-[800px]"`, full width on
small screens and a scrollable body. Title is the domain. A subtitle shows the
zone name and current version when the zone is managed.

Unmanaged state:

- explains that the domain is not authoritative in PanelTutor and that its
  records live in an external DNS;
- shows **Activate DNS zone** only when the user has `domains.dns.zones`, the
  row is tenant-owned, and the domain is eligible;
- if public nameserver settings are missing, the backend returns `422`; the UI
  must show a blocking callout explaining that a platform administrator must
  configure nameservers first, and must not retry silently;
- activation is confirmed in a dialog that names the zone and states that SOA,
  NS and glue records will be created;
- activation sends `POST /dns/zones` with `{ "zone_name": "<domain>" }`.

Managed state uses two tabs, `Records` and `Zone`.

#### Records tab

- table columns: name, type, TTL, values, actions;
- search by name, type or value;
- filter by record type using only types present in the zone;
- row actions **Edit** and **Delete**;
- apex `SOA` and apex `NS` are read-only and show a lock; generic deletion of
  them must never be offered;
- delete uses `ConfirmDialog` naming owner and type, then
  `POST /rrsets/delete` with the latest `expected_version`;
- **New RRset** opens the structured editor.

The editor is structured per complete RRset, never per individual value:

- fields: name (relative to the zone or `@`), type, TTL and a list of values
  with add/remove controls;
- supported types: `A`, `AAAA`, `CNAME`, `MX`, `TXT`, `CAA`, `SRV`, `NS`;
- client validation is limited to shape: required name, supported type,
  at least one value, TTL in `1..604800`, `CNAME` with exactly one value,
  `MX` as `priority host`, `SRV` as `priority weight port target`; the backend
  remains authoritative for DNS semantics;
- **Plan** calls `POST /rrsets/plan` and renders the returned `changes` as the
  preview; the client must not invent a diff;
- **Apply** is disabled until a plan exists, sends that plan's `version` as
  `expected_version`, and refreshes the zone on success;
- a `409` renders a conflict panel with the seen and current versions, a
  **Reload zone** action and no automatic retry;
- `422` renders field-level errors next to the editor;
- `503`/`504` render an unavailable banner with retry and never report success.

TXT values are shown in the editor because the operator needs to edit them, but
toasts, audit-facing UI text and error messages never echo them.

#### Zone tab

- **Export**: authenticated text request to
  `GET /dns/zones/{zone}/export`, downloaded as `<zone>.zone`. Export must not
  go through the JSON request helper; `apiRequest` returns `null` for
  `text/plain` successful responses.
- **Import**: file input or pasted textarea, explicit confirmation, and
  `POST /import` with the current `expected_version`. `$INCLUDE`, paths and
  unsupported directives remain rejected by the backend; the UI shows the
  backend error without rewriting it into a success.
- **Delete zone**: `ConfirmDialog` that requires typing the exact zone name,
  then `DELETE /dns/zones/{zone}?expected_version=...`. On success the dialog
  closes and the zone list is invalidated.

### Nameservers dialog

Global action, available with `domains.dns.read`:

- show `ns1`/`ns2`, optional IPv4/IPv6 for each, SOA contact, default TTL and
  settings version;
- always show the warning that `ns1` and `ns2` may share one address and that
  this is **not physical redundancy**;
- **Verify** asks for zone, resolver and port and calls
  `POST /dns/nameservers/verify`; render the UDP and TCP sections, the
  authoritative flag and the SOA/NS answers; a failure is an error, never a
  partial success;
- **Configure** is rendered only with both `domains.dns.nameservers` and
  `core.platform.admin`.

Configuration flow:

1. edit the settings form;
2. **Plan** calls `POST /dns/nameservers/plan` with the desired settings and an
   explicit list of owned zones;
3. render per-zone drift, per-zone current version, and the settings field
   changes;
4. approve zones with checkboxes, defaulting checked only where drift exists;
5. **Apply** sends only the approved zone names, their versions in
   `zone_versions`, the current settings version as `expected_version`, and the
   settings;
6. an explicitly empty approved set is valid for first-time settings bootstrap;
   the UI must state that no zone will be reconciled, and must never send an
   omitted zone list as a shortcut for "all zones";
7. a `409` shows the stale zone/settings and offers a fresh plan; nothing is
   applied.

The dialog never offers registrar delegation, glue creation, DNSSEC or
Cloudflare actions.

### Permissions

| UI capability | Permission |
| --- | --- |
| Hint, DNS column, open dialog, read records, verify | `domains.dns.read` |
| Activate and delete zone | `domains.dns.zones` |
| Plan/apply/delete RRsets and import | `domains.dns.records` |
| Configure and apply nameservers | `domains.dns.nameservers` **and** `core.platform.admin` |

Without a permission the UI is read-only and does not send the mutation. Hidden
buttons are not the enforcement: the backend rejects too, and the UI must
surface `403` clearly.

### i18n

English is the source language. Every new user-visible string is an English
key translated in `apps/web/src/i18n/es/domains.ts`. The header hint is exactly:

```text
Double-click a domain to access its DNS zone.
```

Spanish:

```text
Haz doble clic en un dominio para acceder a su zona DNS.
```

No hardcoded English or Spanish text inside the new components.

## SCOPE

Entra:

- `frontend/dns/` types, API calls, pure logic, status cell, zone dialog,
  records tab, RRset editor, zone tab and nameservers dialog;
- DNS column, English hint, double-click/Enter interaction and dialog mounts in
  `pages/DomainsPage.tsx`;
- Spanish translations in `apps/web/src/i18n/es/domains.ts`;
- vitest coverage for pure logic, query keys and API contract;
- operator/UI documentation and the ASCII annex.

## OUT OF SCOPE

- Any backend, PowerDNS client, service, router, schema, model or migration
  change.
- New API endpoints or changes to A.SPEC 0067 payloads.
- A new navigation entry or a standalone DNS page.
- Registrar delegation, glue creation, DNSSEC, TSIG, AXFR, NOTIFY, Cloudflare.
- The full browser E2E harness; that is A.SPEC 0069.
- Changes to `vendor/systutor-shell`, shared UI primitives or the global app
  shell.
- Changes to `register.ts`, `plugin.json`, permissions or events manifests.

## CONTRACT

### Preconditions

1. A.SPEC 0067 is implemented and its backend tests are green.
2. The browser session has a valid token and the Domains plugin is enabled.
3. Public nameserver settings are configured before the first activation.

### Postconditions

1. A tenant user sees a DNS state per domain and opens a zone with double
   click or `Enter`, with no per-row DNS button.
2. The header shows the English hint.
3. A managed zone supports read, search, filter, plan/apply, delete, export and
   import through A.SPEC 0067 endpoints.
4. A platform administrator can inspect, plan, apply and verify nameservers;
   a tenant administrator cannot.
5. Version conflicts are visible and never mutate.
6. No PowerDNS key, administrative URL or raw TXT value appears in UI output
   or toasts.
7. Existing Domains behavior, routes, certificates and permissions are
   unchanged.

The independent falsable truth is: a tenant can open a domain by double click,
see the authoritative records, change one RRset through a visible plan and read
the result back, while a stale tab is rejected with a conflict and no write.

## INVARIANTS

```yaml
invariants:
  - id: no-dns-button
    statement: The Domains table has no per-row DNS action button; only double click or Enter on an owned row opens the zone dialog.
    proof: Pure interaction helper tests and a component contract test assert the row opens on double click/Enter and that no DNS action is rendered.
  - id: one-dns-query
    statement: DNS status is derived from one zone-list query and a pure longest-suffix resolver, not one request per row.
    proof: API-key and resolver tests; the page issues a single useQuery for zones.
  - id: backend-is-the-only-writer
    statement: The browser only calls /api/v1/plugins/domains/dns endpoints; it never calls PowerDNS or implements its own transport.
    proof: Static source check plus API module tests.
  - id: explicit-plan-and-version
    statement: Every RRset mutation requires a visible plan and the plan's version; stale writes show 409 and change nothing.
    proof: Logic tests for plan gating and conflict mapping; API tests assert expected_version is sent.
  - id: tenant-scope
    statement: Foreign-tenant rows never open a DNS dialog and every DNS request uses the active tenant context.
    proof: Interaction tests for ownership and API tests for URL construction.
  - id: nameserver-double-gate
    statement: Nameserver configuration is rendered and callable only with domains.dns.nameservers and core.platform.admin.
    proof: Permission helper tests for both gates.
  - id: no-secrets-in-ui
    statement: No API key, administrative URL or raw TXT value is written into DOM, toast, error or test evidence.
    proof: Redaction helpers and source review; TXT is only rendered in the editor table.
  - id: existing-domains-unchanged
    statement: Add/link/remove domain, certificate, route and navigation behavior keep working with their current permissions.
    proof: Existing apps/web test suite and build; the change is additive in DomainsPage.
```

## VERIFICATION

Run exactly from the repository root:

```bash
cd apps/web && npm test
cd apps/web && npm run build
cd ../..
python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
python3 -m pytest scripts/tests/test_spanel_dns.py -q
git diff --check
```

Manual live smoke (not part of the automated gate, recorded for the operator):

1. start the A.SPEC 0066 lab and configure `ns1`/`ns2` through the CLI;
2. open Domains as a tenant administrator;
3. confirm the English hint is visible and there is no DNS button;
4. double click a registered domain and activate its zone;
5. open **Records**, plan and apply an `A` and a `TXT` RRset;
6. open a second tab, change the zone, and confirm the stale tab receives a
   conflict and writes nothing;
7. export the zone, delete the zone with the exact-name confirmation, and
   re-import the export;
8. verify UDP and TCP from the nameservers dialog;
9. confirm a tenant administrator cannot see **Configure**.

A.SPEC 0069 turns this checklist into a real browser E2E.

## ROLLBACK

1. Remove the DNS column, hint, interactions and dialog mounts from
   `DomainsPage.tsx`.
2. Leave the `frontend/dns/` package unreferenced; it has no runtime side
   effects.
3. Revert the Spanish catalog additions.
4. Do not delete zones, records or ownership data as part of a UI rollback.
5. A.SPEC 0067 remains fully operational through the CLI and API.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0068-powerdns-domains-frontend.md
    - docs/0068-domains-dns-frontend-ascii.md
    - vendor/systutor-core/plugins/domains/frontend/dns/**
    - vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
    - apps/web/src/i18n/es/domains.ts
    - apps/web/src/plugins/domains-dns.logic.test.ts
    - apps/web/src/plugins/domains-dns.api.test.ts
  prohibited:
    - vendor/systutor-core/plugins/domains/backend/**
    - vendor/systutor-core/plugins/domains/migrations/**
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/permissions/**
    - vendor/systutor-core/plugins/domains/events/**
    - vendor/systutor-core/plugins/domains/frontend/register.ts
    - vendor/systutor-core/plugins/domains/frontend/api.ts
    - vendor/systutor-shell/**
    - apps/web/src/i18n/es.ts
    - apps/web/src/app/**
    - apps/web/src/features/**
    - apps/web/src/plugins/apps*.test.ts
    - scripts/**
    - docker-compose.yml
    - .env
    - .env.*
    - secrets/**
    - installer/**
    - vendor/systutor-core/src/systutor/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Domains page rendering and row interaction
    - new DNS dialogs and their API calls
    - Spanish translation catalog for Domains
  indirect:
    - tenant administrators gain a UI path to zones already authorized by A.SPEC 0067
    - browser query cache for DNS zones and zone contents
  must_not_affect:
    - existing add/link/remove domain flows
    - certificate verification cell
    - Traefik route management
    - backend DNS authorization, audit and versioning
    - PowerDNS API exposure
    - other plugins or the shell
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0066
    - A.SPEC 0067
  must_compose_with:
    - A.SPEC 0067 permissions, payloads and optimistic versions
    - A.SPEC 0069 browser E2E
  systemic_invariants:
    - PowerDNS remains the RRset authority; PostgreSQL remains the ownership authority.
    - The browser never receives the PowerDNS key or administrative URL.
    - Delegation and DNSSEC remain out of the normal flow.
  composition_checks:
    - cd apps/web && npm test
    - cd apps/web && npm run build
    - python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: DNS presentation, interaction policy, transport calls and pure helpers remain separate from the existing Domains table logic
  pages_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/frontend/dns/logic.ts
    - vendor/systutor-core/plugins/domains/frontend/dns/api.ts
    - vendor/systutor-core/plugins/domains/frontend/dns/DnsZoneDialog.tsx
    - vendor/systutor-core/plugins/domains/frontend/dns/RecordsTab.tsx
    - vendor/systutor-core/plugins/domains/frontend/dns/RrsetEditor.tsx
    - vendor/systutor-core/plugins/domains/frontend/dns/ZoneTab.tsx
    - vendor/systutor-core/plugins/domains/frontend/dns/NameserversDialog.tsx
```

`DomainsPage.tsx` must not absorb DNS logic and must remain below 600 lines. No
new file may exceed 600 lines; files above 400 lines require extraction before
closing the A.SPEC.

## Traceability

- Requirement: A.SPEC 0068 — integrated Domains DNS frontend with nameservers,
  status, double-click access, structured RRset editing and version conflicts.
- owner: PanelTutor maintainers
- approver: project owner (human)
- Code: `frontend/dns/**`, `pages/DomainsPage.tsx`, Spanish catalog.
- Wireframes: `docs/0068-domains-dns-frontend-ascii.md`
- Test: `apps/web/src/plugins/domains-dns.logic.test.ts`,
  `apps/web/src/plugins/domains-dns.api.test.ts`, `npm test`, `npm run build`.
- Commit: pending
- Deployment: tenant UI for the DNS capability delivered in A.SPEC 0067

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest
- [ ] Composition checks passed
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
