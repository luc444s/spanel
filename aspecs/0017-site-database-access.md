# A.SPEC 0017 — Open phpMyAdmin at a site's WordPress database

> `risk: high` — the route returns a database credential. It is the site's own
> credential, on the site's own isolated MariaDB container, but it is still a
> secret in an HTTP response: `no-store`, audited, and never put in a URL.

## WHY

Each site already has its own MariaDB container (`spanel-<slug>-db`) and its own
scoped SQL user. Reaching that database today means SSH into the Docker host,
reading `wp.env` and running a client by hand. That is the only missing piece:
a button in the site dialog that reveals the site's own database credential and
opens phpMyAdmin at that container.

No tenant-wide user, no new grants, no operator CLI. The database identity is
the one WordPress is already using.

## WHAT

1. `POST /api/v1/plugins/hosting/sites/{site_id}/database` returns the site's
   database host, name, user, password and the phpMyAdmin URL, with `no-store`.
2. A **Base de datos** button in the site detail dialog, next to `Ver usuarios`,
   `Logs` and `Configuración`, opens a dialog with those values and an
   `Abrir phpMyAdmin` action.
3. `SPANEL_PHPMYADMIN_URL` points at the existing phpMyAdmin instance. It may
   contain `{server}`; without it the URL is used as-is, which is what a fixed
   `PMA_HOST` deployment needs.
4. No new plugin, no sidebar entry, no iframe, no auto-login, no new permission.

## SCOPE

1. `plugins/hosting/backend/settings.py`: `phpmyadmin_url`.
2. `plugins/hosting/backend/schemas.py`: `SiteDatabaseResponse`.
3. `plugins/hosting/backend/service.py`:
   - `database_credentials(info)` (pure, testable);
   - `phpmyadmin_url(base, host)`;
   - `HostingService.site_database(site, tenant_context)`, which inspects the
     owned site, validates the binding, checks the database container is
     running, builds the URL and audits.
4. `plugins/hosting/backend/router.py`: the new route, `hosting.sites.manage`.
5. `plugins/hosting/frontend/api.ts`: type and call.
6. `plugins/hosting/frontend/components/DatabaseDialog.tsx` (new).
7. `plugins/hosting/frontend/pages/HostingPage.tsx`: button, mutation and dialog.
8. `plugins/hosting/backend/tests/test_database_access.py` (new).
9. `.env.docker.example` and `plugins/hosting/README.md`.
10. This document.

## OUT OF SCOPE

- A tenant-wide SQL user or grants across sites. Each site keeps its own user.
- The MariaDB root password. The WordPress user is enough for phpMyAdmin.
- Creating, deleting or migrating databases; changing WordPress configuration.
- Deploying phpMyAdmin or configuring Traefik.
- Auto-login, `signon`, one-time tickets or passwords in the URL.
- A phpMyAdmin embedded in the panel.
- New permissions, migrations, events or tables.

## ACCESS MODEL

- The button is rendered only with `hosting.sites.manage`; the route requires it.
- The site is resolved by `get_owned_site`, so another tenant's site is a 404,
  identical to a missing one.
- The credential is the site's existing `WORDPRESS_DB_USER`. It has privileges
  only on `WORDPRESS_DB_NAME` inside that site's container; it is not a tenant
  credential and it cannot reach another site's database.
- A site whose container is missing, stopped or not WordPress shows a controlled
  error and no credential.

## CONTRACT

| Method | Path | Permission | Result |
|---|---|---|---|
| `POST` | `/api/v1/plugins/hosting/sites/{site_id}/database` | `hosting.sites.manage` | `{host, database, user, password, url}`, `no-store` |

- `host` is read from `WORDPRESS_DB_HOST` inside the site's container, never
  from the client. It must be a Docker container name; a scheme, `localhost` or
  an IP is 409 and reveals nothing. A shared MariaDB (`spanel-lab-mariadb`) and
  a per-site one (`spanel-<slug>-db`) are both accepted.
- `database`, `user` and `password` come from `WORDPRESS_DB_NAME`,
  `WORDPRESS_DB_USER` and `WORDPRESS_DB_PASSWORD` of the same container.
- `url` is `SPANEL_PHPMYADMIN_URL`. A `{server}` placeholder is replaced with
  the database host; without the placeholder the URL is used as-is, which is
  what a fixed `PMA_HOST` deployment needs. `null` when the variable is empty
  or is not an `http(s)` URL.
- 409 when the WordPress container is missing or not WordPress, when any of the
  four database variables is absent, when the host is not a container name, or
  when the database container is not running.
- The response is audited as `hosting.site.database_access` with the site, the
  database and the host. The password is never written to the audit row, a log
  line or an error message.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: A tenant only reaches the database of its own site.
    proof: >
      The route depends on the hosting ownership guard; the host and the
      credential come from the owned site's container environment, not from the
      request body or the query string. The database keys are not editable
      (A.SPEC 0015), so a tenant cannot point its site somewhere else.

  - id: I2
    statement: The credential is the site's own WordPress user and nothing new is created.
    proof: >
      The values come from the container environment; there is no CREATE USER,
      no GRANT and no migration in this spec. A shared MariaDB is fine: the
      user is scoped to the site's database, not to the tenant.

  - id: I3
    statement: The password only appears in the explicit response.
    proof: >
      The response carries no-store and Pragma no-cache; the audit row holds the
      database and host only; the URL never contains a credential.

  - id: I4
    statement: No sidebar entry, route or iframe is added.
    proof: >
      Only the hosting site dialog changes; phpMyAdmin opens in a new tab.

  - id: I5
    statement: No new permission is created.
    proof: >
      The route uses hosting.sites.manage and the button is rendered only with
      it; a read-only user gets 403 from the route and sees no button.

  - id: I6
    statement: The plugin does not manage phpMyAdmin.
    proof: >
      It only builds a URL from SPANEL_PHPMYADMIN_URL; deployment, the fixed
      server and the network stay infrastructure.
```

## VERIFICATION

**Unit**

- The binding reads the four values from the environment and rejects a missing
  one.
- The binding rejects `localhost`, an IP, a scheme and a host with a path, and
  accepts both a shared and a per-site database container.
- The URL builder: empty or non-http base returns `null`; a fixed-server URL is
  returned as-is; `{server}` is replaced and URL-encoded.
- No error message contains the password.
- `npm --prefix apps/web run build`.

**Live, against the lab**

- `spanel-demowp-wp` shows **Base de datos** in the site detail.
- The dialog shows the site's `wp_<hex>` user and database; phpMyAdmin opens and
  the login with that credential lists exactly that one database.
- Another tenant's site id returns 404.
- A `hosting.sites.read` user gets 403 from the route and sees no button.
- A stopped database container returns 409 with no credential.
- The audit row exists and does not contain the password.

## ROLLBACK

Revert the change. No migration, no new user, no new grant, no data change. The
button and the dialog disappear; phpMyAdmin, MariaDB and the sites are
untouched.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0017-site-database-access.md
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_database_access.py
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/components/DatabaseDialog.tsx
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/README.md
    - .env.docker.example
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/hosting/migrations/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/src/systutor/**
    - apps/web/src/**
    - docker-compose.yml
    - .env
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - the site detail dialog gains one button
    - the new database dialog
    - one inspect of the WordPress container and one of its database container
  indirect:
    - one audit row per reveal
  must_not_affect:
    - surface: wordpress
      invariant: no container is recreated or reconfigured
    - surface: mariadb_data
      invariant: no SQL statement is executed; no DDL and no data change
    - surface: other_tenants
      invariant: the host always belongs to the owned site
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013   # site list and ownership
    - 0014   # site lifecycle and managed label
    - 0015   # config adapter and secret policy
    - 0016   # UI conventions and read-only gating
  must_compose_with:
    - phpMyAdmin reachable from the site's database container (Docker network)
  composition_checks:
    - npm --prefix apps/web run build
    - python -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/frontend/components/DatabaseDialog.tsx
```

`router.py` validates the permission and sets the headers. `service.py` owns the
binding, the validation, the URL and the audit. The dialog only renders what the
API returned.

## Traceability

- **Requirement**: a user who can manage a site opens **Base de datos** from that
  site and reaches phpMyAdmin at the site's own WordPress database.
- **A.SPEC**: this document
- **Code**: `plugins/hosting/backend/{settings,schemas,service,router}.py`,
  `plugins/hosting/frontend/{api.ts,components/DatabaseDialog.tsx,pages/HostingPage.tsx}`
- **Migration**: none
- **Test**: `plugins/hosting/backend/tests/test_database_access.py`, web build
- **Commit**: `TBD`
- **Deployment**: enable `hosting`, set `SPANEL_PHPMYADMIN_URL` (optional, only
  for the link), rebuild the image. No migration and no SQL.
- **owner**: `TBD`
- **approver**: `luc444s`

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
