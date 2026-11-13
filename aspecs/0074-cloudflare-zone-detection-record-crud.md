# A.SPEC 0074 — Cloudflare zone detection and DNS record CRUD

> `risk: high` — mutates public DNS through tenant OAuth credentials. Human
> approver: `luc444s`.

## WHY

Cloudflare OAuth connections currently expose a separate read-only discovery
API, while the Domains table derives its DNS status exclusively from local
PowerDNS ownership. A successfully connected tenant therefore sees **No zone**
for domains that are present in Cloudflare and cannot manage those records from
the panel.

## WHAT

1. The Domains page queries the active tenant's Cloudflare zones and marks a
   matching domain or subdomain as **Cloudflare**. Local PowerDNS ownership
   remains **Managed** and takes precedence.
2. Opening a Cloudflare-backed domain opens a provider-specific modal rather
   than the local PowerDNS modal.
3. The modal lists record name, type, content, TTL, proxy state and priority,
   and supports create, edit and confirmed delete.
4. New tenant-scoped routes use the existing `domains.dns.cloudflare`
   permission and active encrypted OAuth connection:

```text
POST   /dns/cloudflare/zones/{zone_id}/records
PUT    /dns/cloudflare/zones/{zone_id}/records/{record_id}
DELETE /dns/cloudflare/zones/{zone_id}/records/{record_id}
```

## SECURITY CONTRACT

- Zone access is revalidated through the tenant's own OAuth credential before
  every list or mutation; a foreign/unavailable zone looks absent.
- Access/refresh tokens and OAuth client secrets remain encrypted and are never
  returned, logged or audited.
- DNS record content is returned only to an authenticated tenant user with
  `domains.dns.cloudflare`; it is never included in audit details.
- Create/update/delete audit only tenant, zone ID, record ID, type, name and
  operation result.
- Cloudflare response bodies are never propagated to clients or logs.
- Delete requires an explicit UI confirmation.
- Local PowerDNS routes and records are never mutated by the Cloudflare modal.

## CONTRACT

### Preconditions

1. The active tenant has an `active` Cloudflare connection.
2. The user has `domains.dns.cloudflare`.
3. The zone is visible through that tenant's OAuth token.

### Postconditions

1. Cloudflare zones are represented accurately in the Domains table.
2. Successful record mutations are immediately reflected after query
   invalidation.
3. Every successful mutation creates a secret-free audit entry.
4. An upstream permission, validation, rate-limit or availability failure is
   normalized through the existing Cloudflare error contract.

## VERIFICATION

- Client transport tests cover POST, PUT and DELETE without leaking content.
- Service tests cover tenant-scoped zone validation and secret-free audit.
- Router tests cover permission enforcement and CRUD response contracts.
- Frontend pure tests cover Cloudflare zone precedence/detection.
- Frontend TypeScript, focused Vitest and production build pass.

## TRACEABILITY

- Requirement: connected Cloudflare zones must be detected in Domains and the
  modal must support viewing, adding, editing and removing DNS records.
- Commit: pending
- Deployment: production server `https://spanel.systutor.com` pending
