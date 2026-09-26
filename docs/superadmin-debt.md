# Deuda controlada: el bypass de `is_superadmin`

Estado: **abierta, con mecanismo de seguridad instalado** (A.SPEC 0003, commit pendiente).
Última revisión: 2026-09-26.

Esto no es documentación. Es el contrato que hace *controlada* una deuda que decidimos no
pagar ahora, y el criterio exacto para revisitarla.

---

## 1. Qué es la deuda

`users.is_superadmin` es un flag booleano que cortocircuita la autorización. Está escrito
**cinco veces**, y tres de esas copias están en el mismo camino de request que decide permisos.

| # | Sitio | Forma | Nota |
|---|---|---|---|
| B1 | `src/systutor/kernel/tenants/context.py:22` | `return self.is_superadmin or ...` | El original. El que usa `has_permission` |
| B2 | `src/systutor/kernel/tenants/context.py:26` | `self.is_superadmin or ...` | **Redundante**: un usuario sin warehouses ya obtiene `None` = todos (`context.py:40`) |
| B3 | `src/systutor/kernel/auth/dependencies.py:174` | `is_superadmin or any(has_permission(...))` | **Doblemente redundante**: `has_permission` ya cortocircuita |
| B4 | `src/systutor/api/v1/system.py:145` | `is_superadmin or any(p in current_permissions ...)` | **El traicionero**: lee `current_permissions` directo, esquivando `has_permission` |
| B5 | `plugins/tenant/backend/router.py:37-49` | `def require_superadmin()` | Único guard definido fuera del kernel. Protege 7 endpoints |

### Por qué B4 es el peor

```python
can_read_full_runtime = tenant_context.is_superadmin or any(
    permission in tenant_context.current_permissions
    for permission in ("core.plugin.runtime.read", "core.plugin.manage")
)
```

No pasa por `has_permission`. Si alguien borra B1 y olvida B4, el superadmin pierde lectura del
runtime de plugins **sin ningún test que lo detecte**. Ese es el modo de falla que hace esta
deuda peligrosa: no es que falle hoy, es que falla *después*, cuando alguien limpie otra cosa.

### Inventario completo

- Backend producción: **37 ocurrencias** en 28 líneas, 14 archivos
- Tests: 14
- Frontend: **40 ocurrencias en 9 archivos**
- Documentación: 22

De esas 37, unas 10 son el bypass. **Las otras ~27 son plomería inerte** (columna, claim del
JWT, DTOs, `request.state`, store de Zustand, migración) que no participa de la decisión.

---

## 2. Por qué es segura dejada como está

**Las cinco copias leen la misma fuente**: la columna `users.is_superadmin` de la misma fila de
Postgres. El plugin `tenant` se salta `request.state`, pero ambos terminan en el mismo objeto
`User`. Mientras eso sea cierto, no puede haber divergencia.

**No hay exploit hoy.** `require_superadmin` compara contra el mismo valor que el token ya
validó en `dependencies.py:82`.

**La eliminación es más barata de lo que parece.** Para matar el bypass:

- borrar 4 prefijos `is_superadmin or ` (B1-B4)
- borrar una función (B5) y reemplazar 7 call sites por `require_permission("core.tenants.*")`
- agregar `core.tenants.read` y `core.tenants.manage` a `BASE_PERMISSIONS`

**~10 líneas y 2 permisos.**

---

## 3. El invariante que la hace pagable

> **El admin role tiene todos los permisos de `BASE_PERMISSIONS` ∪ permisos de plugins.**

Si esto es cierto, el bypass es **redundante por construcción**: el superadmin tiene todo el
poder por el camino normal de RBAC (`seed.py`, `sync_permissions`). El día que se borre B1, no
se pierde nada, y B4 sigue funcionando porque el permiso está en el role.

**Ese invariante está testeado** en `tests/test_permission_sync.py`
(`test_kernel_permission_reaches_admin_role_on_second_boot`). Antes de A.SPEC 0003 **no lo
estaba**: `entrypoint.sh` solo sembraba en el primer arranque, así que un permiso agregado
después jamás llegaba al role, y el flag lo tapaba. Ese era el agujero.

También está testeado que el bypass **sigue presente**
(`test_superadmin_bypass_still_present`): 0003 no lo tocó. Ese test es el que va a fallar
cuando la deuda se pague, y está bien que falle.

---

## 4. Cuándo revisarla

Disparadores concretos, no "en algún momento":

1. **Alguien endurece `dependencies.py:82`** (el check de token privilege). Es el día que
   `tenant` queda con un bypass que nadie revisa.
2. **Se pide superadmin parcial** — alguien que vea todos los buzones pero no pueda cambiar
   contraseñas. Hoy es imposible con un bool.
3. **Se agrega un plugin con permisos nuevos.** Hay que confirmar que el sync los otorga.
4. **Aparece un 403 inesperado en un endpoint de superadmin.** Síntoma de B4.

---

## 5. Decisiones de producto pendientes

Ninguna se resuelve acá. Son del dueño del sistema.

- **¿El superadmin conserva poder total por rol, explícitamente, o pierde el atajo?**
  - (A) Todos los permisos `core.*` asignados al role. Se conserva el atajo pero es auditable.
  - (B) Superadmin = nadie, y se le asignan permisos como a cualquiera. Desaparece el bypass.
  - (C) Bandera de emergencia: el flag queda solo para break-glass, con log y aviso en UI.

  Recomendación: **(C)**, porque preserva la capacidad de emergencia sin que el camino normal
  dependa del flag. Ojo: para (C) el flag debe **salir del JWT**
  (`security.py:66-74`, `schemas.py:20`, `legacy.py:87`, `api.ts:12`, `store.ts:37`), porque un
  token de 60 minutos con privilegios embebidos no es break-glass.

- **¿El claim `is_superadmin` se queda en el JWT?** Hoy obliga a que cualquier cliente que
  construya un token a mano acierte el tipo (`dependencies.py:63`).

- **Permisos como strings inline.** `require_permission("core.users.read")` aparece ~15 veces
  en `api/v1/core/`. Un typo es un **403 silencioso**: el endpoint queda cerrado y nadie lo
  nota. No hay constante ni test contra el catálogo.

---

## 6. Contexto de repositorio que afecta esto

- `entrypoint.sh:8-17` usa `Base.metadata.create_all`, **no ejecuta migraciones**. Por eso
  la columna `users.is_superadmin` **no se borra**: hacerlo exigiría una migración que el
  arranque ni correría. Dejarla es gratis e inerte.
- `npm run db` apunta a `scripts/systutor-db.sh`, que **no existe**.
- `pyproject.toml` `[tool.pyright] include = ["src", "tests", "app"]` **no incluye `plugins/`**.
  No hay type-checking sobre el plugin `tenant` ni sobre `mail`.
- `mailadmin/.venv` fue creado como copia del venv de `Systutor-oss` y arrastra dos
  distribuciones editables (`systutor_core` y `systutor_oss`). **Hay que correr los tests con
  `PYTHONPATH=src`** o se importa el `src/systutor` del proyecto equivocado y los tests del
  kernel no están probando este repo. Ver A.SPEC 0004.
