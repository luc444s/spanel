# A.SPEC 0076 — Tenant user permissions with one role per assignment

> `risk: high` — makes one role per tenant user a database invariant, migrates
> historical assignments without losing effective permissions, adds a
> tenant-scoped replacement endpoint and changes the Users editor UI.

## WHY

The kernel already stores tenant-scoped roles, role permissions and user-role
assignments, and the operator CLI proves that explicit grants can be managed
through those tables. The web Users editor currently mixes profile fields and
an unrestricted multi-role checkbox list. For tenants with several users this
is difficult to scan and does not provide a compact way to inspect and edit a
role's permissions.

The tenant UI needs a small, role-first interaction: select one user, assign
exactly one role, and open the shared role permission editor directly from the
role row. One role per user is a system rule, not merely a visual restriction.

## WHAT

### Users dialog

- The user dialog shows two tabs: `Profile` and `Roles`.
- While creating a user, `Roles` remains visible but disabled until the profile
  has been saved.
- `Roles` contains:
  - heading `Roles`;
  - subtitle `Access management`;
  - `RBAC` badge;
  - a tenant-user dropdown;
  - a minimalist list of active tenant roles.
- Each role row contains only a checkbox-like exclusive selector and the role
  title. It has no description, permission count or visible Edit button.
- Selecting a role replaces the pending selection; only one row can be checked.
- `Double-click a role to modify its permissions.` is always visible.
- Double-clicking a role row opens the role permission dialog.
- `Save assignment` atomically replaces every existing role assignment of the
  selected user with the selected role.

### Role permission dialog

- The dialog displays the role name and every permission from the existing
  permission catalog, grouped/filterable by module.
- It warns that changes affect every user currently assigned to the role.
- Saving uses the existing tenant-scoped role update contract and audit path.
- There is no visible `Edit` action in the role list.

### Backend assignment contract

Add:

```http
PUT /api/v1/core/users/{user_id}/role
Content-Type: application/json

{"role_id": "<tenant-role-id>"}
```

- Requires `core.roles.manage`.
- Resolves both user and role inside `TenantContext.current_tenant_id`.
- Rejects an inactive or cross-tenant role.
- Replaces the user's complete role assignment in one transaction.
- Emits an audit record containing previous and new role IDs/names.
- Returns the ordinary `CoreUserRead` representation.

The existing plural `role_ids` fields remain wire-compatible for API clients,
but accept at most one item. An empty list resolves to the tenant's zero-access
`unassigned` role. The CLI and legacy assignment endpoints use the same
replacement service and can no longer create multiple assignments.

### Core runtime migration

- `plugins/tenant/migrations/003_single_user_role.py` is loaded and versioned by
  `systutor.kernel.plugins.migrations`; Alembic is not used.
- The runtime step adds a unique index on `user_roles.user_id`.
- A historical user with multiple roles is migrated to one generated composite
  role containing the union of the old roles' permissions. Effective access is
  preserved instead of selecting or discarding a role silently.
- A historical user without a role receives the tenant-local `unassigned` role,
  which has no permissions.
- New users without an explicit/category role receive `unassigned`.
- User categories may resolve to at most one role.

## SCOPE

Entra:

- `aspecs/0076-tenant-user-single-role-permissions-ui.md`
- core user schema, router and service for the singular replacement endpoint;
- core-management frontend API client;
- Users dialog decomposition and `Roles` tab;
- platform Tenants → Users dialog with `Profile` and `Roles` tabs, explicitly
  scoped to the tenant selected by the platform administrator;
- nested role permission editor;
- English source strings and Spanish catalog entries;
- backend and frontend regression tests.
- tenant plugin runtime migration and its loader-level regression test;

No entra:

- direct user-permission rows;
- renaming existing tenant roles;
- deleting roles;
- invoking the CLI as a subprocess from HTTP;
- changing platform-wide RBAC semantics.
- any Alembic revision or Alembic command.

## CONTRACT

1. Every write is scoped to the authenticated tenant; tenant IDs never come
   from the request body.
2. Every tenant user has exactly one `UserRole`, enforced by service logic and
   a database unique constraint.
3. A role from another tenant is indistinguishable from a missing role.
4. An inactive role cannot be assigned.
5. Profile updates no longer rewrite role assignments from the web UI.
6. One click selects a role; double-click opens its permission editor.
7. Role rows show only selection state and role title.
8. Editing a role updates the shared role, therefore every member receives the
   new effective permission set.
9. Existing audit/event behavior for role updates remains active; singular user
   role replacement adds its own audit/event record.
10. Historical effective permissions survive migration through a composite
    role; roleless users receive zero access through `unassigned`.
11. English is the source UI language and Spanish remains available through the
    existing catalog.

## INVARIANTS

- Frontend exclusivity is not authorization; the backend performs replacement.
- No cross-tenant user, role or permission association is accepted.
- No service, legacy endpoint or CLI path can add a second user role.
- A failed validation makes no assignment changes.
- Existing unrelated working-tree changes are not included in the feature
  commit.
- No touched source file exceeds 600 lines; UI is split before 400 lines.

## VALIDATION

- Core runtime migration tests for multi-role permission union, roleless users
  and the unique constraint.
- Focused core API tests for replacement, inactive role, tenant isolation,
  audit and event emission.
- Frontend logic tests for exclusive selection and legacy multi-role handling.
- `ruff check` on modified Python files.
- `python3 -m pyright` for the core package.
- Focused `pytest` plus the core management test module.
- Focused Vitest plus `npm run build` in `apps/web`.
- `git diff --check`.

## OUT OF SCOPE

- A full user-role matrix or bulk assignment screen.
- Per-user direct grants implemented as hidden roles.
- Mobile-only gesture design beyond the existing row interaction.
- Automatic privilege-escalation classification of every plugin permission.
