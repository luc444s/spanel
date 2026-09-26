# A.SPEC 0003 — Sync admin role permissions on every boot

> `risk: normal` — toca el camino de arranque y la tabla de permisos. No hay migración ni
> cambio de esquema, el rollback es un revert, pero un bug acá puede dejar al admin sin
> permisos en el siguiente deploy. Mode: `extreme-poverty`.

## WHY

El superadmin de producción tiene hoy todo el poder, pero **no por el mecanismo que la
arquitectura asume**. Tiene poder por dos razones simultáneas:

1. Su role tiene los 22 permisos de `BASE_PERMISSIONS` más los de los plugins, asignados
   por `seed.py:177-180`.
2. `is_superadmin` cortocircuita la autorización en cinco sitios.

La razón 2 **tapa** un agujero de la razón 1, y ese agujero está en el arranque:

```sh
# entrypoint.sh:33-43
existing = db.scalar(select(User).where(User.email == settings.seed_admin_email))
if not existing:
    result = seed_demo_data(db, settings, loaded)
else:
    print('Seed already exists')
```

`seed_demo_data` es idempotente y granta todo al admin role, **pero solo se invoca en el
primer arranque**. En todo despliegue posterior, un permiso nuevo agregado a
`BASE_PERMISSIONS` o declarado en el manifest de un plugin **nunca llega al role**.

Hoy nadie lo nota porque el flag lo compensa. El día que se elimine el bypass — que es
justo lo que hay que hacer para limpiar la deuda que motiva esta A.SPEC — el superadmin
pierde en silencio todo permiso agregado después del primer deploy. Sin error, sin 403
visible: un 403 sí, pero en un endpoint que nadie miraba.

El segundo problema es que ese `if` vive en bash. La rama que decide si el admin tiene sus
permisos no es testeable.

## WHAT

**Después de cualquier arranque, el admin role tiene todos los permisos de
`BASE_PERMISSIONS` ∪ permisos declarados por los plugins cargados — incluso si el usuario
admin ya existía desde un despliegue anterior.**

Es una verdad, falsable hoy, y hoy **falla**.

## SCOPE

1. `src/systutor/api/seed.py`: extraer `sync_permissions(db, tenant, plugins)` desde el
   bloque de permisos de `seed_demo_data`, y agregar `ensure_seed(db, settings, plugins)`
   que decide seed-first-boot vs sync-permisos. Mover la decisión desde bash a Python.
2. `entrypoint.sh`: reemplazar la rama `if not existing` por una llamada a `ensure_seed`.
3. `tests/test_permission_sync.py`: el test de dos arranques.
4. `docs/superadmin-debt.md`: la nota de deuda que hace controlado el bypass pendiente.

## OUT OF SCOPE

Explícitamente **no** se elimina el bypass. Queda documentado como deuda (ver
`docs/superadmin-debt.md`) y este cambio es lo que lo hace seguro eliminarla después.

- No se borra ninguno de los 5 sitios del cortocircuito.
- No se borra la columna `users.is_superadmin`. Borrarla exige migración, y en este repo
  `entrypoint.sh:8-17` usa `Base.metadata.create_all`, que **no ejecuta migraciones**.
- No se saca el claim del JWT (`security.py`, `schemas.py`, `legacy.py`,
  `dependencies.py:62-86`).
- No se toca el frontend ni sus 40 apariciones.
- No se agregan permisos nuevos a `BASE_PERMISSIONS` (eso es de la deuda, no de acá).
- No se arregla `npm run db`, que apunta a `scripts/systutor-db.sh` inexistente.

## CONTRACT

**Precondiciones**

- El admin user existe (creado en un arranque previo) y tiene un role `admin`.
- `sync_permissions` es idempotente: re-grantear un permiso ya asignado no escribe.

**Postcondiciones**

- Tras `ensure_seed` con usuario existente: el role `admin` posee el conjunto completo.
- Tras `ensure_seed` sin usuario: comportamiento idéntico al `seed_demo_data` actual.
- `ensure_seed` nunca lanza por un arranque con usuario ya existente.

**Verdad nueva, falsable**: agregar un nombre a `BASE_PERMISSIONS`, arrancar de nuevo, y el
role lo tiene. El test lo demuestra con dos llamadas a `ensure_seed`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      El comportamiento de autorización no cambia. users.is_superadmin sigue seteado en
      True (seed.py) y los 5 cortocircuitos siguen intactos. Nadie pierde ni gana acceso
      con este cambio.
    proof: grep de is_superadmin en seed.py y context.py sin cambios respecto de 0002

  - id: I2
    statement: >
      ensure_seed es idempotente. Llamarlo N veces produce el mismo estado que llamarlo una.
    proof: test_ensure_seed_is_idempotent, segunda llamada no agrega RolePermission

  - id: I3
    statement: >
      El primer arranque se comporta como antes: crea tenant, branch, role, admin user y
      permisos, y devuelve el mismo dict de resultado.
    proof: test_ensure_seed_creates_on_first_boot, asserts sobre las claves del dict

  - id: I4
    statement: >
      Los permisos de plugins también se sincronizan, no solo los del kernel.
    proof: test_plugin_permission_reaches_admin_role_on_second_boot

  - id: I5
    statement: >
      seed_demo_data conserva su firma y su comportamiento. No se rompe ningún llamador
      existente (tests/conftest.py, tests/test_plugin_runtime_completion.py).
    proof: suite completa sin cambios de conteo

  - id: I6
    statement: >
      La decisión first-boot-vs-sync sale de bash. entrypoint.sh no contiene lógica
      condicional de seed.
    proof: grep de "if not existing" en entrypoint.sh vacío

  - id: I7
    statement: >
      El bypass de superadmin sigue funcionando exactamente igual. No es una A.SPEC que
      lo surface, es una que lo hace reemplazable después.
    proof: test_superadmin_bypass_still_present, is_superadmin=True sin permisos pasa
```

## VERIFICATION

```bash
cd vendor/systutor-core
python -m pytest tests/test_permission_sync.py -q     # los 5 tests nuevos
python -m pytest tests -q                             # I5: 58 passed, sin cambio
ruff check .                                          # 0 errores
grep -n "if not existing" ../../entrypoint.sh          # I6: vacío
```

**Proof de falsabilidad**: el test `test_kernel_permission_reaches_admin_role_on_second_boot`
falla contra el código previo a esta A.SPEC, porque `entrypoint.sh` lo saltaría. Ese es el
antes y el después medido.

## ROLLBACK

`git revert <sha>`. Sin migración, sin cambio de esquema, sin estado en runtime.

La consecuencia de revertir es acotada y conocida: el admin role queda con los permisos que
tenía. El flag sigue granting todo, así que **no hay lockout**.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/src/systutor/api/seed.py
    - vendor/systutor-core/tests/test_permission_sync.py
    - entrypoint.sh
    - docs/superadmin-debt.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/tenants/context.py   # bypass B1, B2
    - vendor/systutor-core/src/systutor/kernel/auth/dependencies.py # bypass B3
    - vendor/systutor-core/src/systutor/api/v1/system.py           # bypass B4
    - vendor/systutor-core/plugins/tenant/backend/router.py         # bypass B5
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/src/systutor/kernel/auth/**
    - vendor/systutor-core/migrations/**
    - apps/web/**
    - docker-compose.yml
```

Los 4 sitios del bypass están **prohibidos explícitamente**: esta A.SPEC no los toca. Eso es
lo que las hace separables de la deuda.

## Blast Radius

```yaml
blast_radius:
  direct:
    - entrypoint.sh (arranque del contenedor)
    - seed.py (creación de permisos y role_permissions)
    - 1 archivo de tests nuevo
  indirect:
    - Todo despliegue: corre un SELECT por permiso (~22 + plugins). Sin escrituras si nada cambió
    - tests/conftest.py y test_plugin_runtime_completion.py usan seed_demo_data (I5)
  must_not_affect:
    - surface: superadmin_bypass
      surfaces: [context.py, dependencies.py, system.py, tenant/router.py]
      invariant: I1 + I7
    - surface: seed_demo_data_contract
      surfaces: [seed.py]
      invariant: I5
    - surface: schema
      surfaces: [todas las tablas]
      invariant: sin migración en change_surface
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with: []
  systemic_invariants:
    - El arranque de producción sigue creando el admin en el primer deploy. Este cambio
      solo agrega el sync en los siguientes.
    - La autorización no cambia: el bypass de 0002 sigue en pie.
  composition_checks:
    - python3 -m pytest tests -q
    - ruff check .
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - src/systutor/api/seed.py
```

`entrypoint.sh` se adelgaza: pierde lógica de decisión y queda como coordinación. Eso cumple
`entrypoints_must_stay_thin`.

## Traceability

- **Requirement**: el admin role debe tener siempre el conjunto completo de permisos, para
  que la eliminación del bypass sea segura
- **A.SPEC**: este documento
- **Code**: `seed.py` (`sync_permissions`, `ensure_seed`), `entrypoint.sh`
- **Migration**: ninguna
- **Test**: `tests/test_permission_sync.py`
- **Commit**: `TBD`
- **Deployment**: rebuild de imagen. Corre en el próximo arranque, sin paso manual

- **owner**: `TBD`
- **approver**: `TBD`

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
