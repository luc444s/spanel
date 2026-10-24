# A.SPEC 0021 — Release SQL sessions before proxying phpMyAdmin

> `risk: high` — request authentication and DB credential handling must stay
> tenant-scoped. This amends A.SPEC 0019/0020 without changing permissions,
> schema, database users or pool sizing.

## WHY

FastAPI's yield-based `get_db_session` remains alive until an async response
finishes. Using it in the proxy route kept a PostgreSQL connection checked out
across `http.client` calls and streamed downloads. Direct synchronous
`db.get(User, ...)` inside `async def` also blocked the event loop. With enough
concurrent phpMyAdmin assets the app reached `QueuePool size 5 overflow 10
timeout 30s`.

## CONTRACT

- Each proxied request still checks the cached opaque session, active user,
  branch scope, tenant, `hosting.sites.manage` and owned site against fresh DB
  state. Perform those checks in a worker thread with a dedicated short-lived
  SQLAlchemy Session. Close it before any upstream request, upload or streaming.
- Session creation and database credential routes release their read
  transaction after the Bearer/owned-site dependencies, before Docker inspection
  and HTTP login. Audit writes use a new brief transaction and commit before
  the response. No database password is stored in cache or URLs.
- The upstream `http.client` socket closes on normal streaming completion,
  response preparation errors and client disconnection, even before the first
  chunk. A slow upstream must consume no DB connection.
- No SQL pool size change and no other container change.

## VERIFICATION

- 40 parallel real ASGI proxy requests against a QueuePool of size 5, overflow
  0 and timeout 0.5s, with a blocked fake HTTP upstream, all succeed. The pool
  has zero checked-out connections while upstream waits.
- Tests prove connection release when auth fails, session login fails, upstream
  read raises, or a client disconnects before binary streaming starts.
- Run hosting backend tests, Ruff, Pyright and a live authenticated Docker
  smoke check; assert the browser still receives only the opaque proxy cookie.

## ROLLBACK

Revert the proxy/session/router changes and their tests. No migration or DB
configuration change is necessary. The previous implementation can exhaust
the pool under load, so redeploying the corrected app is the remedy.
