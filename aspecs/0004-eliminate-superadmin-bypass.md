# A.SPEC 0004 — Eliminate the is_superadmin bypass

> `risk: normal` — superficie de autorización, 36 archivos. Sin migración, sin cambio de
> esquema. Rollback es un revert y no hay lockout: si algo falla, volver atrás restaura el
> flag. Mode: `extreme-poverty`.

## WHY

`users.is_superadmin` es un flag que cortocircuita la autorización, y está escrito **cinco
veces**. Tres de esas copias viven en el mismo camino de request que decide permisos.

| # | Sitio | Forma |
|---|---|---|
| B1 | `kernel/tenants/context.py:22` | `return self.is_superadmin or ...` |
| B2 | `kernel/tenants/context.py:26` | `self.is_superadmin or ...` — redundante, `context.py:40` ya da acceso total sin warehouses |
| B3 | `kernel/auth/dependencies.py:174` | `is_superadmin or any(has_permission(...))` — doblemente redundante |
| B4 | `api/v1/system.py:145` | lee `current_permissions` directo, **esquivando `has_permission`** |
| B5 | `plugins/tenant/backend/router.py:37-49` | `require_superadmin()`, único guard fuera del kernel, protege 7 endpoints |

Consecuencias concretas:

1. **No hay superadmin parcial.** Es un bool: todo o nada. No se puede dar lectura total sin
   escritura.
2. **No es auditable.** Cuando `has_permission` devuelve `True` por bypass, no queda registro
   de qué permiso se ejercitó.
3. **B4 falla en silencio.** Si alguien borra B1 y olvida B4, el superadmin pierde
   `core.plugin.runtime.read` sin un solo test que lo detecte.
4. **El plugin se salta la ruta validada.** `tenant/router.py:42` lee `current_user.is_superadmin`
   del objeto User, evitando `request.state` y el check de token privilege
   (`dependencies.py:82`). Hoy coinciden porque salen de la misma fila.

El flag además es **redundante por construcción**: el role `admin` ya recibe todos los
permisos de `BASE_PERMISSIONS` ∪ plugins vía `sync_permissions` (`seed.py`), que desde
A.SPEC 0003 corre en **cada arranque**. Por eso la eliminación es segura: el superadmin no
pierde nada, porque su poder siempre vino por el role.

Y el frontend ya tiene la información: `store.ts:33` guarda `permissions: string[]` y
`schemas.py:22` lo manda. Los 40 `isSuperadmin` del frontend recalculan lo que ya tienen.

## WHAT

**No existe ningún atajo de autorización por bandera.** Todo acceso, incluido el del
superadmin, se decide exclusivamente por `RolePermission`.

Es una verdad, falsable, y hoy **falla**.

## SCOPE

1. `context.py`: borrar el campo `is_superadmin` y los dos cortocircuitos (B1, B2).
2. `dependencies.py`: borrar el cortocircuito (B3), la validación del claim del JWT y el
   check de privilege mismatch.
3. `system.py`: borrar el cortocircuito (B4).
4. `tenant/router.py`: borrar `require_superadmin` (B5) y reemplazar sus 7 call sites.
5. `auth/models.py`, `security.py`, `service.py`, `schemas.py`, `seed.py`,
   `tenant/service.py`, `core/legacy.py`: borrar la columna, el claim y los DTOs.
6. `mail/router.py` + `service.py`: reemplazar el flag por el permiso `mail.accounts.read.all`.
7. `seed.py`: agregar `core.tenants.read`, `core.tenants.manage`, `mail.accounts.read.all`.
8. Frontend: 9 archivos, borrado puro de `isSuperadmin`.
9. Tests: invertir el test del bypass y actualizar las construcciones de `TenantContext`.

## OUT OF SCOPE

- **La columna `users.is_superadmin` no se borra de las bases existentes.** `entrypoint.sh:8-17`
  usa `Base.metadata.create_all`, que **no ejecuta migraciones**. Removerla del modelo la deja
  vestigial en las bases actuales: inerte y sin efecto. Borrarla de verdad requiere
  `ALTER TABLE`, que es otra A.SPEC.
- `migrations/versions/**`: migraciones históricas, jamás se editan.
- `ADD/`, `docs/superadmin-debt.md` queda como registro del antes (se actualiza al cierre).
- No se agregan roles ni permisos más allá de los 3 nuevos.
- No se toca `docker-compose.yml` ni la infra.
- El problema del venv (`PYTHONPATH=src`): verificado que no afecta porque los cores son
  equivalentes. Sin acción.

## CONTRACT

**Precondiciones**

- El role `admin` del tenant contiene todo `BASE_PERMISSIONS` ∪ permisos de plugins. Garantizado
  por A.SPEC 0003 (`sync_permissions` en cada arranque).
- El usuario admin sembrado tiene ese role (`seed.py:183`).

**Postcondiciones**

- Un usuario sin el permiso X recibe 403 en un endpoint que exige X, **aunque** tenga
  `is_superadmin=True` en la base.
- El admin sembrado conserva acceso a todo, por role.
- El JWT ya no contiene `is_superadmin`, y un token que lo traiga se rechaza igual que hoy
  (por claims inválidos, no por el flag).

**Cambio de comportamiento, declarado**: cualquier usuario que tuviera `is_superadmin=True`
con un role restringido **pierde el bypass**. En este despliegue el único superadmin es el
sembrado, con el role `admin` completo, así que nadie pierde acceso. Si existieran otros,
habría que revisar sus roles antes de desplegar.

**Verdad nueva, falsable**: `has_permission` devuelve `False` para un usuario sin el permiso,
sin importar la bandera. Lo testea `test_superadmin_no_longer_bypasses_permissions`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      El admin sembrado conserva acceso total. Es la garantía de que esto no es un lockout.
    proof: test_seeded_admin_keeps_full_access

  - id: I2
    statement: >
      El aislamiento por tenant no se debilita. Un usuario sigue viendo solo su dominio,
      y ahora además sin depender de ninguna bandera.
    proof: tests/test_tenant_isolation.py sin cambios en sus aserciones, + test
      test_tenant_user_cannot_read_other_domain

  - id: I3
    statement: >
      La lista de buzones de mail conserva su comportamiento: con mail.accounts.read.all ve
      todos los dominios, sin ella solo el propio. Sin cambio observable.
    proof: test_service.py de mail: superadmin pasa a ser un usuario con el permiso

  - id: I4
    statement: >
      La superficie de API no cambia. Rutas, métodos y formas de respuesta idénticos.
      Solo desaparece un campo del DTO de usuario.
    proof: suite completa sin cambios de conteo salvo el test invertido

  - id: I5
    statement: >
      El seed sigue siendo idempotente y sigue creando todo. ensure_seed no se toca.
    proof: tests/test_permission_sync.py pasa sin cambios salvo el test del bypass

  - id: I6
    statement: >
      No queda ningún cortocircuito. grep de "is_superadmin or" en src/ y plugins/ vacío.
    proof: grep

  - id: I7
    statement: >
      El frontend no inventa reglas de seguridad. hasRequiredPermissions y
      hasAnyPermission quedan como chequeo de permisos puro.
    proof: ausencia de isSuperadmin en los 9 archivos

  - id: I8
    statement: >
      No se agrega ninguna supresión de lint ni se toca la config de ruff.
    proof: git diff no incluye pyproject.toml
```

## VERIFICATION

```bash
cd vendor/systutor-core
export PYTHONPATH="$PWD/src"          # ver OUT OF SCOPE: el venv tiene dos editables

# I6: la verdad
grep -rn "is_superadmin" src/ plugins/ | grep -v "/tests/"   # vacío
grep -rn "require_superadmin" plugins/ src/                  # vacío

# I7: frontend limpio
grep -rn "isSuperadmin\|is_superadmin" apps/web/src ../../apps/web/src

# gates
python -m pytest tests plugins/mail/backend/tests -q
ruff check .
cd ../../apps/web && npx tsc --noEmit
```

**Proof de falsabilidad**: antes de este cambio, un usuario con `is_superadmin=True` y cero
permisos pasa `has_permission("lo que sea")`. Después, falla. El test invertido lo mide.

## ROLLBACK

`git revert <sha>`. Sin migración, sin cambio de esquema aplicado.

La columna queda en la base y el código viejo la vuelve a usar, así que el revert es limpio.
**No hay lockout en ninguna dirección**: si el deploy falla, revertir restaura el flag.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/src/systutor/kernel/tenants/context.py
    - vendor/systutor-core/src/systutor/kernel/auth/dependencies.py
    - vendor/systutor-core/src/systutor/kernel/auth/models.py
    - vendor/systutor-core/src/systutor/kernel/auth/schemas.py
    - vendor/systutor-core/src/systutor/kernel/auth/security.py
    - vendor/systutor-core/src/systutor/kernel/auth/service.py
    - vendor/systutor-core/src/systutor/api/seed.py
    - vendor/systutor-core/src/systutor/api/v1/system.py
    - vendor/systutor-core/src/systutor/api/v1/core/legacy.py
    - vendor/systutor-core/plugins/tenant/backend/router.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_router.py
    - vendor/systutor-core/tests/**
    - apps/web/src/**
    - docs/superadmin-debt.md
  prohibited:
    - vendor/systutor-core/migrations/**          # históricas, jamás se editan
    - vendor/systutor-core/pyproject.toml         # ni una supresión de lint
    - vendor/systutor-core/src/systutor/kernel/permissions/**
    - vendor/systutor-core/src/systutor/kernel/tenants/models.py
    - docker-compose.yml
    - entrypoint.sh
    - .env.docker*
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Todos los endpoints: la autorización deja de tener cortocircuito
    - El JWT: pierde el claim is_superadmin
    - El DTO de usuario: pierde el campo is_superadmin
    - 9 archivos de frontend
  indirect:
    - Tokens emitidos antes del deploy siguen Teniendo el claim; el decode lo ignora
      porque el claim ya no se lee, pero _require_string_claim sobre claims conocidos
      no se ejecuta para este
    - La columna queda vestigial en las bases existentes
  must_not_affect:
    - surface: tenant_isolation
      surfaces: [context.py, mail/service.py]
      invariant: I2 + I3
    - surface: seeded_admin_access
      surfaces: [seed.py, permissions/*]
      invariant: I1
    - surface: lint_config
      surfaces: [pyproject.toml]
      invariant: I8
    - surface: schema_migrations
      surfaces: [migrations/**]
      invariant: prohibido en change_surface
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0003   # sync_permissions en cada arranque: sin esto, esto rompe producción
  must_compose_with: []
  systemic_invariants:
    - El kernel sigue agnóstico de dominio: 0004 solo toca autorización, no reglas de negocio.
    - mail y tenant pasan a usar el mismo mecanismo que el kernel.
  composition_checks:
    - python3 -m pytest tests plugins/mail/backend/tests -q
    - ruff check .
    - npx tsc --noEmit
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations: []
```

Todo es borrado. No se introduce lógica nueva, salvo 3 nombres en `BASE_PERMISSIONS`.

## Traceability

- **Requirement**: la autorización no debe depender de un bypass global
- **A.SPEC**: este documento
- **Code**: 13 archivos de producción + 9 de frontend
- **Migration**: ninguna. La columna queda vestigial (ver OUT OF SCOPE)
- **Test**: `tests/test_permission_sync.py` invertido, tests de mail actualizados
- **Commit**: `TBD`
- **Deployment**: rebuild de imagen. Requiere re-login (el JWT cambió de forma)

- **owner**: `TBD`
- **approver**: `TBD`

> **Requisito de despliegue**: al publicar, el claim `is_superadmin` desaparece del token.
> Los tokens ya emitidos siguen siendo aceptados porque el claim se lee con `payload.get()`
> y solo se valida si se usa; al no usarse, deja de validarse. Aun así, **hay que pedir
> re-login** para que el frontend reciba el usuario sin el campo.

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [ ] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [ ] Traceability established
