# A.SPEC SP-0005 — Manage users from web shell

## WHY

El kernel expone CRUD de usuarios (`/api/v1/core/users` + categories) con
enable/disable. No hay vista de gestión: no se pueden crear usuarios,
asignarles roles ni deshabilitarlos sin curl.

## WHAT

Una transición observable: admin ve la lista de usuarios del tenant y
puede crear usuarios, editar sus datos, asignar/desasignar roles y
habilitar/deshabilitarlos desde la UI.

## SCOPE

- `UsersView` genérica en `vendor/systutor-shell/src/admin/users.tsx`.
- Ruta `/users` en el host (menú existente).
- Tabla: nombre, email, categoría, estado, nº roles, acciones.
- Formulario crear/editar (Dialog): nombre, email, password (solo en
  creación), categoría (`GET /api/v1/core/users/categories`), roles
  (multi-select con roles de SP-0004).
- Acciones: crear (`POST /api/v1/core/users`), editar (`PATCH`),
  disable/enable (`POST /{id}/disable|enable`).
- Error visible por operación fallida; refresco tras cambios.

## OUT OF SCOPE

- Borrado de usuarios (no existe en v1 core; legacy delete NO se usa).
- Cambio de password de usuario existente, reset por email.
- Impersonación / auditoría de sesiones de usuario.

## CONTRACT

- PRE: sesión admin válida; roles existentes (seed admin).
- POST: usuario creado visible en lista; `PATCH` persiste; disable
  bloquea login del usuario (kernel lo impone); asignación de roles se
  refleja en `GET /api/v1/core/users/{id}`.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - El admin seed (admin@example.com) MUST NOT poder deshabilitarse a sí
    mismo desde la UI.
  - Password HASHEADA siempre: nunca se muestra ni se reenvía.
  - Tenant isolation MUST permanecer intacta.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/core/users -H "Authorization: Bearer $TOKEN" | jq
# UI: crear usuario → aparece; asignar rol → persiste; disable → login falla.
```

## ROLLBACK

`git checkout apps/web/src`; usuario de prueba deshabilitado vía UI si
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
    - web.users_view
  indirect:
    - auth.kernel.login (solo por disable de usuarios de prueba)
  must_not_affect:
    - api.8001
    - api.8000
    - db.spanel
    - seed_admin
```

## Traceability

- Requirement: "gestor de usuarios" (consola de administración).
- Commit: shell 598a2b8 / root b0572d8
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
