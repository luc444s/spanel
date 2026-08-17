# A.SPEC SP-0027 — Dashboard containers + resources

## WHY

Spanel ya opera sobre Docker remoto, pero no ofrece una vista panel para
ver qué containers existen, cuáles están corriendo y cuánto consumen. Sin
esa visibilidad, lifecycle y adopción quedan ciegos y el admin vuelve a
`docker ps` y `docker stats` por SSH.

## WHAT

Transición observable: admin abre un dashboard de infraestructura dentro de
Spanel y ve snapshot de containers remotos con estado, CPU y memoria, más
atajos a Sites ya adoptados.

## SCOPE

### Backend (`plugins/docker_infra/backend/plugin.py`)

- Nuevo endpoint `GET /api/v1/plugins/docker_infra/containers/stats`:
  - usa `docker stats --no-stream --format json` en host remoto.
  - retorna snapshot por container:
    `{name, cpu_percent, mem_usage, mem_percent, net_io, block_io, pids}`.
  - si un container aparece en `ps` pero no en `stats`, devolver fila con
    recursos nulos en vez de omitirlo.
- `GET /containers` existente permanece como fuente de `name`, `image`,
  `status`.

### Frontend (`plugins/docker_infra/frontend/`)

- Corregir `register.ts` → `register.tsx` con `pluginId: 'docker_infra'`
  (match real del manifest) para que el plugin frontend cargue.
- Nueva ruta `/p/docker_infra/containers` + nav item `Infra`.
- Nueva vista `ContainersDashboardView.tsx`:
  - cards resumen: total containers, running, stopped, adoptados.
  - tabla responsive con columnas: nombre, imagen, estado, CPU, memoria,
    adoptado/no adoptado, link a Site si existe.
  - botón `Actualizar` manual; sin polling continuo.
  - badge especial para `orquestador_ardi_postgres` como protegido.
- Cruce frontend con `GET /api/v1/plugins/hosting/sites` para marcar qué
  containers ya pertenecen a un Site.

## OUT OF SCOPE

- Series temporales, historiales, alertas, gráficas Prometheus/Grafana.
- Acciones mutantes desde este dashboard.
- Métricas de disco por volumen o red por interfaz detallada.

## CONTRACT

- PRE: plugin docker_infra habilitado; docker remoto accesible.
- POST: dashboard visible en UI, con snapshot consistente entre `ps` y
  `stats`; errores remotos se muestran sin romper navegación.
- Si `stats` falla pero `ps` responde, la UI puede seguir mostrando lista
  base con métricas vacías y aviso degradado.
- Site adoptado debe enlazar a `/p/hosting/sites/{id}` cuando exista.

## INVARIANTS

```yaml
invariants:
  - Dashboard MUST ser read-only.
  - `orquestador_ardi_postgres` MUST mostrarse como protegido, nunca como accionable.
  - Plugin frontend MUST registrarse con `pluginId` exacto `docker_infra`.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/plugins/docker_infra/containers -H "Authorization: Bearer $TOKEN" | jq '.[0]'
curl -s http://127.0.0.1:8001/api/v1/plugins/docker_infra/containers/stats -H "Authorization: Bearer $TOKEN" | jq '.[0]'
# UI: /p/docker_infra/containers muestra resumen + tabla con CPU/RAM
```

## ROLLBACK

Revertir endpoint `containers/stats` y frontend `docker_infra`. El adapter
base `GET /containers` / `GET /containers/{name}/inspect` queda intacto.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/docker_infra/backend/plugin.py
    - plugins/docker_infra/frontend/**
    - plugins/docker_infra/plugin.json
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/hosting/backend/**
    - plugins/proxy/**
    - plugins/mail/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - docker_infra.dashboard
    - docker_infra.container_stats
  indirect:
    - hosting.site.cross_links
  must_not_affect:
    - kernel.auth
    - docker.remote.mutations
    - proxy.domains
    - mail plugin
```

## Traceability

- Requirement: arquitectura-base.md §3, §5, §9.
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
