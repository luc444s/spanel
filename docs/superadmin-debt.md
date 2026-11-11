# PAID debt: the `is_superadmin` bypass

English | [Español](superadmin-debt.es.md)

> **Closed.** A.SPEC 0004 removed the flag entirely. This document remains as a
> historical record of why it existed, what made it dangerous, and what remains as minor debt.
> Last review: 2026-09-26.

**What was removed**: the `users.is_superadmin` column, the JWT claim, the DTOs,
the 5 shortcuts, `require_superadmin` and its 7 endpoints, and the 40 frontend usages.
Superadmin access is now **exclusively through `RolePermission`**.

**What remains as minor debt**:

| Debt | Why it was not paid | Cost |
|---|---|---|
| The `users.is_superadmin` column still exists in deployed databases | `entrypoint.sh` uses `create_all`, which does not run migrations. Dropping it requires `ALTER TABLE` | Inert: nobody reads it anymore. An `ALTER TABLE ... DROP COLUMN` resolves it |
| `npm run db` points to `scripts/systutor-db.sh`, which does not exist | Trivial fix, unrelated to authorization | The command fails; it must be done by hand |
| `pyright` does not include `plugins/**` | Adding it may expose new, never-reviewed type errors | The mail and tenant plugins have no type-check |
| Permissions as inline strings (~15 in `api/v1/core/`) | Outside the scope of 0004 | A typo is a **silent 403**. Constants and a test against the catalog are missing |
| `mailadmin/.venv` with two editables | Verified that the cores are equivalent, with no impact | Kernel tests need `PYTHONPATH=src` |

---

# Historical record

What follows is the analysis prior to payment. It is preserved because it explains the traps
that existed and that should not be reintroduced.

---

## 1. What the debt is

`users.is_superadmin` is a boolean flag that short-circuits authorization. It is written
**five times**, and three of those copies are on the same request path that decides permissions.

| # | Location | Form | Note |
|---|---|---|---|
| B1 | `src/systutor/kernel/tenants/context.py:22` | `return self.is_superadmin or ...` | The original. The one used by `has_permission` |
| B2 | `src/systutor/kernel/tenants/context.py:26` | `self.is_superadmin or ...` | **Redundant**: a user with no warehouses already gets `None` = all (`context.py:40`) |
| B3 | `src/systutor/kernel/auth/dependencies.py:174` | `is_superadmin or any(has_permission(...))` | **Doubly redundant**: `has_permission` already short-circuits |
| B4 | `src/systutor/api/v1/system.py:145` | `is_superadmin or any(p in current_permissions ...)` | **The treacherous one**: reads `current_permissions` directly, bypassing `has_permission` |
| B5 | `plugins/tenant/backend/router.py:37-49` | `def require_superadmin()` | The only guard defined outside the kernel. Protects 7 endpoints |

### Why B4 is the worst

```python
can_read_full_runtime = tenant_context.is_superadmin or any(
    permission in tenant_context.current_permissions
    for permission in ("core.plugin.runtime.read", "core.plugin.manage")
)
```

It does not go through `has_permission`. If someone deletes B1 and forgets B4, the superadmin loses
read access to the plugin runtime **without any test detecting it**. That is the failure mode that makes this
debt dangerous: it is not that it fails today, it is that it fails *later*, when someone cleans up something else.

### Full inventory

- Production backend: **37 occurrences** on 28 lines, 14 files
- Tests: 14
- Frontend: **40 occurrences in 9 files**
- Documentation: 22

Of those 37, about 10 are the bypass. **The other ~27 are inert plumbing** (column, JWT
claim, DTOs, `request.state`, Zustand store, migration) that does not participate in the decision.

---

## 2. Why it is safe to leave as is

**The five copies read the same source**: the `users.is_superadmin` column of the same
Postgres row. The `tenant` plugin skips `request.state`, but both end up in the same
`User` object. As long as that is true, there can be no divergence.

**There is no exploit today.** `require_superadmin` compares against the same value that the token already
validated in `dependencies.py:82`.

**Removal is cheaper than it looks.** To kill the bypass:

- delete 4 `is_superadmin or ` prefixes (B1-B4)
- delete a function (B5) and replace 7 call sites with `require_permission("core.tenants.*")`
- add `core.tenants.read` and `core.tenants.manage` to `BASE_PERMISSIONS`

**~10 lines and 2 permissions.**

---

## 3. The invariant that makes it payable

> **The admin role has all permissions in `BASE_PERMISSIONS` ∪ plugin permissions.**

If this is true, the bypass is **redundant by construction**: the superadmin has all the
power through the normal RBAC path (`seed.py`, `sync_permissions`). The day B1 is deleted,
nothing is lost, and B4 keeps working because the permission is in the role.

**That invariant is tested** in `tests/test_permission_sync.py`
(`test_kernel_permission_reaches_admin_role_on_second_boot`). Before A.SPEC 0003 it was
**not tested**: `entrypoint.sh` only seeded on the first boot, so a permission added
later never reached the role, and the flag covered it up. That was the hole.

It is also tested that the bypass **is still present**
(`test_superadmin_bypass_still_present`): 0003 did not touch it. That test is the one that will fail
when the debt is paid, and it is good that it fails.

---

## 4. When to review it

Concrete triggers, not "at some point":

1. **Someone hardens `dependencies.py:82`** (the token privilege check). That is the day
   `tenant` is left with a bypass that nobody reviews.
2. **Partial superadmin is requested** — someone who can see all mailboxes but cannot change
   passwords. Today that is impossible with a bool.
3. **A plugin with new permissions is added.** The sync must be confirmed to grant them.
4. **An unexpected 403 appears on a superadmin endpoint.** Symptom of B4.

---

## 5. Pending product decisions

None are resolved here. They belong to the system owner.

- **Does the superadmin retain full power by role, explicitly, or lose the shortcut?**
  - (A) All `core.*` permissions assigned to the role. The shortcut is kept but is auditable.
  - (B) Superadmin = nobody, and permissions are assigned like anyone else. The bypass disappears.
  - (C) Emergency flag: the flag remains only for break-glass, with logging and a UI notice.

  Recommendation: **(C)**, because it preserves emergency capability without the normal path
  depending on the flag. Note: for (C) the flag must **leave the JWT**
  (`security.py:66-74`, `schemas.py:20`, `legacy.py:87`, `api.ts:12`, `store.ts:37`), because a
  60-minute token with embedded privileges is not break-glass.

- **Does the `is_superadmin` claim stay in the JWT?** Today it forces any client that
  hand-builds a token to get the type right (`dependencies.py:63`).

- **Permissions as inline strings.** `require_permission("core.users.read")` appears ~15 times
  in `api/v1/core/`. A typo is a **silent 403**: the endpoint stays closed and nobody
  notices. There is no constant or test against the catalog.

---

## 6. Repository context affecting this

- `entrypoint.sh:8-17` uses `Base.metadata.create_all`, **does not run migrations**. That is why
  the `users.is_superadmin` column **is not dropped**: doing so would require a migration that
  startup would not even run. Leaving it is free and inert.
- `npm run db` points to `scripts/systutor-db.sh`, which **does not exist**.
- `pyproject.toml` `[tool.pyright] include = ["src", "tests", "app"]` **does not include `plugins/`**.
  There is no type-checking on the `tenant` plugin or on `mail`.
- `mailadmin/.venv` was created as a copy of the `Systutor-oss` venv and carries two
  editable distributions (`systutor_core` and `systutor_oss`). **Tests must be run with
  `PYTHONPATH=src`** or the wrong project's `src/systutor` is imported and the kernel
  tests are not testing this repo. See A.SPEC 0004.
