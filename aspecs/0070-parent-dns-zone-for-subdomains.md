# A.SPEC 0070 — Parent DNS zone for subdomains

> `risk: medium` — changes authoritative-zone activation policy and clarifies
> how registered web subdomains are represented in the Domains page.

## WHY

Registered web domains such as `infra.example.com` are usually records inside
the authoritative `example.com` zone, not independent DNS zones. Allowing a
standalone child zone without an NS delegation in its parent produces a zone
that exists in PowerDNS but is not reachable through public DNS.

The Domains table also renders its DNS cell before Owner and Certificate while
the headers declare DNS after them, making every value appear under the wrong
heading.

## WHAT

First-stage subdomain handling:

- render row cells in the declared order: Domain, optional Tenant, Status,
  Owner, Certificate, DNS, Actions;
- when a domain is covered by a tenant-owned zone, keep resolving through the
  longest matching suffix;
- label a child domain as **Managed by {zone}** instead of only **Managed**;
- opening a child row opens the matching parent zone, while retaining the child
  domain as the dialog title;
- when no zone exists yet, activation from a child row activates its nearest
  registered parent domain;
- reject direct creation of a child zone when a registered parent domain exists,
  whether or not the parent zone has already been activated;
- retain longest-suffix resolution so a future explicit delegation workflow can
  safely introduce child zones;
- collapse registered subdomains under their highest registered parent in the
  Domains table by default, with an accessible expand/collapse control;
- keep the complete child rows, including publication, certificate and actions,
  available when the parent is expanded;
- list registered subdomains inside the parent zone's Records tab as a scope
  selector; opening from a child preselects that child and filters its RRsets;
- prefill **New RRset** with the selected child owner while still creating the
  record in the parent authoritative zone.

Explicit NS delegation, DS/DNSSEC handling and delegated-child activation are
out of scope.

## ACCEPTANCE

1. With only `example.com` managed, `infra.example.com` displays
   `Managed by example.com` and opens `example.com`.
2. Apex `example.com` displays `Managed`.
3. Owner, Certificate and DNS values align with their headers.
4. Activating from `infra.example.com` targets `example.com` when both domains
   are registered and neither has a zone yet.
5. Direct `POST /dns/zones` for `infra.example.com` returns `409` when
   `example.com` is registered and does not mutate PowerDNS.
6. Existing tenant isolation, exact-zone activation and longest-suffix tests
   continue to pass.
7. Parent rows are collapsed initially and report their child count; expanding
   reveals complete child rows without duplicating them as top-level rows.
8. The Records tab offers **All records** plus registered children covered by
   that exact longest-owned zone. Child scope includes the child owner and any
   names below it.

## VERIFICATION

```bash
cd apps/web && npm test -- --run src/plugins/domains-dns.logic.test.ts
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_dns_router.py -q
```
