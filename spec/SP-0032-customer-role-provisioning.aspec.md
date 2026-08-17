# A.SPEC SP-0032 — Customer role provisioning

## WHY

Seed actual solo garantiza rol `admin`. Además, categorías de usuario del
core mezclan roles auto-asignados con `role_ids` enviados por UI. Sin un
modelo explícito para customer, cada tenant tendría que armar su rol a mano
o podría terminar combinando permisos customer con permisos core/admin.

## WHAT

Transición observable: cada tenant de Spanel dispone de un rol local
`customer`, sembrado de forma idempotente con whitelist exacta de permisos
customer. Desde gestión de usuarios, admin puede crear/editar usuarios de
categoría `customer`; backend canonicaliza asignación a ese rol del tenant y
evita cualquier bleed hacia roles de otro tenant o permisos core/infra.

## SCOPE

### Seed de rol customer (`vendor/systutor-core/src/systutor/api/seed.py`)

- Extender seed que hoy crea `admin` para también asegurar rol
  `customer` en el tenant seed actual.
- Rol `customer` usa esta whitelist exacta de permisos Spanel:
  - `hosting.sites.read`
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
  - `proxy.domains.read`
  - `proxy.domains.create`
  - `proxy.domains.update`
  - `proxy.domains.delete`
  - `mail.server.read`
  - `mail.domains.read`
  - `mail.domains.create`
  - `mail.domains.delete`
  - `mail.mailboxes.read`
  - `mail.mailboxes.create`
  - `mail.mailboxes.delete`
- `hosting.sites.adopt`, `proxy.traefik.read`, `mail.server.provision`,
  `docker_infra.*` y cualquier `core.*` quedan fuera del rol customer.
- Seed es idempotente: si rol `customer` ya existe en tenant, se actualiza
  su set de permisos en vez de duplicarlo.

### Registro de categoría customer (`vendor/systutor-core/app/main.py`)

- Registrar `register_user_category("customer", "Customer", ["customer"])`
  durante bootstrap del API para que `/api/v1/core/users/categories` la
  exponga sin endpoint nuevo.
- No se agrega CRUD de tenants: primera iteración cubre tenant seed actual y
  deja helper reutilizable para futuro flujo interno de alta de tenants.

### Canonicalización server-side (`vendor/systutor-core/src/systutor/api/v1/core/services/users.py`)

- En create/update, si `category == "customer"`:
  - resolver rol por nombre `customer` dentro de `tenant_id` actual
  - ignorar `role_ids` extra enviados por cliente
  - reemplazar asignación final por ese único rol customer del tenant
- Si rol `customer` no existe en ese tenant, responder error explícito en vez
  de crear usuario parcialmente configurado.
- Si `category` deja de ser `customer`, flujo vuelve a reglas normales de
  roles explícitos; no se reusa ningun `role_id` de otro tenant.

### UX de asignación (`vendor/systutor-shell/src/admin/users.tsx`)

- Al elegir categoría `customer`, formulario:
  - bloquea edición manual de roles
  - muestra nota de que customer es tenant-scoped y no incluye core/infra
  - limpia roles manuales residuales para que UI refleje la canonicalización
- En edición de un customer existente, el formulario conserva estado bloqueado
  mientras `category == "customer"`.

## OUT OF SCOPE

- Enforcement por endpoint en plugins (SP-0030).
- Navegación/visibilidad customer en shell (SP-0031).
- Nuevos endpoints de tenant o migraciones de schema.

## CONTRACT

- PRE: catálogo de permisos de SP-0030 disponible en manifests/plugins.
- POST: tenant seed tiene rol `customer` visible en `/api/v1/core/roles`.
- POST: crear usuario con `category: "customer"` produce usuario con solo rol
  `customer` del tenant actual, aunque cliente envíe `role_ids` extra.
- POST: login de customer refleja whitelist exacta en `/api/v1/auth/me` y no
  incluye `core.*` ni `docker_infra.*`.

## INVARIANTS

```yaml
invariants:
  - Rol `customer` MUST existir por tenant, nunca como rol global compartido.
  - Asignacion customer MUST resolverse por `tenant_id` + nombre `customer`.
  - Categoria `customer` MUST NOT terminar con permisos `core.*` ni `docker_infra.*`.
  - `orquestador_ardi_postgres` MUST seguir fuera de alcance porque customer no recibe adopt ni infra.
  - Cambio MUST NOT requerir migraciones ni nuevas tablas.
```

## VERIFICATION

```bash
# seed / role visible
curl -s http://127.0.0.1:8001/api/v1/core/roles \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[] | select(.name=="customer")'

# crear customer sin roles manuales (o con roles extra: backend debe canonicalizar)
curl -s -X POST http://127.0.0.1:8001/api/v1/core/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"Customer Demo",
    "email":"customer@example.com",
    "password":"ChangeMe123!",
    "category":"customer",
    "role_ids":[],
    "warehouse_ids":[]
  }' | jq

# login customer y revisar permisos efectivos
curl -s -X POST http://127.0.0.1:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"customer@example.com","password":"ChangeMe123!"}' | jq '.user.permissions'
```

## ROLLBACK

Revertir seed/category canonicalization y UX del formulario. Rol `customer`
ya creado puede dejarse inactivo o limpiarse manualmente desde roles.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/app/main.py
    - vendor/systutor-core/src/systutor/api/seed.py
    - vendor/systutor-core/src/systutor/api/v1/core/services/users.py
    - vendor/systutor-shell/src/admin/users.tsx
    - vendor/systutor-core/tests/**
  prohibited:
    - plugins/**/backend/**
    - apps/web/**
    - vendor/systutor-core/src/systutor/kernel/permissions/models.py
    - .postgres/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - core.seed.customer_role
    - core.user_category.customer
    - shell.users_form
  indirect:
    - auth.me.permissions
    - customer.login_profile
  must_not_affect:
    - core.admin_seed
    - db.schema
    - plugin.runtime
```

## Traceability

- Requirement: customer role provisioning por tenant.
- Inputs: `vendor/systutor-core/src/systutor/api/seed.py`,
  `vendor/systutor-core/src/systutor/api/v1/core/services/users.py`,
  `vendor/systutor-shell/src/admin/users.tsx`.
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
