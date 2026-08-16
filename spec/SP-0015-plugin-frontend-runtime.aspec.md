# A.SPEC SP-0015 — Plugin frontend runtime

## WHY

Las vistas de dominio (sitios, mail, backups) no pertenecen al menú del
host; cada plugin trae su UI vía `frontend_entrypoint`. Hoy el kernel
tiene el mecanismo en el manifest pero el host no lo consume: sin esto,
la UI de hosting/mail iría a parar al nav principal (viola la regla del
usuario: docker-list vive fuera del navegador).

## WHAT

Una transición observable: el host carga los `frontend_entrypoint` de los
plugins habilitados; las rutas y entradas de navegación declaradas por
cada plugin aparecen dinámicamente en el shell, fuera del menú fijo.

## SCOPE

- Host (`apps/web`): módulo que pide `/api/v1/core/plugins` (habilitados
  con frontend_entrypoint) y registra en react-router las rutas
  devueltas por el `register.ts` de cada plugin.
- `GET /api/v1/plugins/{id}/frontend-manifest` (kernel? NO — plugin
  hosting/docker-infra exponen su propia declaración) — primera iteración:
  el host importa `frontend/register.ts` del plugin vía ruta estática de
  vite (alias `@spanel-plugin/<id>`), sin descarga dinámica.
- Patrón `register.ts`: `registerPlugin()` → {pluginId, routes[],
  navigation[], widgets[]} — rutas montadas bajo `/p/<pluginId>/...`.
- Menú host muestra entradas `navigation[]` agrupadas por plugin.

## OUT OF SCOPE

- Carga remota de bundles JS (plugin entregado como artefacto HTTP) —
  spec futura; primera iteración es build-time (plugin local al repo).
- Hot reload de plugins en frontend.

## CONTRACT

- PRE: plugin habilitado con `frontend_entrypoint` válido.
- POST: navegación del plugin visible; ruta `/p/<pluginId>/...` renderiza
  su vista; plugin deshabilitado no aparece ni monta rutas; fallo de un
  plugin no rompe el shell (se loguea y se omite).

## INVARIANTS

```yaml
invariants:
  - Menú fijo del host (plugins/roles/usuarios/branches) MUST no cambiar.
  - Shell MUST seguir funcionando sin plugins.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
cd apps/web && npm run build
# hosting con register.ts de prueba: nav "Sitios" visible en /p/hosting/*
```

## ROLLBACK

`git checkout apps/web` + deshabilitar plugin en runtime.

## Change Surface

```yaml
change_surface:
  allowed:
    - apps/web/src/**
    - apps/web/vite.config.ts
    - plugins/hosting/frontend/**
  prohibited:
    - vendor/**
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - web.shell.nav
  indirect:
    - web.plugins_view
  must_not_affect:
    - kernel.auth
    - api.8001
```

## Traceability

- Requirement: arquitectura-base.md §9.
- Commit:
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
