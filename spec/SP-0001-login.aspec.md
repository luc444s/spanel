# A.SPEC SP-0001 — Authenticate user from web shell

## WHY

El frontend shell mínimo muestra solo health-check. No existe forma de
autenticarse desde el navegador. La API kernel ya expone `POST
/api/v1/auth/login` (JWT) y `GET /api/v1/auth/me`, y el seed demo creó
`admin@example.com` / `ChangeMe123!` en la DB `spanel`. Sin login, ninguna
vista de gestión (plugins, roles, usuarios, branches) es accesible.

## WHAT

Una sola transición observable: usuario ingresa email+password en pantalla
de login, recibe JWT, el token queda persistido y el frontend muestra la
sesión autenticada (`/auth/me`) en lugar de la pantalla de login.

## SCOPE

- Vistas/tipos genéricos en `vendor/systutor-shell/src/` (auth/token,
  api/types, admin/login) — reutilizables por cualquier host SYSTUTOR.
- Composición en `apps/web` (App.tsx): gate auth, título "Spanel".
- Persistencia del token (localStorage) + `setTokenProvider` del shell.
- Render condicional: sin token → login; con token → shell principal.
- Fetch `GET /api/v1/auth/me` para validar sesión al recargar.
- Error visible (Alert) ante credenciales inválidas o API caída.

## OUT OF SCOPE

- Registro de usuarios, reset de password, 2FA, refresh tokens.
- Logout (A.SPEC SP-0002).
- Gestor de plugins (SP-0003), roles (SP-0004), usuarios (SP-0005),
  branches/tenants (SP-0006).
- React Router — el render condicional alcanza; router llega con las
  vistas de gestión.

## CONTRACT

- PRE: API en `http://127.0.0.1:8001` arriba; seed ejecutado en `spanel`
  (`admin@example.com` / `ChangeMe123!`); CORS origin 5175 permitido.
- POST: token guardado en localStorage; `apiRequest` envía
  `Authorization: Bearer <token>`; `GET /api/v1/auth/me` responde 200 con
  el perfil del usuario; credenciales incorrectas → 401 → Alert visible,
  NO se persiste token.

## INVARIANTS

```yaml
invariants:
  - API kernel (vendor/systutor-core) MUST NOT be modified.
  - Health-check del shell MUST seguir funcionando sin token.
  - Falla de login MUST NOT persistir token ni mostrar credenciales.
  - API en 8001 y el uvicorn del usuario en 8000 MUST NOT be affected.
```

## VERIFICATION

```bash
cd apps/web && npm run build          # tsc -b + vite build sin errores
curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"ChangeMe123!"}' | jq .access_token
curl -s http://127.0.0.1:8001/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
# UI: credenciales malas → Alert de error; buenas → perfil visible.
```

## ROLLBACK

`git checkout` en ambos repos (shell submodule y root) de los archivos
listados en Change Surface. Sin migraciones ni cambios de datos.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-shell/src/**
    - apps/web/src/**
    - apps/web/tsconfig.app.json
  prohibited:
    - vendor/systutor-core/**
    - apps/web/vite.config.ts
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - web.shell.render
  indirect:
    - none
  must_not_affect:
    - api.8001
    - api.8000 (trabajo ajeno)
    - db.spanel
    - web.health
```

## Traceability

- Requirement: "frontend shell mínimo con autenticación" (pedido del
  usuario 2026-08-16).
- Commit:
- Deployment: `npm run frontend` (5175) + `npm run services` (8001).

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
