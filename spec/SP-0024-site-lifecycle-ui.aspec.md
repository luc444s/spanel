# A.SPEC SP-0024 — Site lifecycle UI

## WHY

SP-0010 dejó lifecycle operativo en backend y SP-0016 agregó detalle del
Site, pero la UI todavía no permite operar start/stop/restart con feedback
real. Hoy el admin sigue dependiendo de curl para acciones básicas.

## WHAT

Transición observable: desde la UI de hosting, admin puede arrancar,
detener, reiniciar y refrescar estado de un Site; la vista refleja el
estado vivo del container y los errores remotos sin salir del panel.

## SCOPE

### Frontend (`plugins/hosting/frontend/`)

- `SitesView.tsx`:
  - agregar columna `Estado` con badge vivo (`running`, `exited`,
    `missing`, `unreachable`, etc).
  - agregar acciones por fila: `Start`, `Stop`, `Restart`, `Detalle`.
  - refresco manual de la lista (`Actualizar`) sin recargar toda la app.
  - deshabilitar botones mientras una operación está en vuelo para ese
    Site.
- `SiteDetailView.tsx`:
  - agregar toolbar lifecycle (`Start`, `Stop`, `Restart`, `Actualizar`).
  - mantener `Cargar logs`, pero mostrar estado de carga y último error en
    contexto de la acción actual.
  - después de cada acción exitosa, volver a leer `GET /sites/{id}` para
    reflejar estado vivo.
- `register.tsx` sin rutas nuevas; misma navegación `/p/hosting/sites` y
  `/p/hosting/sites/:id`.

### Backend mínimo requerido (`plugins/hosting/backend/plugin.py`)

- `GET /api/v1/plugins/hosting/sites` enriquecido con `container_status`
  consultando docker remoto en vivo, para que la tabla principal no tenga
  que disparar un `GET /sites/{id}` por cada fila.
- No se agregan endpoints lifecycle nuevos; se consumen los existentes:
  - `POST /sites/{id}/start`
  - `POST /sites/{id}/stop`
  - `POST /sites/{id}/restart`
  - `GET /sites/{id}`
  - `GET /sites/{id}/logs?tail=N`

## OUT OF SCOPE

- Polling continuo, websockets o eventos push.
- Stats CPU/RAM (SP-0027).
- Eliminación de Sites (SP-0025).
- Adopción/provisioning desde UI (SP-0026).

## CONTRACT

- PRE: plugin hosting habilitado; endpoints SP-0010/SP-0016 operativos.
- POST: lifecycle completo disponible desde UI; cada acción muestra estado
  de progreso y resultado; al terminar, lista/detalle muestran estado vivo
  del container.
- `403 container protegido` se muestra tal cual al usuario; no se oculta ni
  se convierte en éxito falso.
- Si docker remoto falla → error visible en la pantalla actual, sin dejar
  la UI en estado ambiguo.

## INVARIANTS

```yaml
invariants:
  - Semántica lifecycle existente MUST permanecer intacta; la UI no inventa acciones nuevas.
  - `orquestador_ardi_postgres` MUST seguir bloqueado por backend aunque exista botón visible en UI.
  - Estado vivo MUST venir de docker remoto, no de cache persistida.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/sites -H "Authorization: Bearer $TOKEN" | jq '.[0]'
# UI: /p/hosting/sites muestra badges de estado y botones Start/Stop/Restart
# UI: /p/hosting/sites/<id> permite lifecycle y refresca badge sin reload completo
```

## ROLLBACK

Revertir cambios en `plugins/hosting/frontend/` y quitar `container_status`
de `GET /sites`. Endpoints SP-0010 originales quedan intactos.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/frontend/**
    - plugins/hosting/backend/plugin.py
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker_infra/**
    - plugins/proxy/**
    - plugins/mail/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.lifecycle.ui
    - hosting.site.list
  indirect:
    - docker.remote.state_visibility
  must_not_affect:
    - kernel.auth
    - hosting.site.provision
    - proxy.domains
    - mail plugin
```

## Traceability

- Requirement: arquitectura-base.md §1, §3, §9; extensión de SP-0010 y SP-0016.
- Commit: (pending)
- Deployment: `npm run frontend` + `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
