# A.SPEC SP-0010 — Site lifecycle operations

## WHY

Sites adoptados son solo registros: no se pueden operar. Un gestor de
sitios sin start/stop/restart/logs no gestiona nada.

## WHAT

Una transición observable: sobre un Site adoptado, admin ejecuta
start/stop/restart y lee logs; el estado del container remoto cambia y la
lista lo refleja en vivo.

## SCOPE

- `POST /api/v1/plugins/hosting/sites/{id}/start|stop|restart` — delega
  a docker-infra (`start/stop/restart <container>`).
- `GET /api/v1/plugins/hosting/sites/{id}/logs?tail=N` — `docker logs`
  stream acotado.
- Estado persistido del Site se actualiza con estado real (consulta
  docker-infra).
- Eventos `container.state_changed` + audit por cada operación.

## OUT OF SCOPE

- Operaciones sobre containers no adoptados (solo sitios).
- Exec interactivo, stats, rediseño de estado persistido.
- Reinicio programado (cron) — spec futura.

## CONTRACT

- PRE: site adoptado (SP-0009); docker-infra habilitado.
- POST: start/stop/restart devuelven estado real del container;
  logs devuelven últimas N líneas; site inexistente/no adoptado → 404;
  fallo docker → 502 sin efectos parciales; toda operación auditada.

## INVARIANTS

```yaml
invariants:
  - Operaciones MUST ser solo sobre containers adoptados.
  - Tenant isolation MUST permanecer intacta (solo sitios propios).
  - Kernel MUST NOT ser modificado.
  - Logs MUST NOT exponer credenciales ni secretos del container.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/stop -H "Authorization: Bearer $TOKEN"
curl -s "http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/logs?tail=50" -H "Authorization: Bearer $TOKEN"
sshpass -p "$PW" ssh lucas@100.67.5.50 'docker ps -a --format "{{.Names}} {{.Status}}"'
```

## ROLLBACK

Sin cambios de datos estructurales; estado de containers se revierte con
la operación inversa (stop→start).

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.lifecycle
  indirect:
    - container.remote.state (intencional)
  must_not_affect:
    - kernel.auth
    - docker-infra.containers
    - sites adoptados no objetivo
```

## Traceability

- Requirement: arquitectura-base.md §1, §4 (Site).
- Commit:
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
