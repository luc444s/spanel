# A.SPEC 0065 — Apps deploy governed by RBAC, not platform admin

> `risk: medium` — removes the `core.platform.admin` requirement from Apps
> deploy while keeping the tenant boundary and the platform-only delete gate.
> No schema migration.

## WHY

`core.platform.admin` is the platform break-glass capability: tenant CRUD,
global inventories, hosting deletion and cross-tenant operations. Using it as a
prerequisite for deploying an application forces operators to grant a broadly
powerful permission just to run a tenant-scoped deploy.

Deployment is already protected by an explicit plugin permission
(`apps.deployments.create`) and by the tenant-scoped application lookup. The
extra `TenantContext.is_platform` check is redundant for authorization and
prevents selected tenants from deploying without becoming platform admins.

## WHAT

- `POST /applications/{application_id}/deployments` requires only
  `apps.deployments.create` plus an application owned by the active tenant.
- `core.platform.admin` is no longer checked by the deploy endpoint.
- The frontend shows Deploy when the session has `apps.deployments.create`;
  it no longer requires `core.platform.admin`.
- Delete keeps its platform double gate: `apps.applications.delete` **and**
  `core.platform.admin`.
- `apps.deployments.create` remains in `platform_permissions` so existing and
  future platform roles keep receiving it on boot; it also remains an ordinary
  RBAC permission assignable to tenant roles.
- No tenant flag, approval workflow or tenant role-management UI is added in
  this spec. Those are future work.

## SCOPE

Entra:
- `vendor/systutor-core/plugins/apps/backend/router.py`
- `vendor/systutor-core/plugins/apps/frontend/logic.ts`
- `vendor/systutor-core/plugins/apps/frontend/pages/AppsPage.tsx`
- `vendor/systutor-core/plugins/apps/frontend/logic.test.ts`
- `vendor/systutor-core/plugins/apps/backend/tests/test_deploy_source.py`
- `vendor/systutor-core/plugins/apps/permissions/apps.json`
- `aspecs/0065-apps-deploy-rbac.md`

No entra:
- `core.platform.admin` semantics or `core.platform.inventory` split.
- Tenant-level feature flags, deploy approval or tenant role management.
- Quotas, sandbox or network-policy hardening; those remain required before
  exposing deploys to untrusted tenants (A.SPEC 0052).
- Delete decoupling.

## CONTRACT

1. A user with `apps.deployments.create` can deploy an application that belongs
   to the active tenant.
2. A user without `apps.deployments.create` receives `403` from the permission
   dependency.
3. An application from another tenant is not found (`404`) even with the
   deploy permission.
4. `apps.applications.delete` still requires `core.platform.admin`.
5. Platform roles keep receiving `apps.deployments.create` through the
   additive `platform_permissions` boot sync.
6. No database migration or automatic permission grant to `tenant_admin`.

## INVARIANTS

- The active tenant is never taken from client input.
- Deploy never resolves an application outside `context.current_tenant_id`.
- Hiding the button is not authorization; the backend permission remains.
- The permission sync is additive-only and never revokes existing grants.

## VALIDATION

- `pytest vendor/systutor-core/plugins/apps/backend/tests/test_deploy_source.py -q`
- `npm test` in `apps/web` for the Apps frontend logic.
- `tsc --noEmit` in `apps/web`.

## OUT OF SCOPE

- Self-service onboarding of tenants to Apps deploy.
- Per-tenant quotas, sandboxing, egress policy or approval.
- Decoupling application deletion.
