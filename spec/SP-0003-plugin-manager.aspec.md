# A.SPEC SP-0003 — Manage plugins from web shell

## WHY

El kernel monta plugins en runtime (instalar, habilitar, deshabilitar,
migrar, desinstalar) y expone `GET/POST /api/v1/core/plugins*`. Hoy esa
gestión solo es posible por curl. El frontend necesita la vista mínima de
gestor de plugins.

## WHAT

Una transición observable: usuario autenticado ve la lista de plugins del
runtime y puede ejecutar acciones de ciclo de vida (install, enable,
disable, uninstall, migrate) desde la UI, con estado visible por plugin.

## SCOPE

- `PluginsView` genérica en `vendor/systutor-shell/src/admin/plugins.tsx`.
- Ruta `/plugins` en el host (se introduce `react-router-dom` en apps/web).
- Tabla de plugins: nombre, versión, estado, acciones por estado
  (install, enable, disable, migrate, uninstall).
- Refresco de lista tras cada acción; error visible (Alert).
- Navegación: Layout host con menú (Plugins | Roles | Usuarios | Branches)
  — vistas pendientes muestran placeholder hasta sus specs.

## OUT OF SCOPE

- Edición de manifiestos, creación de plugins nuevos, logs por plugin.
- Página de detalle de plugin con permisos/eventos (SP futura).
- Gestor de roles/usuarios/branches (SP-0004/5/6).

## CONTRACT

- PRE: SP-0001 y SP-0002 completas; sesión con rol admin (seed); API 8001
  arriba con plugin runtime persistido (DB `spanel`).
- POST: lista coincide con `GET /api/v1/core/plugins`; cada acción llama
  el endpoint correspondiente (`POST /{id}/install|enable|disable|
  uninstall|migrate`); la lista se refresca y refleja el nuevo estado;
  errores 4xx/5xx se muestran sin romper la vista.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - Permisos/eventos declarados por plugins MUST NOT alterarse desde UI.
  - Tenant isolation del kernel MUST permanecer intacta.
  - Health del shell MUST seguir funcionando.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/core/plugins -H "Authorization: Bearer $TOKEN" | jq length
# UI: tabla lista plugins; disable → estado cambia; enable → vuelve.
```

## ROLLBACK

`git checkout apps/web/src apps/web/package.json` y `npm install` para
restaurar dependencias previas.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-shell/src/**
    - apps/web/src/**
    - apps/web/package.json   # react-router-dom
  prohibited:
    - vendor/systutor-core/**
    - apps/web/vite.config.ts
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - web.plugins_view
  indirect:
    - web.session (router reemplaza render condicional simple)
  must_not_affect:
    - api.8001
    - api.8000
    - db.spanel
    - plugin_runtime.kernel
```

## Traceability

- Requirement: "gestor de plugins" (primera iteración de consola).
- Commit: shell 5563fa4 / root (pendiente)
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
