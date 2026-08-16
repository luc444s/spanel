# A.SPEC SP-0021 — Domain CRUD + frontend

## WHY

SP-0011 creó dominios (POST) y listó (GET). Pero no se pueden eliminar ni
modificar dominios, y no hay UI. Sin CRUD completo ni frontend, el admin
no puede gestionar dominios sin curl.

## WHAT

Transición observable: admin gestiona dominios desde la UI — agregar,
editar FQDN, eliminar. Traefik se actualiza automáticamente en cada
operación. DNS se configura externamente (Cloudflare, registrar, etc).

## SCOPE

### Backend (`plugins/proxy/backend/plugin.py`)

- `PATCH /domains/{id}` {fqdn} → actualiza FQDN en DB + reescribe
  traefik route con la lista actualizada de dominios del site.
- `DELETE /domains/{id}` → elimina dominio de DB + reescribe traefik
  route sin ese FQDN. Si era el último dominio, elimina el archivo
  de config de traefik.
- Refactor `_write_route()` → `_sync_site_route(docker, site)` que
  lee `domains_json` del site y reescribe el YAML completo.
- Helper `_remove_route(docker, site_name)` para eliminar archivo
  de config de traefik.

### Frontend (`plugins/proxy/frontend/`)

- `DomainsView.tsx` — tabla de dominios con:
  - Columnas: FQDN, Site, SSL status, Acciones
  - Botón "Agregar dominio" (modal/form inline)
  - Acciones por fila: editar FQDN, eliminar (con confirmación)
- `register.tsx` — registrar route `/domains` + nav item "Dominios"

### Endpoints finales

| Method | Path | Acción |
|--------|------|--------|
| GET | `/domains` | listar todos los dominios del tenant |
| POST | `/domains` | crear dominio + traefik route (SP-0011) |
| PATCH | `/domains/{id}` | editar FQDN + actualizar traefik |
| DELETE | `/domains/{id}` | eliminar dominio + actualizar traefik |

## OUT OF SCOPE

- DNS server local (no aplica para producción VPS)
- SSL/letsencrypt automatizado (traefik lo maneja con http-01)
- Wildcard certs
- Gestión DNS externa (API Cloudflare, etc) — futuro

## CONTRACT

- PRE: proxy plugin instalado y habilitado; traefik corriendo en remoto.
- POST: CRUD completo de dominios; traefik refleja estado real de DB;
  dominio duplicado → 409; dominio inexistente → 404; fallo traefik →
  502 sin romper la API.
- PATCH: si FQDN nuevo ya existe en otro domain → 409.
- DELETE: si domain no pertenece al tenant → 404.

## INVARIANTS

```yaml
invariants:
  - Tenant isolation: solo dominios del tenant actual son visibles.
  - Dominio duplicado (mismo FQDN) → 409.
  - Traefik config SIEMPRE refleja el estado de DB.
  - Kernel MUST NOT ser modificado.
  - docker_infra plugin se importa, no se modifica.
```

## VERIFICATION

```bash
# crear dominio
curl -X POST http://localhost:8001/api/v1/plugins/proxy/domains \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"fqdn":"test.example.com","site_id":"<id>"}'

# listar
curl http://localhost:8001/api/v1/plugins/proxy/domains \
  -H "Authorization: Bearer $TOKEN"

# editar
curl -X PATCH http://localhost:8001/api/v1/plugins/proxy/domains/<id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"fqdn":"new.example.com"}'

# eliminar
curl -X DELETE http://localhost:8001/api/v1/plugins/proxy/domains/<id> \
  -H "Authorization: Bearer $TOKEN"

# verificar traefik route actualizado
docker exec spanel-traefik cat /etc/traefik/dynamic/<site>.yml
```

## ROLLBACK

Eliminar endpoints PATCH/DELETE; frontend DomainsView y register
revertido a vacío. Tabla `hosting_domain` intacta (data no se pierde).

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/proxy/backend/plugin.py
    - plugins/proxy/frontend/register.tsx
    - plugins/proxy/frontend/DomainsView.tsx
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
    - plugins/hosting/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - proxy.domains
    - traefik.dynamic_config
  indirect:
    - hosting.domains_json (sync)
  must_not_affect:
    - kernel.auth
    - docker_infra
    - hosting.site_provisioning
```

## Traceability

- Requirement: extensión de SP-0011 (proxy domains).
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
