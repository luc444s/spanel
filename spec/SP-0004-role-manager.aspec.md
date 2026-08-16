# A.SPEC SP-0004 — Manage roles from web shell

## WHY

RBAC del kernel gestiona roles por tenant con permisos declarativos
(`/api/v1/core/roles`, `/api/v1/core/permissions`). No hay vista para
crear, editar o habilitar/deshabilitar roles. El admin solo puede
operarlos por curl.

## WHAT

Una transición observable: usuario autenticado con rol admin ve la lista
de roles del tenant activo y puede crear roles nuevos, editarlos
(nombre/descripción/permisos) y habilitar/deshabilitarlos desde la UI.

## SCOPE

- `RolesView` genérica en `vendor/systutor-shell/src/admin/roles.tsx`.
- Ruta `/roles` en el host (menú existente de SP-0003).
- Tabla de roles: nombre, nº de permisos, estado, acciones.
- Formulario crear/editar (Dialog): nombre, permisos (multi-select desde
  `GET /api/v1/core/permissions`).
- Acciones: crear (`POST /api/v1/core/roles`), editar (`PATCH`),
  disable/enable (`POST /{id}/disable|enable`).
- Error visible por operación fallida; refresco tras cada cambio.

## OUT OF SCOPE

- Borrado de roles (endpoint no existe en v1 core).
- Asignación de roles a usuarios (SP-0005).
- Permisos custom por plugin (solo selección sobre catálogo existente).

## CONTRACT

- PRE: sesión admin válida; permisos kernel cargados.
- POST: rol creado aparece en lista; `PATCH` persiste cambios; disable
  impide uso (estado reflejado); catálogo de permisos nunca se modifica.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - Catálogo de permisos MUST permanecer intacto (read-only).
  - Rol admin del seed MUST NOT poder quedarse sin permisos core.
  - Tenant isolation MUST permanecer intacta.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/core/roles -H "Authorization: Bearer $TOKEN" | jq
# UI: crear rol "test" → aparece; editar → persiste; disable → estado cambia.
```

## ROLLBACK

`git checkout apps/web/src`; datos de prueba creados via UI se borran
manualmente (o se dejan — idempotencia del kernel).

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
    - web.roles_view
  indirect:
    - web.shell_menu
  must_not_affect:
    - api.8001
    - api.8000
    - db.spanel
    - auth.kernel
```

## Traceability

- Requirement: "gestor de roles" (consola de administración).
- Commit: shell 4cd7a2f / root (pendiente)
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
