# A.SPEC SP-0009 — Adopt discovered container as site

## WHY

Spanel ve containers remotos (SP-0007/0008) pero no los integra: no hay
Site, tenant ni stack. "Llamar containers ya creados e integrarlos
simplemente" es la promesa central del producto.

## WHAT

Una transición observable: admin elige un container descubierto, lo
adopta; Spanel crea un Site persistido (tenant/branch del actor, stack
inferido, dominios desde labels traefik/caddy) y el container pasa de
`discovered` a `adopted`.

## SCOPE

- Migraciones del plugin `hosting`: tabla `site` (id, tenant_id,
  branch_id, stack, container_id, name, dominios JSON, estado).
- `POST /api/v1/plugins/hosting/sites/adopt` {container_name} →
  inspecciona (docker-infra), infiere stack por señales
  (arquitectura-base.md §5), persiste Site.
- `GET /api/v1/plugins/hosting/sites` lista sitios del tenant.
- Evento `site.adopted` + audit log kernel.
- Container adoptado NO se modifica.

## OUT OF SCOPE

- Lifecycle start/stop/logs (SP-0010), dominios/SSL (SP-0011).
- Provision de stacks (SP-0012), UI (SP-0015/0016).
- Adopción de compose multi-container (una spec futura).

## CONTRACT

- PRE: docker-infra habilitado; sesión admin con tenant.
- POST: Site visible en lista; stack inferido correcto para wordpress/
  php/db/proxy-only; dominios poblados desde labels si existen; segundo
  adopt del mismo container → 409; tenant isolation (solo ve sus sites).

## INVARIANTS

```yaml
invariants:
  - Containers remotos MUST NOT ser modificados por adopt.
  - Tenant isolation MUST permanecer intacta.
  - Kernel MUST NOT ser modificado.
  - Adopt de container inexistente MUST fallar sin efectos parciales.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/adopt -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"container_name":"osrm"}'
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/sites -H "Authorization: Bearer $TOKEN" | jq
```

## ROLLBACK

`git checkout plugins/hosting` + borrar filas de `site` (migración
down o SQL directo).

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
    - hosting.site.adopt
  indirect:
    - none
  must_not_affect:
    - kernel.auth
    - docker.remote
    - docker-infra.containers
```

## Traceability

- Requirement: arquitectura-base.md §1, §5 (adopt).
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
