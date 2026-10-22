# A.SPEC 0013 — Hosting a WordPress per tenant: sites, users and one-password reset

> `risk: high` — this adds a plugin, a table, a frontend route and a write operation. The write
> resets a real account, and the read side decides which tenant may see which site. A mistake is
> either a cross-tenant leak or a reset on somebody else's user. Mode: `full`. Approver required.
>
> This document absorbs the former planned 0015, 0016 and 0017. They are one vertical slice: the
> same `hosting_sites` row and the same `get_owned_site` guard decide the site list, the user list
> and the reset. Splitting them would duplicate the guard or leave a window where one endpoint is
> scoped and the next is not.

## WHY

The foundation of A.SPEC 0012 gives the plugin a safe core: no database secret in the argv, tenant
administrators receive the permissions declared by the manifest, and the proven Docker/WordPress
logic lives in `plugins/hosting/backend/`. None of that is reachable by a person yet.

The product question is concrete:

> A tenant administrator opens Spanel, sees the WordPress sites of **their** tenant, opens one,
> sees its users with their roles, and resets the password of exactly one user without stopping the
> site.

Today the answer is "no": there is no plugin, no site ownership, no endpoint and no screen.

### The part that is easy to get wrong

The WordPress detection of A.SPEC 0007 walks every container on the machine. That is correct for
discovery and **wrong for authorization**. A container is not owned by a tenant because it exists,
because its image says `wordpress`, or because it has a `spanel.tenant` label that anybody with
Docker access can write. Ownership has to be a deliberate row in a table, and every query has to
filter it by the tenant that the core put in `TenantContext`.

The same trap appears in the URL. If the API takes a container name and runs `docker exec` on it,
the caller can name `spanel-demowp-db` or `mailserver`. The container name must be resolved from an
owned `hosting_sites` row, never accepted as the resource selector.

### Existing sites have no owner

The three real WordPress containers (`spanel-lucas-wp`, `spanel-test-wp`, `spanel-demowp-wp`) were
created before this table existed. They cannot be assigned automatically: guessing "the only tenant
that exists" is how a staging site ends up in a customer's list. Adoption is explicit and audited.
Until a site is adopted, it is visible to nobody.

## WHAT

**A tenant can list only its own WordPress sites, list the users of one of those sites, and reset
the password of one user by ID. A request for a site of another tenant returns 404 and writes
nothing.**

Falsable today, and today **fails**: no plugin, no table, no route.

## SCOPE

1. `vendor/systutor-core/plugins/hosting/plugin.json` and the required plugin structure:
   - `id: hosting`, `name: WordPress Hosting`, `api_version: 1`;
   - permissions:
     - `hosting.sites.read` — list and view the tenant's sites;
     - `hosting.users.read` — list users of an owned site;
     - `hosting.users.password` — reset one user's password;
   - `tenant_admin_permissions`: the same three. A tenant administrator needs the whole MVP;
   - no `hosting.sites.adopt`: adoption is an operator action, not a tenant action;
   - no events in this A.SPEC. The write is audited with `record_audit`, which is the kernel
     contract; an event can be added when a consumer exists.
2. `plugins/hosting/migrations/001_create_hosting_sites.py`:
   - table `hosting_sites`:
     - `id` `String(36)` primary key;
     - `tenant_id` `String(36)` `ForeignKey("tenants.id")`, `nullable=False`, indexed;
     - `container_name` `String(255)`, `nullable=False`, **unique**;
     - `domain` `String(255)`, nullable, **unique** (PostgreSQL allows multiple NULLs, so sites
       without a domain do not collide);
     - `created_at`, `updated_at` timezone-aware;
   - `upgrade` is idempotent (`checkfirst=True`);
   - `downgrade` drops the table;
   - the migration must run on the real PostgreSQL and on the SQLite test database.
3. `plugins/hosting/backend/models.py`: the SQLAlchemy model, not registered as a kernel model.
4. `plugins/hosting/backend/settings.py`:
   - `SPANEL_DOCKER_TRANSPORT` (`ssh` or `local`);
   - `SPANEL_DOCKER_SSH_HOST`, `_USER`, `_PORT`, `_STRICT_HOST_KEY`;
   - `SPANEL_DOCKER_SSH_PASSWORD` falls back to `MAIL_SERVER_PASSWORD` for compatibility;
   - `SPANEL_HOSTING_COMMAND_TIMEOUT` (default 60 s);
   - no secret is logged; the settings object is not returned by an endpoint.
5. `plugins/hosting/backend/guard.py`:
   - `get_owned_site(site_id, tenant_context, db)` loads the row by `id` **and**
     `tenant_id == tenant_context.current_tenant_id`;
   - returns 404 when it does not exist or belongs to another tenant;
   - every site-scoped endpoint depends on it.
6. `plugins/hosting/backend/service.py`:
   - `list_sites(tenant_id)` loads only rows owned by the tenant, then asks Docker for the live
     state of those containers. It never inspects a container that is not in the list;
   - a mapped container that no longer exists is returned with `status: "missing"`, not hidden and
     not an exception;
   - `list_users(site)` verifies the container still exists and is a WordPress before running the
     database query;
   - `reset_password(site, wp_user_id, password | None)`:
     - resolves the user by **ID** only;
     - generates a password when the body does not carry one;
     - never falls back to a login;
     - writes one row, reads it back by ID, and returns the generated password once;
     - writes an audit row with `module="hosting"`, action `hosting.user.password_reset`, the
       actual tenant/branch/actor from `TenantContext`, and details that do **not** contain the
       password or the hash.
7. `plugins/hosting/backend/router.py`:
   - `GET /api/v1/plugins/hosting/sites`;
   - `GET /api/v1/plugins/hosting/sites/{site_id}/users`;
   - `POST /api/v1/plugins/hosting/sites/{site_id}/users/{wp_user_id}/password`;
   - all three declare `tenant_context: TenantContext = Depends(get_current_tenant_context)` and
     the relevant `require_permission(...)`;
   - response models use `extra="forbid"` on requests and never expose `tenant_id`;
   - the reset response sets `Cache-Control: no-store`.
8. `plugins/hosting/frontend/register.ts`, `api.ts`, `pages/HostingPage.tsx`:
   - route `/app/hosting`, navigation label `Hosting`, group `Administración`;
   - list of the tenant's sites; selecting one loads its users;
   - role shown as returned, including empty;
   - reset action opens a confirmation dialog, shows the generated password once with a copy
     button, and hides the action when `managed=false`;
   - errors are shown with the existing toast/message patterns.
9. `scripts/hosting_adopt.py` (operator CLI, not an endpoint):
   - `--container NAME --tenant SLUG [--domain DOMAIN] [--dry-run]`;
   - verifies the container exists and is a WordPress using the extracted discovery of A.SPEC 0012;
   - resolves the tenant through the core `Tenant` model, not by guessing;
   - refuses a container already in `hosting_sites` (the unique constraint is the last line of
     defence);
   - records an audit row `hosting.site.adopted` with `actor_type="system"`;
   - never starts, stops or writes to the container.
10. Tests: live API suite with two tenants, plus pure tests for request schemas and formatting.
11. This document.

## OUT OF SCOPE

- **No creation of sites.** A.SPEC 0014. This A.SPEC only adopts existing ones through a CLI.
- **No start/stop/restart/delete/logs.** A.SPEC 0014.
- **No WordPress user creation, deletion or role changes.** Only listing and one password reset.
- **No reset by login.** The login is ambiguous by database definition (A.SPEC 0011); the API only
  accepts the numeric WordPress user ID.
- **No storing of passwords.** The generated password exists in the response and nowhere else.
- **No caching of users or sites.** A cached user list after a reset is a support ticket. The site
  list may be cached later, but not in this A.SPEC.
- **No `hosting.sites.all`.** A tenant sees its own sites and nothing else. A platform-wide view is
  a separate decision and a separate permission.
- **No labels as authorization.** `spanel.tenant` may be written later for observability; it is
  never read to decide access.
- **No change to the extracted core.** If the plugin needs a new function, it goes in the plugin,
  not in `scripts/`.
- **No offline mock of Docker.** Pure tests cover parsing and formatting; the API tests touch the
  real machine.

## CONTRACT

**Preconditions**

- A.SPEC 0012 is implemented: the plugin core is importable and the manifest supports
  `tenant_admin_permissions`.
- The plugin is installed and enabled through the core plugin runtime.
- The operator has adopted at least one container into the tenant used by the test.
- The WordPress container and its database are running.

**Postconditions**

- `GET /sites` returns only rows whose `tenant_id` equals the authenticated tenant. A tenant with
  no adopted sites gets `[]`, not every WordPress on the machine.
- A site mapped to tenant A never appears in tenant B's list, even if B knows its `site_id`.
- `GET /sites/{site_id}/users` returns the WordPress users of that site with ID, login, email,
  status and role. A user with no capabilities row is present with an empty role.
- `POST /sites/{site_id}/users/{wp_user_id}/password` resets exactly one row, by ID. Two rows
  sharing a login are irrelevant because the login is not the selector.
- If the body omits `password`, the response contains a generated password once. If the body
  carries one, the response does not echo it.
- The reset response is `Cache-Control: no-store`.
- An audit row exists with the correct `tenant_id`, `branch_id`, `actor_user_id`, `entity_id`, and
  no password or hash in `details`.
- A site with `spanel.managed=false` is listed with `managed: false`; the reset endpoint returns
  409 and writes nothing.
- A missing container is returned by the list as `missing`; the users/reset endpoints return 409
  with a message naming the container.
- Disabling the plugin removes the routes and the navigation; the `hosting_sites` rows remain.
- `hosting_adopt.py --dry-run` prints the resolution and writes nothing. A second adoption of the
  same container fails with the existing owner.

**New falsifiable truth**

With two tenants, A and B, and one adopted site per tenant, A's token cannot read or reset
anything in B's site even when it supplies B's `site_id` and WordPress `user_id`; the attempt
returns 404 and leaves B's `user_pass` unchanged.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      Every site lookup filters by tenant_id in SQL, using TenantContext.current_tenant_id. No
      endpoint filters in Python after loading and no endpoint trusts a tenant_id from the client.
    proof: >
      test_listado_solo_del_tenant and a static check that every HostingSite query in the router
      and service contains `tenant_id`.

  - id: I2
    statement: >
      A resource of another tenant is indistinguishable from a missing resource: 404, not 403.
    proof: >
      test_site_de_otro_tenant_es_404 and test_usuario_de_otro_tenant_es_404, both against the
      live API with two real tenants.

  - id: I3
    statement: >
      The container name is never accepted from the client. It is read from the owned
      hosting_sites row.
    proof: >
      test_la_api_no_acepta_container_name: the request schemas have no such field and
      `extra="forbid"` rejects it; the service receives a HostingSite, not a string.

  - id: I4
    statement: >
      The reset selects one row by WordPress ID, never by login, and verifies the read-back by ID.
    proof: >
      test_reset_por_id_una_sola_fila, creating two rows with the same login in the lab and
      asserting only the selected ID changed.

  - id: I5
    statement: >
      No password or hash is written to logs, audit details, list responses or error messages. The
      generated password appears only in the reset response, with no-store.
    proof: >
      test_la_password_no_aparece_en_audit_ni_en_logs and test_listado_no_devuelve_hashes; a live
      reset with a sentinel password, then a search of the audit row and the captured logs.

  - id: I6
    statement: >
      A site with spanel.managed=false is visible but not operable; the write returns 409 and
      writes nothing.
    proof: >
      test_managed_false_no_se_puede_resetear against a real lab container.

  - id: I7
    statement: >
      The audit row carries the tenant, branch and actor from TenantContext, and the kernel's
      resolve_tenant_scope accepts them.
    proof: >
      test_audit_del_reset_tiene_el_tenant_del_contexto.

  - id: I8
    statement: >
      Adoption is explicit, audited and idempotent by refusal: a container already in
      hosting_sites cannot be adopted by another tenant.
    proof: >
      test_adopcion_duplicada_falla and test_adopcion_dry_run_no_escribe.

  - id: I9
    statement: >
      Disabling the plugin hides the API and the navigation but preserves the ownership rows.
    proof: >
      test_plugin_deshabilitado_no_expone_rutas; the rows are counted before and after.

  - id: I10
    statement: >
      The migration is reversible: upgrade creates the table, downgrade removes it, and a second
      upgrade restores it.
    proof: >
      test_migracion_up_down_up on the real database, executed in VERIFICATION (not prose).
```

## VERIFICATION

**Gates**

```bash
ruff check vendor/systutor-core/plugins/hosting scripts/
python3 -m pyright vendor/systutor-core/plugins/hosting scripts/
npm --prefix apps/web run build
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_api_live.py -q
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_adopt_live.py -q
python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q   # pure only
```

**Live tests to add**

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_listado_solo_del_tenant` | I1 | the list leaks another tenant's sites |
| `test_site_de_otro_tenant_es_404` | I2 | a 403 confirms the resource exists |
| `test_usuario_de_otro_tenant_es_404` | I2 | the user endpoint ignores tenant scope |
| `test_la_api_no_acepta_container_name` | I3 | the caller can select a container directly |
| `test_reset_por_id_una_sola_fila` | I4 | a duplicate login updates two rows |
| `test_la_password_no_aparece_en_audit_ni_en_logs` | I5 | the new password or hash leaks |
| `test_managed_false_no_se_puede_resetear` | I6 | `spanel.managed=false` is decorative |
| `test_audit_del_reset_tiene_el_tenant_del_contexto` | I7 | the audit lands in the wrong tenant |
| `test_adopcion_duplicada_falla` | I8 | two tenants own one container |
| `test_adopcion_dry_run_no_escribe` | I8 | dry-run writes |
| `test_plugin_deshabilitado_no_expone_rutas` | I9 | disabling only hides the menu |
| `test_migracion_up_down_up` | I10 | the downgrade is untested prose |

**The two-tenant fixture**

The live suite creates tenant B through the real tenant plugin, logs in as its administrator, and
adopts a lab site to B. It does not use a mocked `TenantContext`: the point is that the core builds
the context from the real user and role. Tenant A uses the demo tenant and `spanel-demowp-wp` or a
lab site. Every destructive assertion runs on `spanel-lab-*` only; the demo site is read-only for
this suite.

**What cannot be proven without A.SPEC 0014**

- Creating a site and verifying that its first `hosting_sites` row is born with the right tenant.
  This A.SPEC proves adoption, which is the other half.
- Deleting a site and checking that only its owned resources disappear.

Both are named as 0014 work, not claimed here.

## ROLLBACK

Preferred: disable the plugin through the core plugin screen. Routes and navigation disappear; the
`hosting_sites` rows remain, so re-enabling restores ownership.

Full rollback: run the plugin migration downgrade, which drops `hosting_sites`, then remove the
plugin directory. **This loses ownership data**: until the sites are adopted again, they are
invisible. Docker containers and volumes are untouched. This is a deliberate, bounded loss of
metadata, not of sites.

`git revert` of the single commit restores the code. If the migration was applied, the revert must
be followed by the downgrade command; the commit cannot undo a table by itself.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0013-hosting-sites-users-reset.md
    - vendor/systutor-core/plugins/hosting/plugin.json
    - vendor/systutor-core/plugins/hosting/README.md
    - vendor/systutor-core/plugins/hosting/permissions/**
    - vendor/systutor-core/plugins/hosting/events/**
    - vendor/systutor-core/plugins/hosting/migrations/**
    - vendor/systutor-core/plugins/hosting/backend/**
    - vendor/systutor-core/plugins/hosting/frontend/**
    - scripts/hosting_adopt.py
    - scripts/tests/test_hosting_api_live.py
    - scripts/tests/test_hosting_adopt_live.py
    - apps/web/src/features/plugins/runtime.tsx        # only if the glob needs no change: expected none
  prohibited:
    - vendor/systutor-core/src/systutor/**               # core contract was closed in 0012
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - scripts/probe_docker.py
    - scripts/reset_wp_password.py
    - docker-compose.yml
    - .env
    - .env.docker
    - vendor/systutor-core/plugins/hosting/backend/tests/fixtures/**  # no invented server output
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - the new hosting_sites table
    - the new /api/v1/plugins/hosting/* routes
    - the new /app/hosting frontend route
    - the ownership rows created by the adoption CLI
  indirect:
    - plugin runtime: one more plugin to install, enable and migrate
    - tenant admin roles: they receive the three declared permissions on the next boot
    - the plugin navigation for users without hosting.sites.read
  must_not_affect:
    - surface: reset_existente_por_cli
      surfaces: [scripts/reset_wp_password.py]
      invariant: A.SPEC 0011/0012 live suite stays green
    - surface: deteccion_wordpress
      surfaces: [plugins/hosting/backend/discovery.py]
      invariant: A.SPEC 0007's 12 cases stay green
    - surface: otros_tenants
      surfaces: [list, users, reset]
      invariant: I1, I2
    - surface: mail
      surfaces: [plugin mail, permisos]
      invariant: mail routes and permissions are unchanged
    - surface: core
      surfaces: [tenant context, permission checks, audit]
      invariant: no core file is modified by this A.SPEC
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0012   # core, extraction and tenant_admin_permissions
  must_compose_with:
    - 0007   # detection cases consumed by the site list
    - 0008   # listing query and role parser
    - 0011   # reset targets exactly one row
    - 0014   # creation must write the same ownership row
  systemic_invariants:
    - Tenant identity always comes from TenantContext; never from a request field.
    - A container is operable only through an owned hosting_sites row.
    - The reset is by ID, never by login.
    - The generated password is returned once and stored nowhere.
  composition_checks:
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_api_live.py -q
    - python3 -m pytest scripts/tests/test_probe_docker.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/
    - vendor/systutor-core/plugins/hosting/frontend/
```

The router depends on the service; the service depends on the extracted core and on
`HostingSite`. The router must not build SQL or Docker arguments. The frontend must not decide
tenant scope; it only renders what the API returns.

## Traceability

- **Requirement**: a tenant administrator manages the WordPress sites of their tenant from Spanel
- **A.SPEC**: this document
- **Code**: `vendor/systutor-core/plugins/hosting/**`, `scripts/hosting_adopt.py`
- **Migration**: `001_create_hosting_sites.py`
- **Test**: `scripts/tests/test_hosting_api_live.py`, `scripts/tests/test_hosting_adopt_live.py`
- **Commit**: `TBD`
- **Deployment**: install and enable the plugin; run migrations; adopt existing sites by CLI
- **owner**: `TBD`
- **approver**: `luc444s` — explicit review required before IMPLEMENT because `risk: high`

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
