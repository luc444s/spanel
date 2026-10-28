# Tenant Management Plugin

Superadmin-only plugin for managing tenants: CRUD operations, explicit domain
ownership, site namespace configuration and user assignment.

Every endpoint requires `core.platform.admin` in addition to the plugin
permission, so a tenant role that received the permission by mistake still gets
`403`.

## Permissions

- `tenant.tenants.read` — List and view tenants and their managed domains
- `tenant.tenants.manage` — Create/update tenants, manage domains and assign users

## Endpoints

- `GET /api/v1/plugins/tenant/tenants` — List all tenants
- `POST /api/v1/plugins/tenant/tenants` — Create a tenant
- `PUT /api/v1/plugins/tenant/tenants/{tenant_id}` — Update email domain / site suffix
- `GET /api/v1/plugins/tenant/tenants/{tenant_id}/domains` — List managed domains
- `POST /api/v1/plugins/tenant/tenants/{tenant_id}/domains` — Add a managed domain
- `DELETE /api/v1/plugins/tenant/tenants/{tenant_id}/domains/{domain}` — Remove a free domain
- `GET /api/v1/plugins/tenant/tenants/{tenant_id}/users` — List users in a tenant
- `POST /api/v1/plugins/tenant/tenants/{tenant_id}/users` — Create/assign a user
- `DELETE /api/v1/plugins/tenant/tenants/{tenant_id}/users/{user_id}` — Remove user from tenant

## Domain ownership

`tenant_domains` is the source of truth. A domain belongs to exactly one tenant;
the plugin registers the `systutor.contracts.domain_namespace` provider consumed
by `domains`, `hosting` and `mail`. See `docs/tenant-domains.md`.

The site suffix remains tenant configuration but no longer decides which domains
the tenant can use.
