# A.SPEC 0077 — Domains and DNS as the primary application

> `risk: low` — changes visible labels, navigation order and RBAC-aware landing
> redirects. Technical plugin IDs, routes, APIs and permission namespaces stay
> unchanged.

## WHY

The `domains` plugin manages both registered domains and authoritative DNS, but
its visible name only says `Domains`. It is the primary product surface and
must be the first Administration entry and the first application users see
after login when their role can access it.

## WHAT

- Visible plugin, route, page and navigation label: `Domains and DNS`.
- Spanish label: `Dominios y DNS`.
- `/app/domains` remains unchanged.
- The Domains and DNS entry sorts first inside `Administration`.
- Login, `/` and the `/app` index land on `/app/domains` when the session has
  `domains.domains.read`.
- Users without that permission land on `/app/dashboard`; no redirect sends a
  user to a forbidden route.

## CONTRACT

1. Plugin ID remains `domains`.
2. Permission namespace remains `domains.*`.
3. API and frontend path remain `/app/domains`.
4. Requested protected routes preserved by login still take precedence over
   the default landing page.
5. Driver-category routing keeps its dedicated logistics destination.

## VALIDATION

- Frontend tests cover landing fallback and Administration ordering.
- `npm run build` in `apps/web`.
- Existing Domains frontend tests.
- `git diff --check`.
