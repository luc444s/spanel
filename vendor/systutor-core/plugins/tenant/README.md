# Tenant Management Plugin

Superadmin-only plugin for managing tenants: CRUD operations, domain configuration, and user assignment.

## Permissions

- `tenant.tenants.read` — List and view tenants
- `tenant.tenants.manage` — Create, update tenants and assign users

## Endpoints

- `GET /api/v1/plugins/tenant/tenants` — List all tenants
- `POST /api/v1/plugins/tenant/tenants` — Create a tenant
- `PUT /api/v1/plugins/tenant/tenants/{tenant_id}` — Update tenant domain
- `GET /api/v1/plugins/tenant/tenants/{tenant_id}/users` — List users in a tenant
- `POST /api/v1/plugins/tenant/tenants/{tenant_id}/users` — Assign user to tenant
- `DELETE /api/v1/plugins/tenant/tenants/{tenant_id}/users/{user_id}` — Remove user from tenant
