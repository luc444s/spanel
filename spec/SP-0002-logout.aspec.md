# A.SPEC SP-0002 — Terminate user session

## WHY

Con JWT persistido (SP-0001), no existe forma de cerrar sesión desde el
frontend. La API kernel no expone endpoint de logout (JWT stateless) — el
cierre es cliente-side: descartar token.

## WHAT

Una transición observable: usuario autenticado presiona "Cerrar sesión" y
vuelve a la pantalla de login; el token se elimina del storage y toda
petición posterior deja de enviarlo.

## SCOPE

- `LogoutButton` genérico en `vendor/systutor-shell/src/admin/logout.tsx`.
- Composición en `apps/web` (App.tsx): botón en el shell principal.
- Eliminación del token de localStorage + `setTokenProvider(() => null)`.
- Vuelta inmediata al render de login.

## OUT OF SCOPE

- Revocación server-side / blacklist de JWT (no existe en kernel).
- Expiración/refresh de tokens.
- Confirmación modal (SP futura).

## CONTRACT

- PRE: sesión activa (token presente y válido).
- POST: token ausente de localStorage; `GET /api/v1/auth/me` sin header
  responde 401; la UI muestra login; recargar la página mantiene logout.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - Sesión del uvicorn ajeno (8000) MUST NOT be affected.
  - Logout MUST NOT limpiar datos de otra app del localStorage
    (solo la clave propia del token).
```

## VERIFICATION

```bash
cd apps/web && npm run build
# UI: login → logout → login visible → recarga → sigue login.
# localStorage sin token tras logout.
```

## ROLLBACK

`git checkout apps/web/src` — sin datos server-side que revertir.

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
    - web.session
  indirect:
    - none
  must_not_affect:
    - api.8001
    - api.8000
    - db.spanel
```

## Traceability

- Requirement: sesión de usuario completa (SP-0001 + logout).
- Commit: shell 469e781 / root (pendiente)
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
