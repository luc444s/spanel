# A.SPEC tenant-plugin — Create tenant management plugin

> `risk: low` — CRUD sobre tabla existente, sin migraciones nuevas, sin tocar core.

## WHY

El sistema multi-tenant ya existe pero no hay interfaz para gestionar tenants. El superadmin necesita crear, editar y asignar usuarios a tenants desde el UI, sin tocar la BD manualmente.

## WHAT

Un plugin `tenant` que expone CRUD de tenants (nombre, slug, domain) y asignación de usuarios, accesible solo para `is_superadmin=true`. DMS domain se configura desde esta interfaz.

## SCOPE

- Plugin `plugins/tenant/` con backend y frontend
- Endpoints: list, create, update domain, assign/remove user
- Página frontend: tabla de tenants, modales para crear/editar, panel de gestión de usuarios
- Sidebar: sección "Administración" visible solo para superadmin
- Permisos: `tenant.tenants.read`, `tenant.tenants.manage`

## OUT OF SCOPE

- Branch management por tenant
- Tenant deletion
- Tenant activation/deactivation
- Audit events para tenants
- Bulk user assignment
- Migraciones (usa tabla `tenants` existente)

## CONTRACT

- Precondición: superadmin autenticado con `is_superadmin=true`
- Postcondición: tenant creado con domain configurado, usuario asignado al tenant
- Cada tenant tiene un `domain` único (constraint UNIQUE ya existe)

## INVARIANTS

```yaml
invariants:
  - Solo superadmin puede acceder a los endpoints del plugin
  - Tenant slug es único (constraint existente en BD)
  - Tenant domain es único (constraint existente en BD)
  - Un usuario pertenece a un solo tenant a la vez
  - No se borran tenants existentes
  - No se modifican tenants de otros superadmins
```

## VERIFICATION

```bash
# 1. Plugin se carga sin errores
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/core/plugins | jq '.[] | select(.plugin_id=="tenant")'

# 2. Listar tenants (superadmin)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/plugins/tenant/tenants

# 3. Crear tenant
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Test","slug":"test","domain":"test.com"}' \
  http://localhost:8000/api/v1/plugins/tenant/tenants

# 4. Verificar en BD
psql -U postgres -d sysadmin -c "SELECT slug, domain FROM tenants WHERE slug='test';"
```

## ROLLBACK

Reversible: eliminar tenant creado manually si es necesario. No hay migraciones que revertir.

```bash
psql -U postgres -d sysadmin -c "DELETE FROM tenants WHERE slug='test';"
```

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/tenant/ (nuevo directorio)
    - apps/web/src/shared/layout/Sidebar.tsx (agregar link admin)
  prohibited:
    - src/systutor/kernel/ (no tocar core)
    - src/systutor/core/ (no tocar core)
    - Base de datos (no CREATE TABLE)
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - plugins/tenant/
  indirect:
    - Sidebar.tsx (nuevo link condicional)
  must_not_affect:
    - Tabla tenants (solo lectura/UPDATE, no DROP/ALTER)
    - Tabla users (solo UPDATE tenant_id, no schema change)
    - Auth flow existente
    - Plugin mail (domain depende de tenants.domain)
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - mail-plugin (usa tenants.domain)
  systemic_invariants:
    - Tenant domain debe existir antes de usar mail plugin
  composition_checks:
    - Crear tenant con domain → mail plugin puede listar cuentas
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility — tenant CRUD only
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - plugins/tenant/backend/service.py
```

## Traceability

- Requirement: Superadmin necesita gestionar tenants desde UI
- owner: -
- approver: -
- Commit: -
- Deployment: -

## Definition of Done

- [ ] Plugin carga sin errores
- [ ] Superadmin puede listar tenants
- [ ] Superadmin puede crear tenant con domain
- [ ] Superadmin puede editar domain de tenant
- [ ] Superadmin puede asignar usuario a tenant
- [ ] Superadmin puede quitar usuario de tenant
- [ ] No-superadmin no puede acceder (403)
- [ ] Sidebar muestra "Tenants" solo para superadmin
- [ ] Mail plugin funciona con domain del tenant creado
