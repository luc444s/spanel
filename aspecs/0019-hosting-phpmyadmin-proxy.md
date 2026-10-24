# A.SPEC 0019 — Embed phpMyAdmin through an authenticated internal reverse proxy

> `risk: high` — the proxy reaches a database console with a database
> credential. The credential is the site's own WordPress user, but the proxy
> still has to keep it out of URLs, iframes, logs and caches, and must not let a
> tenant reach another site's database. Mode: `full`. Approver required.

## WHY

A.SPEC 0017 reveals the site's database credential and opens phpMyAdmin in a
new tab with `SPANEL_PHPMYADMIN_URL`. That link is built for a browser and is
often `http://127.0.0.1:8081/`: on another machine it points at the user's own
laptop, not at the panel's phpMyAdmin. Exposing phpMyAdmin on a public port or
giving each site a subdomain is explicitly not wanted.

The panel cannot simply put the existing link in an iframe:

- the panel authenticates with a Bearer token in `localStorage`; an iframe does
  not send that header;
- phpMyAdmin answers with `X-Frame-Options: DENY`;
- redirects and generated links can point at `localhost` or at the internal
  container name;
- the phpMyAdmin cookies must not collide between sites, and the browser must
  not receive the database password in a URL.

Today this is falsable and fails: there is no proxy route, the iframe cannot
authenticate, and the external link leaves the panel.

## WHAT

Add an **internal, authenticated reverse proxy** under the hosting plugin:

```
browser iframe
  -> /api/v1/plugins/hosting/sites/{site_id}/phpmyadmin/...
     (opaque proxy-session cookie, sent automatically by the iframe)
  -> SPanel validates session, user, tenant, permission and site ownership
  -> SPanel talks to http://spanel-phpmyadmin:8080 over an internal Docker network
  -> phpMyAdmin is already logged in as the site's own MariaDB user
```

The browser never talks to phpMyAdmin directly, never sees its cookies and never
puts the database password in the iframe URL. The phpMyAdmin login is performed
server-side once per proxy session with the site's existing `WORDPRESS_DB_*`
credential (or the `wp-config.php` fallback from A.SPEC 0018).

The frontend replaces the "open a new tab" action with a React modal of
`95vw × 90vh` that contains the iframe, plus **Expandir/Restaurar** and
**Cerrar** actions. The database credential dialog stays as it is.

## SCOPE

1. `plugins/hosting/backend/phpmyadmin_proxy.py` (new, focused module)
   - validate `SPANEL_PHPMYADMIN_INTERNAL_URL` (http/https only, no userinfo);
   - `PmaClient`: a stdlib `http.client` client that performs the login form
     flow (`token` + `set_session` + `pma_username` + `pma_password`), keeps the
     upstream cookies server-side and rejects a response that is still the login
     form;
   - pure response helpers: cookie parse/merge, `Location` rewrite, HTML/CSS/JS
     origin rewrite, hop-by-hop and framing-header filtering;
   - a streaming proxy response for binary downloads, with body rewriting only
     for bounded text responses.
2. `plugins/hosting/backend/phpmyadmin_session.py` (new, focused module)
   - server-side session records in the core cache
     (`systutor.core.cache.cache()`): random id, `user_id`, `tenant_id`,
     `site_id`, upstream cookies, absolute expiry;
   - one-time ticket records (30 s) that exchange for the session cookie;
   - `get_phpmyadmin_session` dependency: cookie or ticket, expiry, load the
     user, tenant context, `hosting.sites.manage`, owned site;
   - cookie name/path/flags and cache-key helpers.
3. `plugins/hosting/backend/settings.py`
   - `phpmyadmin_internal_url` from `SPANEL_PHPMYADMIN_INTERNAL_URL`, default
     `http://spanel-phpmyadmin:8080`;
   - `phpmyadmin_session_ttl` (default 1500 s, max 3600);
   - `phpmyadmin_timeout` (default 15 s).
4. `plugins/hosting/backend/schemas.py`
   - `PhpMyAdminSessionResponse { url, expires_in }`.
5. `plugins/hosting/backend/service.py`
   - `phpmyadmin_login_credentials(site, tenant_context)`: the same owned-site,
     DB-running and environment/`wp-config.php` resolution as `site_database`,
     audited as `hosting.site.phpmyadmin_access` with database and host only;
   - it never returns the password to the route.
6. `plugins/hosting/backend/router.py`
   - `POST /sites/{site_id}/phpmyadmin/session` (Bearer, `hosting.sites.manage`);
   - `DELETE /sites/{site_id}/phpmyadmin/session` (Bearer, `hosting.sites.manage`);
   - the catch-all proxy route with all common HTTP methods, using the proxy
     session dependency instead of the Bearer dependency.
7. `plugins/hosting/frontend/api.ts`
   - `createPhpMyAdminSession`, `closePhpMyAdminSession`, types.
8. `plugins/hosting/frontend/components/PhpMyAdminDialog.tsx` (new)
   - modal, loading/error/retry, iframe, expand/restore, close, reload.
9. `plugins/hosting/frontend/components/DatabaseDialog.tsx`
   - "Abrir phpMyAdmin" opens the embedded modal instead of `window.open`.
10. `vendor/systutor-shell/src/ui/dialog.tsx`
    - optional `bodyClassName` (default unchanged) so the iframe body can be
      flex-filled with no padding. Existing callers keep their current layout.
11. `.env.docker.example`, `plugins/hosting/README.md` and root `README.md`
    - internal URL, session TTL and the network prerequisite.
12. Tests:
    - `plugins/hosting/backend/tests/test_phpmyadmin_rewrite.py`
    - `plugins/hosting/backend/tests/test_phpmyadmin_proxy.py`
    - `plugins/hosting/backend/tests/test_phpmyadmin_auth.py`
    - `scripts/tests/test_hosting_phpmyadmin_live.py`
13. This document.

## OUT OF SCOPE

- **Creating, recreating, deleting or modifying any database, container, volume
  or network.** The proxy only reads the site's existing credential and talks
  HTTP to the existing phpMyAdmin.
- **Creating SQL users or grants.** The site's existing WordPress user is the
  only login. If its grants do not admit the phpMyAdmin container, the session
  fails with a controlled error.
- **Publishing phpMyAdmin on a port or subdomain**, changing Traefik, changing
  `docker-compose.yml`, or changing `PMA_ABSOLUTE_URI`/`PMA_HOST`.
- **Letting the browser log in manually.** The proxied login form is disabled;
  the only credentials used are the owned site's.
- **New permissions, migrations, tables or events.**
- **Storing the database password** in the session cache, cookie, iframe URL,
  logs or audit row.
- **Using `SPANEL_PHPMYADMIN_URL` as the upstream.** The new internal variable is
  authoritative. The old variable stays only for the deprecated
  `SiteDatabaseResponse.url` field and is not used by the proxy.

## CONTRACT

### Session creation

| Method | Path | Auth | Permission |
|---|---|---|---|
| `POST` | `/api/v1/plugins/hosting/sites/{site_id}/phpmyadmin/session` | Bearer | `hosting.sites.manage` |

Response `200`:

```json
{ "url": "/api/v1/plugins/hosting/sites/<site_id>/phpmyadmin/?ticket=<one-time>", "expires_in": 1500 }
```

- `Cache-Control: no-store`, `Pragma: no-cache`.
- The site is resolved by `get_owned_site`; another tenant's id is 404.
- The credential is resolved exactly like A.SPEC 0017/0018: environment first,
  `wp-config.php` second; the site and database containers must be running.
- The server logs in to phpMyAdmin with that credential, stores the phpMyAdmin
  cookies in the server-side session and returns only a one-time ticket URL.
- `409 hosting_phpmyadmin_unavailable` when the internal URL is unset/invalid or
  the site/database is not available; `502 hosting_phpmyadmin_login_failed` when
  phpMyAdmin rejects the credential or is unreachable. No password is echoed.
- The audit row is `hosting.site.phpmyadmin_access` with `database` and `host`.

### Proxy

| Method | Path | Auth |
|---|---|---|
| `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS` | `/api/v1/plugins/hosting/sites/{site_id}/phpmyadmin/{path:path}` | proxy cookie or one-time ticket |

- `GET .../phpmyadmin/?ticket=...` validates the one-time ticket, creates the
  session cookie and answers `303` to `.../phpmyadmin/` with the ticket removed.
  The redirect carries `Referrer-Policy: no-referrer`.
- A missing, expired, unknown or wrong-site cookie is `401` with a small HTML
  message ("La sesión de phpMyAdmin expiró; volvé a abrir el modal").
- The cookie is `spanel_pma`, `HttpOnly`, `SameSite=Strict`, `Path` exactly the
  site's `/api/v1/plugins/hosting/sites/{site_id}/phpmyadmin`, and `Secure` when
  the request scheme/`X-Forwarded-Proto` is https.
- Every proxied request revalidates: session exists and is not expired, user is
  active, `user.tenant_id == session.tenant_id`, the tenant context still has
  `hosting.sites.manage`, and the site row still belongs to that tenant.
  Missing user is 401, missing permission is 403, another tenant's site is 404.
- A proxied `POST` whose form contains `pma_username` or `pma_password` is
  rejected with `403 hosting_phpmyadmin_login_disabled`; the login form is not a
  second way into the database.
- An upstream response that is still the phpMyAdmin login form invalidates the
  session and returns `401`; it is never rendered in the iframe.
- All other paths, query strings, methods, form bodies, uploads, static assets,
  AJAX responses and downloads are forwarded.
- The upstream base is the configured internal URL. The browser path after
  `/phpmyadmin/` is appended to the upstream base path. The host is fixed; a
  client cannot choose it (no SSRF parameter).

### Response handling

- Relay status, content type, content disposition, cache headers, range headers
  and other end-to-end headers.
- Never relay upstream `Set-Cookie` to the browser; merge it into the
  server-side session. Deletions (`Max-Age=0`, past `Expires`) remove the cookie.
- Strip hop-by-hop headers, `X-Frame-Options`, `X-Content-Security-Policy` and
  `X-WebKit-CSP`. Keep CSP but replace `frame-ancestors` with
  `frame-ancestors 'self'`; the iframe is same-origin, so `'self'` is the panel.
- Rewrite `Location` values that point at the internal origin, the legacy
  `SPANEL_PHPMYADMIN_URL` origin, `127.0.0.1:<port>` or `localhost:<port>` to the
  site's proxy base. Relative `Location` values stay relative.
- For bounded `text/html`, `text/css`, `application/javascript` and
  `application/json` responses, rewrite the same origins to the proxy base. Use
  `Accept-Encoding: identity` upstream so the text is readable. Binary and
  larger bodies stream unmodified.
- The iframe never receives a URL containing `127.0.0.1`, `localhost`, the
  internal container name or the database password.

### Frontend

- "Abrir phpMyAdmin" (in the existing database dialog) opens
  `PhpMyAdminDialog` at `95vw × 90vh`.
- The dialog calls `POST .../phpmyadmin/session`, then sets the iframe `src` to
  the returned relative URL.
- Header actions: **Expandir** toggles `100vw × 100vh` (label **Restaurar**),
  **Recargar** recreates the iframe from a fresh session, **Cerrar** closes and
  calls `DELETE .../phpmyadmin/session` best-effort.
- The iframe fills the body; the dialog keeps the current SPanel border,
  background, title and button styles. On error it shows the existing `Alert`
  and a **Reintentar** action. No credentials are rendered in the modal.
- The old `window.open(access.url)` path is removed. `SiteDatabaseResponse.url`
  remains for API compatibility but the UI no longer follows it.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: The browser never receives the database password in the phpMyAdmin flow.
    proof: >
      The session response contains only a relative URL and a TTL. The ticket is
      random, not derived from the credential. The login POST is made by the
      server; tests assert the password is absent from the response body, the
      URL, the cookie and the audit row.

  - id: I2
    statement: Only the owned site's MariaDB user can be used through the proxy.
    proof: >
      Credentials come from the owned site's container, never from the request.
      Proxied login-form POSTs are rejected, and an upstream login page
      invalidates the session instead of being shown. Tests cover both.

  - id: I3
    statement: Every proxied request revalidates session, user, tenant, permission and site.
    proof: >
      The proxy dependency loads the cookie session and re-checks the user and
      site on every request; tests revoke the permission, deactivate the user,
      expire the session and move the site to another tenant.

  - id: I4
    statement: Another site's cookie/session cannot be replayed against this site.
    proof: >
      The cookie path is site-specific and the session record stores site_id and
      tenant_id. A test presents a valid cookie for site A to site B and gets
      401/404, never an upstream request.

  - id: I5
    statement: No container, database, volume or network is created, modified or deleted.
    proof: >
      The only Docker operations are the existing read-only inspects and
      wp-config read; phpMyAdmin login is HTTP. The live test lists Docker
      resources before and after and asserts they are identical.

  - id: I6
    statement: No public port and no subdomain are added.
    proof: >
      The iframe uses the panel's own origin path; the internal URL is only used
      by the server. docker-compose.yml and Traefik labels are unchanged.

  - id: I7
    statement: phpMyAdmin sessions, redirects, forms, AJAX and static assets work in the iframe.
    proof: >
      A fake upstream server test performs GET, POST, 302 and static asset
      requests through the proxy; the live test loads the real phpMyAdmin
      dashboard and one of its CSS/JS assets.

  - id: I8
    statement: The iframe never navigates to localhost or the internal container name.
    proof: >
      Location and text rewrites are unit-tested for absolute upstream URLs,
      loopback URLs and root-relative paths. The live test scans the proxied
      dashboard and redirects for `127.0.0.1` and the internal hostname.

  - id: I9
    statement: The modal preserves the panel design and has expand and close actions.
    proof: >
      The component uses the existing Dialog/Button/Alert primitives, with a
      95vw x 90vh default and a 100vw x 100vh expanded state. The frontend build
      and the manual UI check cover it.

  - id: I10
    statement: The session cache never contains the database password.
    proof: >
      The session record stores user/tenant/site ids, expiry and phpMyAdmin
      cookies only. A unit test inspects the serialized cache value and asserts
      the password is absent.
```

## VERIFICATION

### Unit and component

```bash
python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
ruff check vendor/systutor-core/plugins/hosting/backend
python3 -m pyright vendor/systutor-core/plugins/hosting/backend
npm --prefix apps/web run build
```

- URL validation: http/https only, no userinfo, no empty host; default is
  `http://spanel-phpmyadmin:8080`.
- Cookie merge: set, replace, delete, `Max-Age=0`, expired `Expires`, multiple
  `Set-Cookie` headers.
- `Location`: absolute internal, absolute loopback, root-relative, relative and
  external.
- Body rewrite: HTML/CSS/JS containing internal/loopback origins; binary body
  unchanged; bounded text only.
- Header filter: `X-Frame-Options` and legacy CSP removed; `frame-ancestors`
  normalized; hop-by-hop headers absent; upstream `Set-Cookie` not relayed.
- Login: form parsing gets `token` and `set_session`; a successful POST is not a
  login form; a rejected POST raises the controlled error and never logs the
  password.
- Auth: missing cookie 401; expired session 401; inactive user 401; wrong tenant
  session 401/404; revoked permission 403; cross-site cookie 404; ticket replay
  401; login-form POST 403.
- Session cache JSON does not contain the password.

### Fake upstream, full proxy

`test_phpmyadmin_proxy.py` starts a stdlib `ThreadingHTTPServer` on
`127.0.0.1:0` and exercises the real client/proxy helpers:

- GET login page -> cookie -> POST credentials -> dashboard;
- dashboard HTML with `X-Frame-Options: DENY` -> header stripped;
- `302 Location: http://127.0.0.1:<port>/index.php?...` -> rewritten;
- CSS and JS assets -> correct content type and bytes;
- form POST and query string -> preserved;
- upload body -> preserved;
- upstream cookie update and deletion -> reflected in the session, never in the
  browser response;
- upstream login page after expiry -> 401 and session invalidated.

### Live, read-only, from the test laptop

The laptop runs the real phpMyAdmin lab and can reach it on loopback. The test
uses an existing WordPress container's credential and does not modify anything:

```bash
SPANEL_LIVE_TESTS=1 \
SPANEL_PHPMYADMIN_INTERNAL_URL=http://127.0.0.1:8081 \
SPANEL_PHPMYADMIN_TEST_CONTAINER=spanel-test-wp \
python3 -m pytest scripts/tests/test_hosting_phpmyadmin_live.py -q
```

- Resolve `spanel-test-wp`'s credential from its environment or `wp-config.php`.
- Log in through the real `PmaClient`; assert the dashboard is not a login page.
- Assert the dashboard lists `wp_test` and does not list another site's database
  (`wp_lucas`).
- Fetch a real CSS/JS asset through the proxy and assert its content type.
- Scan the proxied HTML and redirects for `127.0.0.1`, `localhost` and the
  internal container name.
- Snapshot Docker containers/volumes/networks before and after; assert no
  change. No SQL, no `docker start/stop/rm/create`, no container recreate.

The production app container must be able to resolve the configured internal
URL (phpMyAdmin and the app on a shared internal Docker network). This is an
infrastructure prerequisite; this spec does not attach, recreate or modify the
existing containers. If the URL is not resolvable, session creation returns
`502` and the modal shows the controlled error.

### Manual UI

- Open a site -> **Base de datos** -> **Abrir phpMyAdmin**.
- The modal opens at 95vw x 90vh with no page navigation.
- phpMyAdmin is already logged in as the site user and shows only that site's
  database.
- Browse a table, run a harmless `SELECT`, upload/import a small text file and
  download a small export.
- **Expandir** fills the viewport; **Restaurar** returns to 95vw x 90vh;
  **Cerrar** removes the iframe and the modal.
- A user without `hosting.sites.manage` sees no button and gets 403 from the
  session route.

## ROLLBACK

Revert the new backend/frontend modules, the router/settings/service/schema
changes, the Dialog optional prop, the tests, the documentation and this
document. No migration, no SQL, no container or network change and no stored
data, so rollback is a `git checkout`. The old credential dialog and external
link behaviour return; phpMyAdmin and MariaDB are untouched.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0019-hosting-phpmyadmin-proxy.md
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_session.py
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_phpmyadmin_rewrite.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_phpmyadmin_auth.py
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/components/PhpMyAdminDialog.tsx
    - vendor/systutor-core/plugins/hosting/frontend/components/DatabaseDialog.tsx
    - vendor/systutor-shell/src/ui/dialog.tsx
    - scripts/tests/test_hosting_phpmyadmin_live.py
    - vendor/systutor-core/plugins/hosting/README.md
    - README.md
    - .env.docker.example
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/database.py
    - vendor/systutor-core/plugins/hosting/backend/database_binding.py
    - vendor/systutor-core/plugins/hosting/backend/wpconfig.py
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
    - two new backend modules and one catch-all proxy route
    - one server-side session per open phpMyAdmin modal
    - one HTTP login to the existing phpMyAdmin per session
    - one iframe modal in the site database dialog
  indirect:
    - core cache use (Redis when available, memory fallback)
    - existing site/database inspects during session creation
    - one audit row per session creation
  must_not_affect:
    - surface: mariadb_data
      invariant: no SQL, no user, no grant, no schema change
    - surface: containers
      invariant: no start/stop/rm/create/recreate/network change
    - surface: secrets
      invariant: password never in URL, cookie, cache, log, audit or iframe
    - surface: other_tenants
      invariant: cookie and session are scoped to one owned site
    - surface: public_exposure
      invariant: no new host port, subdomain or Traefik router
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013   # ownership and site list
    - 0017   # site database credential and phpMyAdmin URL
    - 0018   # wp-config.php fallback for the credential
  must_compose_with:
    - an existing phpMyAdmin reachable at SPANEL_PHPMYADMIN_INTERNAL_URL
    - the app container and phpMyAdmin sharing an internal Docker network
    - the core cache (Redis in production, memory fallback)
  composition_checks:
    - python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
    - npm --prefix apps/web run build
    - SPANEL_LIVE_TESTS=1 SPANEL_PHPMYADMIN_INTERNAL_URL=http://127.0.0.1:8081 SPANEL_PHPMYADMIN_TEST_CONTAINER=spanel-test-wp python3 -m pytest scripts/tests/test_hosting_phpmyadmin_live.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_session.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/frontend/components/PhpMyAdminDialog.tsx
```

`router.py` only parses the request and delegates. `phpmyadmin_proxy.py` owns
upstream HTTP, cookies and response rewriting. `phpmyadmin_session.py` owns the
cache record, ticket exchange and per-request authorization. `service.py` only
resolves the existing site credential and audits. No new runtime dependency is
required: the proxy uses `http.client`, `http.cookies` and `urllib.parse`.
`dialog.tsx` gains one optional body class; existing layouts are unchanged.

## Traceability

- **Requirement**: open phpMyAdmin inside a 95vw x 90vh React modal through an
  authenticated internal reverse proxy, without public ports or subdomains.
- **A.SPEC**: this document
- **Code**: `plugins/hosting/backend/phpmyadmin_proxy.py`,
  `plugins/hosting/backend/phpmyadmin_session.py`,
  `plugins/hosting/backend/router.py`,
  `plugins/hosting/frontend/components/PhpMyAdminDialog.tsx`
- **Migration**: none
- **Test**: `test_phpmyadmin_rewrite.py`, `test_phpmyadmin_proxy.py`,
  `test_phpmyadmin_auth.py`, `scripts/tests/test_hosting_phpmyadmin_live.py`
- **Commit**: `TBD`
- **Deployment**: rebuild the image and set
  `SPANEL_PHPMYADMIN_INTERNAL_URL`; no SQL and no migration.
- **owner**: `TBD`
- **approver**: `TBD`

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
