# A.SPEC 0020 — Open proxied phpMyAdmin in a new tab

> Replaces the iframe/modal UI of A.SPEC 0019 at the user's request. The
> authenticated internal proxy, tenant isolation and server-side credentials
> remain the security boundary. No direct phpMyAdmin URL is exposed.

## WHY

phpMyAdmin runs a cross-framing protection script that navigates the top-level
browser window when embedded. Its links also include query values such as
`index.php?route=/server/status`; treating those query values as root-relative
URLs produces a 404 on the proxied `/server/status` path.

## WHAT

- **Abrir phpMyAdmin** opens a blank tab synchronously with the user's click,
  creates the owned site's session through the Bearer-authenticated POST, then
  navigates the tab to the returned same-origin one-time ticket URL. The
  opener is detached before navigation. If the POST fails, close the blank tab
  and show the actual API error in the existing credential dialog.
- No iframe, custom toolbar, modal, new public port or subdomain is used. The
  browser's native tab controls handle reloading and closing.
- Preserve query route values (`route=/server/status`) when rewriting upstream
  HTML; root-relative asset links and internal absolute origins still map to
  the site's proxy path.
- The proxy's existing opaque site-scoped cookie and per-request authorization
  apply equally to the new tab. The ticket is consumed by a 303 redirect and
  never contains the database password.

## VERIFICATION

- A rewrite test ensures `href="index.php?route=/server/status"` is unchanged,
  while root-relative assets and internal origins still rewrite correctly.
- Backend tests, Ruff and Pyright pass; frontend build passes.
- Against the real laptop Docker deployment: the authenticated session POST
  returns 200, the new-tab ticket follows a 303 to the dashboard (200), and
  the dashboard's Status link retains `index.php?route=/server/status` and
  returns 200 through the proxy. The browser receives only `spanel_pma`.

## ROLLBACK

Restore the frontend dialog behavior and prior response rewriter; no schema,
container, database or network change is required.
