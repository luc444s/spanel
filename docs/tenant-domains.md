# Tenant-managed domains

Domain ownership is explicit and globally unique. It lives in
`tenant_domains(domain PK, tenant_id, created_at, updated_at)` and is not inferred
from `tenant_site_settings.site_suffix`.

English | [Español](tenant-domains.es.md)

## Rules

- A domain belongs to exactly one tenant. The PK enforces this and the API
  responds `409 tenant_domain_conflict` if another tenant claims it.
- The tenant module manages the set:
  - `GET /api/v1/plugins/tenant/tenants/{id}/domains`
  - `POST /api/v1/plugins/tenant/tenants/{id}/domains` with `{"domain": "..."}`
  - `DELETE /api/v1/plugins/tenant/tenants/{id}/domains/{domain}`
- All three endpoints require `core.platform.admin` in addition to
  `tenant.tenants.read`/`manage`.
- `tenant.domain` remains the primary mail domain and does not change when
  adding or removing managed domains.
- The tenant UI shows the primary domain (`tenants.domain`, the one used by
  DMS), the `site_suffix` and the list of additional domains. The top
  form edits the primary domain and the suffix; the bottom section adds or removes
  secondary domains from `tenant_domains`.
- `site_suffix` still exists as configuration and informational value,
  but it no longer decides site visibility, availability or creation.

## Effects on other modules

- **Domains**: `check` is available for any free or owned domain;
  `list` shows its sites' primary domains, extras from its YAML and also the
  `tenant_domains` rows not yet published (A.SPEC 0040): they are listed as
  `published=false` with a "Link" action. `DELETE` on a reserved domain releases the
  row if it is not in use; if the proxy does not respond, the list keeps working and
  warns with `proxy_available=false`.
- **Hosting**: creating a site accepts any free or owned domain. If it is
  free, it claims it in the same transaction; if it belongs to another tenant it responds
  `409 hosting_site_domain_conflict`.
- **Mail**: the domain set is `{tenant.domain}` ∪ `tenant_domains`.
  Accounts can be created and passwords changed on any of them; the
  primary domain remains the default value.

## Account unification

`scripts/unify_tenants.py` consolidates several tenants into one. By default it is
dry-run and always writes a JSON plan:

```bash
python3 scripts/unify_tenants.py \
  --target-slug demo \
  --domain spanel.site --domain debug.com --domain test2.com \
  --all-others --apply
```

- Moves sites and users, claims domains and deactivates the sources.
- Never changes `container_name`, `domain` or `kind`, nor deletes users or
  audit records.
- Does not run Docker, does not rewrite YAML, does not restart containers or issue
  certificates.
- `--rollback PLAN` reverts exactly what is recorded in the plan.
- `--purge` only deletes a source with no dependencies; auditing blocks it.

## Synchronization

`scripts/sync_tenant_domains.py` keeps `tenant_domains` consistent with the rule
"the primary is not a secondary". By default it is dry-run and always writes a
JSON report:

```bash
python3 scripts/sync_tenant_domains.py            # dry-run
python3 scripts/sync_tenant_domains.py --apply    # removes redundant rows
```

- Removes `tenant_domains` rows whose domain is the primary domain of the same
  tenant (leftover from the `002` migration backfill).
- Does not insert primary domains.
- If a row is the primary domain of **another** tenant, it writes nothing, reports it and
  exits with code 1: this is resolved by changing the primary domain or unifying
  accounts.
- Invalid domains are reported and skipped.
- It is idempotent: the second run reports `0` rows.

## Migration

`plugins/tenant/migrations/002_tenant_domains.py` creates the table and backfills
from `tenants.domain` and from the primary domains of `hosting_sites`. It is idempotent and
`downgrade()` removes only `tenant_domains`.

Before a real unification, a database backup is recommended, along with review of the
generated JSON plan.
