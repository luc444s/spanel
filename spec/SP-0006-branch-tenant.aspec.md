# A.SPEC SP-0006 — Manage branches and show tenant

## WHY

La API expone CRUD de branches (`/api/v1/core/branches`) con
enable/disable. El tenant activo viaja en el JWT (`/auth/me`). La consola
necesita gestionar branches y mostrar el tenant actual. Nota: el kernel
NO expone endpoints de gestión de tenants (crear/editar) — se maneja por
seed/servicio interno.

## WHAT

Una transición observable: admin ve el tenant activo (nombre/id desde
`/auth/me`) y la lista de branches del tenant; puede crear, editar y
habilitar/deshabilitar branches desde la UI.

## SCOPE

- `BranchesView` genérica en `vendor/systutor-shell/src/admin/branches.tsx`.
- Ruta `/branches` en el host (menú existente).
- Cabecera del Layout host con tenant actual (datos de `/auth/me`).
- Tabla de branches: nombre, código, estado, acciones.
- Formulario crear/editar (Dialog) según schema `CoreBranchRead`.
- Acciones: crear (`POST /api/v1/core/branches`), editar (`PATCH`),
  disable/enable (`POST /{id}/disable|enable`).
- Error visible por operación fallida; refresco tras cambios.

## OUT OF SCOPE

- Crear/editar/eliminar TENANTS (sin API en kernel v0.1).
- Switcher multi-tenant / cambio de tenant en sesión.
- Gestión de datos por branch (aislamiento es del kernel).

## CONTRACT

- PRE: sesión admin válida; tenant seed existente en `spanel`.
- POST: branches listados coinciden con API; crear/editar persiste;
  disable/enable cambia estado y la lista lo refleja; tenant mostrado es
  el del JWT.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - Tenant del usuario MUST NOT poder cambiarse desde la UI.
  - El branch del admin seed MUST NOT poder deshabilitarse sin advertencia
    explícita (bloquearía su contexto).
  - Tenant isolation MUST permanecer intacta.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/auth/me -H "Authorization: Bearer $TOKEN" | jq
curl -s http://127.0.0.1:8001/api/v1/core/branches -H "Authorization: Bearer $TOKEN" | jq
# UI: crear branch → aparece; disable → estado cambia; tenant visible.
```

## ROLLBACK

`git checkout apps/web/src`; branch de prueba deshabilitado vía UI si
estorbase.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-shell/src/**
    - apps/web/src/**
  prohibited:
    - vendor/systutor-core/**
    - apps/web/vite.config.ts
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - web.branches_view
  indirect:
    - web.shell_header
  must_not_affect:
    - api.8001
    - api.8000
    - db.spanel
    - tenancy.kernel
```

## Traceability

- Requirement: "branch/tenant" (consola de administración).
- Commit: shell 5cb0f92 / root (pendiente)
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
