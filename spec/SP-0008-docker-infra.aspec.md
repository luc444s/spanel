# A.SPEC SP-0008 — Docker infra plugin with shared adapter

## WHY

SP-0007 dejó el adapter docker (ssh→docker CLI) dentro del plugin
`hosting`. El adapter es infraestructura compartida: `hosting`, `proxy` y
`mail` lo necesitan. Acoplado a hosting, los otros plugins duplicarían
código o se colgarían de un dominio ajeno.

## WHAT

Una transición observable: el plugin `docker-infra` queda registrado y
habilitado, expone servicio de containers (list/ps/inspect/exec) y eventos
`container.discovered` / `container.state_changed`; `hosting` deja de
tener adapter propio y consume el de `docker-infra`.

## SCOPE

- Plugin `docker-infra` en `plugins/docker-infra/` (manifest + backend).
- `backend/adapter.py`: ssh→docker CLI (mover desde hosting + ampliar:
  `ps`, `inspect`, `exec`, `run`, `start`, `stop`, `restart`, `logs`,
  `stats`).
- `backend/service.py`: capa operaciones con timeouts y errores tipados.
- Registro de eventos `container.discovered`, `container.state_changed`.
- Router mínimo `GET /containers` (migra SP-0007) y `GET /containers/{id}/inspect`.
- Migración: plugin hosting pasa a consumir docker-infra (sin endpoints
  duplicados).

## OUT OF SCOPE

- Adopción como Site (SP-0009), lifecycle de sitios (SP-0010).
- Docker socket local, docker-py, agentes remotos.
- Persistencia de containers (siempre en vivo).

## CONTRACT

- PRE: plugin hosting instalado; API kernel con plugin runtime.
- POST: `docker-infra` instalado+habilitado; `GET /api/v1/plugins/
  docker-infra/containers` devuelve misma lista que SP-0007; credenciales
  por env; errores ssh/docker → 502 tipado.

## INVARIANTS

```yaml
invariants:
  - API kernel MUST NOT be modified.
  - Discovery MUST remain read-only (ps/inspect/stats no mutan).
  - Containers remotos MUST NOT ser modificados sin acción explícita.
  - Credenciales SSH MUST NOT aparecer en logs ni respuestas.
```

## VERIFICATION

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login -H "Content-Type: application/json" -d '{"email":"admin@example.com","password":"ChangeMe123!"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
curl -s http://127.0.0.1:8001/api/v1/plugins/docker-infra/containers -H "Authorization: Bearer $TOKEN" | jq
# hosting sin endpoints docker propios tras migración
```

## ROLLBACK

`git checkout plugins/` + reinstalar plugins en runtime.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/docker-infra/**
    - plugins/hosting/backend/**
  prohibited:
    - vendor/**
    - apps/web/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - docker-infra.containers
  indirect:
    - hosting.containers (migración de endpoint)
  must_not_affect:
    - kernel.auth
    - docker.remote (read-only)
```

## Traceability

- Requirement: arquitectura-base.md §3 (docker-infra) y §5 (discovery).
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
