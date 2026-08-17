# A.SPEC SP-0030 — Customer RBAC matrix + backend enforcement

## WHY

Spanel ya es multi-tenant sobre Docker remoto, pero permisos de plugins
siguen demasiado gruesos (`hosting.containers.*`, `proxy.routes.*`,
`mail.domains.*`, `docker_infra.*`) y varios endpoints solo exigen sesión
válida. Así no se puede abrir panel customer sin regalar superficie de
infraestructura o forzar al customer a usar permisos admin.

## WHAT

Transición observable: cada endpoint de `hosting`, `proxy`, `mail` y
`docker_infra` exige un permiso explícito vía `require_permission(...)`.
Un usuario con rol customer del tenant puede operar solo capacidades
permitidas sobre sus propios datos; `core.users.*`, `core.roles.*`,
`core.branches.*`, `core.plugin.*` y `docker_infra.*` responden 403.

## SCOPE

### Catálogo de permisos (`plugins/*/plugin.json`)

- `plugins/hosting/plugin.json` reemplaza permisos gruesos por:
  - `hosting.sites.read`
  - `hosting.sites.adopt`
  - `hosting.sites.provision`
  - `hosting.sites.update`
  - `hosting.sites.delete`
  - `hosting.runtime.read`
  - `hosting.runtime.manage`
  - `hosting.access.read`
  - `hosting.backups.read`
  - `hosting.backups.create`
  - `hosting.files.manage`
  - `hosting.sso.create`
- `plugins/proxy/plugin.json` declara:
  - `proxy.domains.read`
  - `proxy.domains.create`
  - `proxy.domains.update`
  - `proxy.domains.delete`
  - `proxy.traefik.read`
- `plugins/mail/plugin.json` declara:
  - `mail.server.read`
  - `mail.server.provision`
  - `mail.domains.read`
  - `mail.domains.create`
  - `mail.domains.delete`
  - `mail.mailboxes.read`
  - `mail.mailboxes.create`
  - `mail.mailboxes.delete`
- `plugins/docker_infra/plugin.json` mantiene:
  - `docker_infra.containers.read`
  - `docker_infra.containers.manage`

### Matriz de enforcement backend

#### Hosting (`plugins/hosting/backend/**`)

| Method | Path | Permiso requerido |
|--------|------|-------------------|
| GET | `/sites` | `hosting.sites.read` |
| GET | `/sites/{site_id}` | `hosting.sites.read` |
| POST | `/sites/adopt` | `hosting.sites.adopt` |
| POST | `/sites/provision/wordpress` | `hosting.sites.provision` |
| PATCH | `/sites/{site_id}` | `hosting.sites.update` |
| DELETE | `/sites/{site_id}` | `hosting.sites.delete` |
| POST | `/sites/{site_id}/start` | `hosting.runtime.manage` |
| POST | `/sites/{site_id}/stop` | `hosting.runtime.manage` |
| POST | `/sites/{site_id}/restart` | `hosting.runtime.manage` |
| GET | `/sites/{site_id}/logs` | `hosting.runtime.read` |
| GET | `/sites/{site_id}/access-logs` | `hosting.access.read` |
| POST | `/sites/{site_id}/backups` | `hosting.backups.create` |
| GET | `/sites/{site_id}/backups` | `hosting.backups.read` |
| POST | `/sites/{site_id}/files/ensure` | `hosting.files.manage` |
| POST | `/sites/{site_id}/sso` | `hosting.sso.create` |

#### Proxy (`plugins/proxy/backend/plugin.py`)

| Method | Path | Permiso requerido |
|--------|------|-------------------|
| GET | `/traefik/status` | `proxy.traefik.read` |
| GET | `/domains` | `proxy.domains.read` |
| POST | `/domains` | `proxy.domains.create` |
| PATCH | `/domains/{domain_id}` | `proxy.domains.update` |
| DELETE | `/domains/{domain_id}` | `proxy.domains.delete` |

#### Mail (`plugins/mail/backend/plugin.py`)

| Method | Path | Permiso requerido |
|--------|------|-------------------|
| GET | `/server/status` | `mail.server.read` |
| POST | `/server/ensure` | `mail.server.provision` |
| GET | `/domains` | `mail.domains.read` |
| POST | `/domains` | `mail.domains.create` |
| DELETE | `/domains/{domain_id}` | `mail.domains.delete` |
| GET | `/mailboxes` | `mail.mailboxes.read` |
| POST | `/mailboxes` | `mail.mailboxes.create` |
| DELETE | `/mailboxes/{mailbox_id}` | `mail.mailboxes.delete` |

#### Docker infra (`plugins/docker_infra/backend/plugin.py`)

| Method | Path | Permiso requerido |
|--------|------|-------------------|
| GET | `/containers` | `docker_infra.containers.read` |
| GET | `/containers/stats` | `docker_infra.containers.read` |
| GET | `/containers/{name}/inspect` | `docker_infra.containers.read` |

### Patrón de wiring

- Cada módulo define aliases `REQUIRE_* = Depends(require_permission("..."))`
  cerca del router.
- Los handlers dejan de depender solo de `get_current_user`; cuando se
  necesite `tenant_id`, el parámetro `user: User` sale de
  `require_permission(...)` para no duplicar autenticación.
- Se conservan filtros tenant ya existentes (`get_own_site`, joins por
  `tenant_id`, queries sobre `mail_domain`/`mailbox` del usuario actual).
- No se introducen checks manuales de permiso dentro del body del handler;
  el contrato queda declarado en la firma del endpoint.

## OUT OF SCOPE

- Seed/assignment del rol customer (SP-0032).
- Ocultar navegación o botones en frontend (SP-0031).
- Cambios de schema, tablas o migraciones.

## CONTRACT

- PRE: manifests de plugins sincronizados y usuario autenticado con JWT
  válido.
- POST: endpoint sin permiso requerido responde 403 aunque el usuario esté
  en mismo tenant; endpoint con permiso conserva comportamiento funcional
  actual (200/201/204/409/422/502 según caso).
- POST: búsquedas cross-tenant siguen devolviendo 404 o colección vacía,
  nunca datos de otro tenant.
- POST: `docker_infra` y adopción de containers pasan por permiso explícito;
  customer no puede enumerar ni inspeccionar infraestructura remota.

## INVARIANTS

```yaml
invariants:
  - Ningun endpoint de plugin MUST quedar protegido solo por `get_current_user`.
  - Tenant isolation MUST seguir dependiendo de `tenant_id` y `get_own_site`, no solo del permiso.
  - `orquestador_ardi_postgres` MUST seguir protegido y nunca volverse adoptable/mutable por este cambio.
  - Customer role MUST NOT incluir `core.*` ni `docker_infra.*`.
  - `vendor/systutor-core/src/systutor/kernel/auth/dependencies.py` MUST NOT cambiar semantica.
```

## VERIFICATION

```bash
# tokens de admin y customer del mismo tenant
ADMIN_TOKEN=...
CUSTOMER_TOKEN=...

# customer: lectura permitida, infra/core prohibidos
curl -i http://127.0.0.1:8001/api/v1/plugins/hosting/sites \
  -H "Authorization: Bearer $CUSTOMER_TOKEN"
curl -i http://127.0.0.1:8001/api/v1/plugins/docker_infra/containers \
  -H "Authorization: Bearer $CUSTOMER_TOKEN"
curl -i http://127.0.0.1:8001/api/v1/core/users \
  -H "Authorization: Bearer $CUSTOMER_TOKEN"

# customer: mutacion admin-only (adopt) prohibida
curl -i -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/adopt \
  -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"container_name":"orquestador_ardi_postgres"}'

# admin: mismo endpoint permitido, pero container operativo sigue protegido
curl -i -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/adopt \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"container_name":"orquestador_ardi_postgres"}'
```

## ROLLBACK

Revertir manifests y wiring `require_permission(...)` en plugins. Sin
migraciones ni datos persistidos para deshacer.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/backend/**
    - plugins/hosting/plugin.json
    - plugins/proxy/backend/plugin.py
    - plugins/proxy/plugin.json
    - plugins/mail/backend/plugin.py
    - plugins/mail/plugin.json
    - plugins/docker_infra/backend/plugin.py
  prohibited:
    - apps/web/**
    - vendor/systutor-core/src/**
    - .postgres/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.authz
    - proxy.authz
    - mail.authz
    - docker_infra.authz
  indirect:
    - customer.role_whitelist
    - web.route_visibility
  must_not_affect:
    - kernel.jwt_validation
    - db.spanel.schema
    - docker.remote.protected_container_guard
```

## Traceability

- Requirement: customer panel RBAC por tenant.
- Inputs: `README.md`, `arquitectura-base.md`, plugins actuales
  `hosting`/`proxy`/`mail`/`docker_infra`.
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
