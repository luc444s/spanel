# A.SPEC SP-0007 — List remote Docker containers

## WHY

Spanel gestiona sitios web sobre Docker remoto (host x86 en Tailscale,
`lucas@100.67.5.50`). El kernel no conoce Docker; la integración vive en
un plugin. Primer paso: el host app puede consultar containers del docker
remoto desde su API, sin docker local (Termux solo tiene ssh).

## WHAT

Una transición observable: usuario autenticado consulta
`GET /api/v1/plugins/hosting/containers` y recibe la lista viva de containers del
docker remoto (nombre, imagen, estado), obtenida via SSH.

## SCOPE

- Plugin `hosting` en `plugins/hosting/` (manifest + backend).
- `backend/docker_adapter.py`: wrapper subprocess sobre
  `sshpass ssh <host> docker ps --format '{{json .}}'`.
- Credenciales por env (`SPANEL_DOCKER_SSH_*`) — nunca en código ni specs.
- Router FastAPI `GET /containers` (mounted en `/api/v1/plugins/hosting/containers`) con auth kernel
  (`get_current_user`).
- Permiso declarado `hosting.containers.read` (asignación a roles queda
  para specs futuras).

## OUT OF SCOPE

- Operaciones de ciclo de vida (start/stop/logs) — SP-0010.
- Discovery/adopción como recursos — SP-0008/0009.
- Docker socket local, docker-py, agentes remotos.
- Persistencia de la lista (es read-only, en vivo).

## CONTRACT

- PRE: API kernel arriba; ssh+sshpass en el host app; credenciales en
  env; docker host alcanzable por Tailscale.
- POST: `GET /api/v1/plugins/hosting/containers` sin token → 401; con token →
  200 JSON `[{name, image, status}]` igual a `docker ps` remoto; fallo de
  ssh/docker → 502 con mensaje, sin romper la API.

## INVARIANTS

```yaml
invariants:
  - API kernel (vendor/systutor-core) MUST NOT be modified.
  - Otros endpoints MUST seguir respondiendo si docker remoto cae.
  - Credenciales SSH MUST NOT aparecer en logs ni respuestas.
  - Containers remotos MUST NOT ser modificados por esta spec.
```

## VERIFICATION

```bash
sshpass -p "$PW" ssh lucas@100.67.5.50 'docker ps --format "{{.Names}}"'
TOKEN=$(curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"ChangeMe123!"}' | jq -r .access_token)
curl -s http://127.0.0.1:8001/api/v1/hosting/containers -H "Authorization: Bearer $TOKEN" | jq
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/api/v1/hosting/containers  # 401
```

## ROLLBACK

`git checkout plugins/` — sin cambios de datos ni de docker remoto.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
  prohibited:
    - vendor/**
    - apps/web/**
    - spec/ (salvo esta A.SPEC)
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.containers.list
  indirect:
    - none
  must_not_affect:
    - api.8000 (ajeno)
    - kernel.auth
    - docker.remote (solo lectura)
```

## Traceability

- Requirement: "gestor simple de webs con containers docker" (kickoff
  producto 2026-08-16).
- Commit: root 3264496
- Deployment: `npm run services:no-reload` (recarga de plugin al boot).

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
