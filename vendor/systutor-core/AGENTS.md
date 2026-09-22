# AGENTS.md — systutor-core

Operating rules for AI agents working on the SYSTUTOR open source kernel.

## Principles

- The kernel stays small and contains no business domain logic. No
  logistics, crm, products, stock, sales, or commerce references in code,
  permissions, or configuration.
- Business logic belongs in host application plugins.
- Modules communicate through events, not direct coupling.
- Significant actions must be auditable.
- No file exceeds 600 lines; split before 400.
- Features require a spec and tests before a PR.
- The `systutor.*` namespace is the public API. Signature changes are
  breaking for host applications.

## Public surface

- `systutor.kernel` — auth, RBAC, tenancy, audit, events, plugin runtime,
  documents, signatures, tasks
- `systutor.core` — config (with `register_settings_factory`), database,
  lifecycle, errors, logging
- `systutor.api` — dependencies and kernel management APIs
- `systutor.contracts` — shared contracts
- `systutor.sdk` — plugin SDK

## Rules

1. `src/systutor` must not import from `app.*`; `app/` is the reference
   executable.
2. Do not add business fields to `Settings`; host applications add them by
   subclassing.
3. New kernel permissions use the `core.*` namespace and are added to
   `BASE_PERMISSIONS` in `systutor/api/seed.py`.
4. New kernel model migrations are written here with Alembic.
5. Every public API change updates `README.md`.

## Quality gates

- `ruff check .`
- `python3 -m pyright`
- `python3 -m pytest tests -q`

All must pass before commit. Tests use SQLite and `TestClient`; test
plugins are generated in temporary directories (`tests/conftest.py`).
