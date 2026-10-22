# A.SPEC 0014 — Provision and operate a WordPress site without leaving orphans

> `risk: high` — this creates containers, volumes and networks on a real machine, and it deletes
> them. A bug here is not a wrong row: it is a database volume removed, a site left half-installed,
> or a container that belongs to another tenant touched. Mode: `full`. Approver required.
>
> This document absorbs the former planned 0018 and 0019. Creation and lifecycle are one contract:
> creation writes the `hosting_sites` row that lifecycle protects, and deletion must remove exactly
> the resources creation made.

## WHY

A.SPEC 0013 can adopt a WordPress that already exists. That is necessary but not the product: a
tenant administrator also needs to **create** a site from Spanel, and later stop, start, inspect and
remove it.

Creation is the first operation in this project that writes infrastructure instead of reading it.
The failure modes are concrete and expensive:

- the database container starts, the WordPress container never installs, and both stay running as
  an orphan nobody owns;
- the domain is already in `hosting_sites`, two sites answer the same host, and the second one
  silently wins;
- a delete removes a volume by name and takes another site's data with it;
- a tenant sends another tenant's `site_id` and stops a site that is not theirs;
- the generated database or administrator password ends up in `ps`, in a log, or in the audit row.

The lifecycle actions are grouped here because they share one rule: **operate only the exact
resources that belong to an owned, managed `hosting_sites` row, and never use a broad prune.**

## WHAT

**A tenant with the right permission can create a WordPress site bound to its tenant, and operate
only that site: start, stop, restart, read bounded logs and, with the platform permission, delete
it. A failed creation leaves no container, volume, network or ownership row behind.**

Falsable today, and today **fails**: there is no creation and no lifecycle endpoint.

## SCOPE

1. `plugins/hosting/plugin.json`:
   - add `hosting.sites.create`, `hosting.sites.manage`, `hosting.sites.delete`;
   - add `hosting.sites.create` and `hosting.sites.manage` to `tenant_admin_permissions`;
   - **do not** add `hosting.sites.delete` to `tenant_admin_permissions`: deleting a site is a
     platform operation, and the tenant cannot remove the row that proves ownership;
   - keep the three permissions of A.SPEC 0013.
2. `plugins/hosting/backend/settings.py`:
   - `SPANEL_HOSTING_WP_IMAGE` (default `wordpress:latest`);
   - `SPANEL_HOSTING_DB_IMAGE` (default `mariadb:11`);
   - `SPANEL_HOSTING_NETWORK` (default `proxy`; the network must exist before creation);
   - `SPANEL_HOSTING_TRAEFIK` (default `true`): add the Traefik labels for the domain;
   - `SPANEL_HOSTING_INSTALL_TIMEOUT` (default 180 s);
   - `SPANEL_HOSTING_LOG_LINES` (default 200, hard maximum 1000).
3. `plugins/hosting/backend/provisioner.py`:
   - derive a slug from the requested name and validate it against `[a-z0-9-]{3,40}`;
   - fixed names: `spanel-<slug>-wp`, `spanel-<slug>-db`, volumes `spanel-<slug>-wp` and
     `spanel-<slug>-db`;
   - preflight **before creating anything**:
     - domain not present in `hosting_sites`;
     - container names and volume names free;
     - network exists;
     - the tenant exists and is active (resolved through the core `Tenant` model);
   - generate the database name, database user, database password and WordPress administrator
     password with `secrets`; none is written to a command line;
   - create the database container with its environment from a temporary env-file or stdin, never
     from `-e PASSWORD=...` in the argv;
   - wait for the database to accept connections;
   - create the WordPress container with `WORDPRESS_DB_*` pointing at the database container;
   - install WordPress with `wp core install` through `wordpress:cli`, using the site volume and
     the site network; the administrator password enters through stdin or an env-file, not argv;
   - wait until `wp core is-installed` succeeds or the install timeout expires;
   - write the `hosting_sites` row **after** the install succeeds, with
     `tenant_id = tenant_context.current_tenant_id`;
   - label every created container and volume with `spanel.site=<slug>`,
     `spanel.managed=true` and `spanel.tenant=<tenant_id>`; the labels are for observability, not
     authorization.
4. Compensating cleanup on any failure after the first resource exists:
   - remove the WordPress container, the database container, both volumes and the network **only if
     this operation created it**;
   - remove the `hosting_sites` row if it was inserted;
   - never run `docker system prune`, `docker volume prune` or a wildcard removal;
   - the original error is reported; cleanup failures are appended, not hidden.
5. `plugins/hosting/backend/service.py`:
   - `create_site(tenant_context, name, domain, admin_email)` returns the site and the generated
     administrator password once;
   - `start_site`, `stop_site`, `restart_site`: resolve the owned site, verify `managed`, run the
     exact `docker start|stop|restart <container>`, audit;
   - `site_logs(site, lines)`: resolve the owned site, clamp `lines` to the configured maximum,
     return `docker logs --tail N`, audit is not required for reads but the access is still
     tenant-scoped;
   - `delete_site`: requires `hosting.sites.delete`, resolves the owned site, removes containers,
     volumes and the network created for that site, deletes the row, audits;
   - all write methods receive `TenantContext`, never a `tenant_id` parameter.
6. `plugins/hosting/backend/router.py`:
   - `POST /api/v1/plugins/hosting/sites`;
   - `POST /api/v1/plugins/hosting/sites/{site_id}/start`;
   - `POST /api/v1/plugins/hosting/sites/{site_id}/stop`;
   - `POST /api/v1/plugins/hosting/sites/{site_id}/restart`;
   - `GET /api/v1/plugins/hosting/sites/{site_id}/logs?lines=200`;
   - `DELETE /api/v1/plugins/hosting/sites/{site_id}`;
   - requests use `extra="forbid"`; no request contains `tenant_id`, `container`, `volume` or
     `network`;
   - the create response sets `Cache-Control: no-store` because it carries the generated
     administrator password once.
7. `plugins/hosting/frontend/`:
   - a “Crear sitio” action visible with `hosting.sites.create`;
   - start/stop/restart buttons visible with `hosting.sites.manage`, disabled when
     `managed=false`;
   - logs panel with bounded lines;
   - delete action visible only with `hosting.sites.delete`, with a typed confirmation of the site
     name.
8. Tests: live creation, lifecycle and deletion on the lab, plus pure tests for slug/name/domain
   validation and request schemas.
9. This document.

## OUT OF SCOPE

- **DNS and TLS.** The Traefik labels are written; creating the DNS record, issuing the certificate
  and verifying public reachability are deployment responsibilities, not this plugin's.
- **Backups and restore.** Deletion is irreversible by design and is documented as such.
- **WordPress core, plugin or theme updates.**
- **Multisite.**
- **Migration of a site between hosts or networks.**
- **Bulk creation.**
- **Custom compose files, resource limits, CPU/memory quotas.**
- **Host port allocation.** The site joins the configured Docker network; publishing is out of
  scope.
- **Creating WordPress users after install.** Only the initial administrator is created.
- **Tenant administrator deletion.** The delete permission is platform-only.
- **`docker system prune` and any wildcard cleanup.**
- **Changes to the extracted core or to A.SPEC 0013's read/reset endpoints**, beyond adding the new
  permissions to the manifest.

## CONTRACT

**Preconditions**

- A.SPEC 0013 is implemented: `hosting_sites`, the tenant guard and the read/reset endpoints.
- The configured Docker network exists.
- The configured images are available or can be pulled.
- The operator has `hosting.sites.create` or `hosting.sites.manage` as appropriate.

**Postconditions — creation**

- The new row's `tenant_id` equals `TenantContext.current_tenant_id`. A body cannot override it.
- The domain and the container names are unique. A duplicate returns 409 **before** creating any
  container.
- On success: the database container, the WordPress container, both volumes and the
  `hosting_sites` row exist; `wp core is-installed` is true; the site appears in the owner's list.
- The generated administrator password is returned once, with `Cache-Control: no-store`. It is not
  stored, logged, written to the audit row or included in a later response.
- On failure at any step: no container, volume, network or `hosting_sites` row created by this
  operation remains. Other sites are byte-identical.
- The operation is not retried automatically after a partial failure; the operator sees the error
  and can run it again because cleanup ran.

**Postconditions — lifecycle**

- Start/stop/restart act on exactly the `container_name` of the owned row and nowhere else.
- A site with `spanel.managed=false` returns 409 for every lifecycle action and for delete.
- Start on an already-running site and stop on an already-stopped site succeed idempotently and are
  audited.
- Logs return at most the configured number of lines, never more than 1000, and only for an owned
  site.
- Delete removes the WordPress container, the database container, both volumes and the network if
  it belongs to the site, then the row. It never removes a resource without the site's exact name
  or label. It never prunes.
- A delete of a site whose containers are already gone still removes the row and records the
  missing resources in the audit details.
- Every write records an audit row with the tenant, branch and actor from `TenantContext`.

**New falsifiable truth**

Create a site, stop it, start it, read its logs and delete it, all through the API, as the owner
tenant. Then repeat every call with another tenant's token: every call returns 404 and the target
site's `user_pass`, container state and volumes are unchanged.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      The new site's tenant comes from TenantContext. No request field can set or change it.
    proof: >
      test_creacion_usa_el_tenant_del_contexto: the request schema has no tenant_id, and the row's
      tenant_id equals the authenticated tenant's id.

  - id: I2
    statement: >
      A failed creation leaves no orphan: every resource created by the attempt is removed, and no
      resource of another site is touched.
    proof: >
      test_creacion_fallida_no_deja_huerfanos injects a failure after the database container is
      created and asserts the containers, volumes, network and row are absent, while a control
      site is byte-identical.

  - id: I3
    statement: >
      Lifecycle and delete use the exact container and volume names derived from the owned row.
      There is no wildcard, no prune and no name taken from the request.
    proof: >
      test_lifecycle_solo_toca_su_sitio and a static check that no command contains `prune` or a
      `spanel-*` wildcard.

  - id: I4
    statement: >
      spanel.managed=false blocks every lifecycle action and delete with 409, and writes nothing.
    proof: >
      test_managed_false_bloquea_start_stop_delete against a real lab container.

  - id: I5
    statement: >
      Delete removes only the owned site's resources and its row; other sites keep their
      containers, volumes and users.
    proof: >
      test_delete_no_toca_otros_sitios, with two lab sites and a before/after inventory.

  - id: I6
    statement: >
      No generated password appears in an argv, a log, an audit row or a later response.
    proof: >
      test_la_password_de_creacion_no_se_repite: capture the create response, then read logs,
      audit and the site list, and assert the sentinel password is absent.

  - id: I7
    statement: >
      Every write is audited with the tenant, branch and actor from TenantContext.
    proof: >
      test_audit_de_creacion_y_lifecycle.

  - id: I8
    statement: >
      Domain and container-name uniqueness is enforced before creation and again by the database.
    proof: >
      test_dominio_duplicado_es_409 and test_nombre_duplicado_es_409; both assert no new container
      exists after the rejection.

  - id: I9
    statement: >
      Start and stop are idempotent and do not fail when the requested state is already true.
    proof: >
      test_start_stop_idempotentes.

  - id: I10
    statement: >
      The frontend hides actions the user cannot perform and never sends a tenant identifier.
    proof: >
      test_ui_permissions (component test) and a static check that `api.ts` has no tenant_id.
```

## VERIFICATION

**Gates**

```bash
ruff check vendor/systutor-core/plugins/hosting
python3 -m pyright vendor/systutor-core/plugins/hosting
npm --prefix apps/web run build
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_lifecycle_live.py -q
```

**Live tests to add**

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_creacion_usa_el_tenant_del_contexto` | I1 | a body can choose the owner |
| `test_creacion_fallida_no_deja_huerfanos` | I2 | a failed install leaves containers or volumes |
| `test_lifecycle_solo_toca_su_sitio` | I3 | a wildcard or wrong name touches a neighbour |
| `test_managed_false_bloquea_start_stop_delete` | I4 | the label is decorative |
| `test_delete_no_toca_otros_sitios` | I5 | delete removes more than its own resources |
| `test_la_password_de_creacion_no_se_repite` | I6 | the admin password leaks or is returned twice |
| `test_audit_de_creacion_y_lifecycle` | I7 | writes are not auditable |
| `test_dominio_duplicado_es_409` | I8 | two sites share a domain |
| `test_nombre_duplicado_es_409` | I8 | the second create reuses a container name |
| `test_start_stop_idempotentes` | I9 | a repeated start fails |
| `test_otro_tenant_no_puede_operar` | I1, I5 | cross-tenant lifecycle works |

**The lab is the only place these run**

Every destructive action is limited to containers named `spanel-lab-*`. The suite refuses to run
if the target name does not match that prefix, and it inventories the demo sites before and after.
The two-tenant fixture of A.SPEC 0013 is reused.

**What cannot be proven here**

- Public DNS and TLS reachability. The labels are written; the certificate is deployment.
- A real disk-full or Docker-daemon crash in the middle of a delete. The compensating cleanup is
  tested with injected command failures, not with a killed daemon.

## ROLLBACK

Code: `git revert` of the commit. No core migration and no schema change in this A.SPEC.

State: every site created by this A.SPEC is removed by its own delete path. If the feature must be
rolled back while sites exist, **do not delete the containers**; disable the plugin, leave the
`hosting_sites` rows and the containers in place, and remove them manually only after exporting the
data. The plugin owns the naming convention, not the data.

Deletion itself has no compensation: there is no backup in this A.SPEC. The UI requires the site
name to be typed and the audit row records what was removed. That is the honest limit.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0014-hosting-provisioning-lifecycle.md
    - vendor/systutor-core/plugins/hosting/plugin.json
    - vendor/systutor-core/plugins/hosting/README.md
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py   # new
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - scripts/tests/test_hosting_lifecycle_live.py
  prohibited:
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/hosting/migrations/**   # 0013 owns the table
    - scripts/probe_docker.py
    - scripts/reset_wp_password.py
    - docker-compose.yml
    - .env
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - new containers, volumes and networks created by the provisioner
    - new lifecycle routes and UI actions
    - the hosting_sites rows created by the provisioner
  indirect:
    - the Docker host's resource usage
    - the tenant administrator role, which receives create/manage but not delete
    - the site list, which must show the new site immediately
  must_not_affect:
    - surface: sitios_existentes
      surfaces: [contenedores, volumenes, usuarios]
      invariant: I2, I5
    - surface: separacion_tenant
      surfaces: [creacion, lifecycle, logs, delete]
      invariant: I1, I5
    - surface: reset_y_listado
      surfaces: [A.SPEC 0013 endpoints]
      invariant: the 0013 live suite stays green
    - surface: credenciales
      surfaces: [argv, logs, audit, respuestas]
      invariant: I6
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013   # hosting_sites, guard, service and frontend shell
  must_compose_with:
    - 0012   # transport, discovery and credential-safe commands
    - 0007   # the created container must be detected by the same rules
  systemic_invariants:
    - Tenant identity always comes from TenantContext.
    - Every created resource is named from the owned row and labeled with its tenant.
    - Deletion never uses a wildcard or a prune.
  composition_checks:
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_api_live.py -q
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_lifecycle_live.py -q
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
```

`provisioner.py` owns naming, creation, waiting and compensating cleanup. `service.py` owns tenant
scope, permissions, audit and orchestration. The router owns HTTP only.

## Traceability

- **Requirement**: a tenant can create and operate its own WordPress sites from Spanel
- **A.SPEC**: this document
- **Code**: `vendor/systutor-core/plugins/hosting/backend/provisioner.py`,
  `service.py`, `router.py`, `frontend/**`
- **Migration**: none (the table is A.SPEC 0013's)
- **Test**: `scripts/tests/test_hosting_lifecycle_live.py`
- **Commit**: `TBD`
- **Deployment**: rebuild the image; ensure the configured Docker network and images exist
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
