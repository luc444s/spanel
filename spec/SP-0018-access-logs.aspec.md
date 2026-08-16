# A.SPEC SP-0018 — Access logs from traefik

## WHY

"También dirá de qué dirección viene" — aplicado a visitas: ¿quién
accede a cada sitio? Traefik ya ve todo el tráfico; Spanel debe exponer
los accesos por Site (IP cliente, path, status).

## WHAT

Una transición observable: en el detalle de un Site, admin ve accesos
recientes (IP cliente, método, path, status, timestamp) capturados por
traefik del docker remoto y consultados desde Spanel.

## SCOPE

- Proxy plugin: habilita traefik access logs en JSON (archivo o
  prometheus endpoint) acotado por host.
- Backend hosting: `GET /api/v1/plugins/hosting/sites/{id}/access-logs?
  since&limit` — lee logs del remoto vía docker-infra (docker exec
  `tail`/query sobre archivo json) y filtra por host/site.
- UI: tabla simple de accesos en el detalle del Site.

## OUT OF SCOPE

- Analítica (gráficas, agregados, unique visitors), persistencia de
  logs en Spanel, alertas.
- Retención/rotación de logs de traefik.

## CONTRACT

- PRE: traefik con access logs JSON activo; site con dominio ruteado
  (SP-0011).
- POST: accesos recientes del dominio visibles con IP cliente; sin
  dominio → lista vacía con mensaje; logs de otros tenants inaccesibles;
  límites por query (default 100).

## INVARIANTS

```yaml
invariants:
  - Logs de otros tenants MUST NO exponerse.
  - Lectura de logs MUST ser read-only sobre el remoto.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
curl -sI https://<dominio>          # genera un acceso
curl -s "http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/access-logs?limit=10" -H "Authorization: Bearer $TOKEN" | jq
```

## ROLLBACK

Desactivar access logs en traefik + `git checkout plugins/`.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/proxy/**
    - plugins/hosting/**
    - apps/web/src/**
  prohibited:
    - vendor/**
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - proxy.access_logs
  indirect:
    - traefik.config (remoto)
  must_not_affect:
    - kernel.auth
    - routing
    - logs de tenants ajenos
```

## Traceability

- Requirement: arquitectura-base.md §7 (pedido explícito del usuario).
- Commit: root (pendiente)
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
