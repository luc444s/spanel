# A.SPEC 0022 — Preserve phpMyAdmin AJAX navigation scripts

## Why

Firefox reports `AJAX.source.getPostData is not a function` after clicking the
`wp_demowp` database in the phpMyAdmin 5.2.3 sidebar. Opening the same link in
a new tab works. The proxy's generic `(["'\(])/...` URL rewrite corrupts JS
regular expressions in `js/dist/functions.js`. A syntax error prevents that
script from registering `jQuery.fn.getPostData`, which `js/dist/ajax.js`
expects during normal AJAX navigation.

## Contract

- Serve authenticated JavaScript assets byte-for-byte, with the upstream
  Content-Type and without modifying their regular expressions. Preserve the
  existing special case that neutralizes the legacy frame-breakout script.
- Rewrite only URL-bearing HTML attributes and CSS `url()` references for
  root-relative paths. Preserve inline JavaScript expressions in HTML.
- Keep the same site-scoped cookie, per-request user/site authorization,
  upstream session handling and no-store response policy. No database or
  MariaDB permission changes.

## Verification

- Compare upstream and proxied `functions.js` and `ajax.js` on the Docker
  deployment: hashes and Content-Type must match. The proxied `functions.js`
  must still define and register `getPostData` before `ajax.js` calls it.
- Exercise a read-only AJAX POST to the `wp_demowp` database structure page;
  expect a successful JSON response with the existing site session.
- Unit regression tests cover an AJAX script pair and HTML containing both
  root-relative links and inline JavaScript regular expressions.
