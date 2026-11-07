# A.SPEC 0045 — Sincronización de permisos de plugins en runtime (migración de datos por arranque)

> `risk: normal` — kernel pequeño y aditivo: solo asegura filas de
> `permissions` y asignaciones a roles de plataforma en el boot que ya corre.
> Idempotente, nunca revoca. Sin migración de esquema: es migración de DATOS
> por runtime.

## WHY

El caso real: `hosting.profiles.manage` (A.SPEC 0042) se declaró en
`permissions/hosting.json`, pero ningún entorno lo recibió:

- `_sync_plugin_permissions` (catálogo `permissions` + roles) solo corre en
  `install_plugin`/`enable_plugin` — un plugin **ya registrado/enable** nunca
  re-sincroniza al añadir permisos a su manifest.
- `sync_plugin_registry` (que sí corre en boot vía `seed.py:285`) actualiza
  `plugin_registry.permissions_json` (metadatos) pero **no toca el catálogo**
  `permissions` ni asigna.
- `/api/v1/core/permissions` se alimenta del catálogo → el permiso nuevo no
  es asignable por UI/seed → el router del plugin lo exige y nadie lo tiene.
- El único remedio operativo hoy es SQL manual, como hicimos en local —
  indefendible para producción.

El remedio correcto es que **el runtime migre los datos al arrancar**: el
boot ya re-pobla `tenant_admin_permissions` (persistent.py, A.SPEC 0012) y
tiene el gancho ideal (`sync_plugin_registry`); falta extender esa misma
corrida al catálogo y a la asignación de plataforma.

## WHAT

1. **Manifest** (`kernel/plugins/manifest.py` + `contracts/plugins.py`):
   campo nuevo `platform_permissions: list[str] = []` en `PluginManifest`,
   cargado desde `plugin.json`. Contrato del plugin manifest extendido
   aditivamente (campo opcional; plugins viejos siguen parseando igual).
2. **`kernel/plugins/service.py::sync_plugin_registry`** — al final del loop:
   a. Para **cada permiso del manifest** (`manifest.permissions`):
      `ensure_permission(db, permission_name, ...)` (ya existe en
      `kernel/permissions/service.py`, es idempotente). Así el catálogo
      siempre refleja el manifest declarado al boot, sin install/enable.
   b. Para cada permiso en `manifest.platform_permissions`: asignarlo
      (idempotente, aditivo) a **cada rol que ya tenga `core.platform.admin`**
      (query por `role_permissions` y no por nombre de rol, para no acoplar
      seed). Solo INSERTs faltantes; **nunca revoca**.
   c. Audit `plugin.permissions.synced` (system actor) SOLO cuando algo haya
      cambiado, con `details={"plugin_id", "added": [...]}` — sin secreto, no
      spam por boot (si no hay añadidos, no audit line).
3. **Orden / dependencias**: `sync_plugin_registry` corre después del seed
   base como hoy (`seed.py:285`), así que `core.platform.admin` ya existe.
   Si un tenant administrador de plataforma no tiene el rol correcto, no
   pasa nada: no se crean roles nuevos, solo se extienden los existentes.
4. **Plugin hosting** (`plugins/hosting/plugin.json`):
   ```json
   "platform_permissions": ["hosting.sites.delete", "hosting.profiles.manage"]
   ```
   `hosting.sites.delete` entra también (hoy es prácticamente manual igual).
   El resto (read/create/manage) queda como `is_*` tenant-admin vía
   `tenant_admin_permissions` que ya se re-pobla en boot (0012) y NO cambia.
5. **No entra por diseño**: (additive-only; revocar permisos quitados del manifest — el sync es additive-only y el cleanup queda para una spec posterior con política explícita);
   tocar `users.is_superadmin` (prohibido); crear roles; tocar catálogo en
   shutdown; tocar el flujo install/enable (queda igual, no doble trabajo:
   `_sync_plugin_permissions` post-documentación cite notas are idempotent).

## SCOPE

Entra:
- `vendor/systutor-core/src/systutor/contracts/plugins.py` (campo opcional)
- `vendor/systutor-core/src/systutor/kernel/plugins/manifest.py` (parse)
- `vendor/systutor-core/src/systutor/kernel/plugins/service.py` (sync +
  audit, ≤ +80 líneas)
- `vendor/systutor-core/plugins/hosting/plugin.json` (platform_permissions)
- `vendor/systutor-core/tests/**` (tests nuevos; no se rompen las suites existentes)
- `vendor/systutor-core/README.md` (documentar platform_permissions)
- `aspecs/0045-runtime-plugin-permission-sync.md`

No entra:
- Revocación/cleanup de permisos huérfanos del catálogo.
- `seed.py` reestructura, seed de tenants demo, loops del uvicorn.
- Migración de esquema (no hay: catálogo/carpetas_operations re-usadas).
- `provisioner```filemanager```mail```domains```backend plugins dominios) —
  sin tocar.

## CONTRACT

Precondiciones:
- App arranca y `sync_plugin_registry` corre en seed (hoy ya lo hace).
- Un plugin manifest declaró permisos nuevos sin re-cycle install/enable.

Postcondiciones:
1. Al boot siguiente, el catálogo `permissions` contiene los permisos
   declarados en el manifest de cada plugin cargado (idempotente).
2. Los `platform_permissions` de cada plugin quedan asignados a los roles
   con `core.platform.admin`, sin crear filas duplicadas ni revocar.
3. Un plugin sin `platform_permissions` no genera asignaciones nuevas.
4. Un boot sin cambios → ninguna escritura en `permissions`/`role_permissions`
   ni líneaudit `plugin.permissions.synced`.
5. La corrección manual SQL previa (fix 0042 local) deja de ser necesaria en
   despliegues nuevos: el runtime ejecuta la misma migración de datos.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El sync por boot es aditivo y idempotente; nunca revoca.
    proof: import/nonevements de resync garantizan INSERT-only; doble boot no duplica filas.
  - id: I2
    statement: La asignación de platform va por roles con core.platform.admin, nunca por users.is_superadmin.
    proof: query por role_permissions; test con 2 roles y una revocada de no afectar no-platform.
  - id: I3
    statement: plugins viejos (manifest sin platform_permissions) arrancan igual que hoy.
    proof: test de manifest sin el campo default=[]; suite de plugins verdes.
  - id: I4
    statement: Sin cambios no hay audit ruido ni escrituras; con cambios, audit con details {"plugin_id", "added"}.
    proof:  / test_sync_added_audits.
  - id: I5
    statement: El estado instalado/enable del plugin no cambia por este sync (no re-instala, no corre hooks).
    proof: test de estado en registry tras boot con permisos nuevos: sigue enabled.
  - id: I6
    statement: Tenant admins NO reciben platform_permissions (el puente sigue siendo RBAC, no duplicación silenciosa).
    proof: test_tenant_admin_role_does_not_gain_profiles_manage.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin && PYTHONPATH=src:. \
  .venv/bin/python -m pytest vendor/systutor-core/tests -q
cd /data/data/com.termux/files/home/Proyectos/mailadmin && .venv/bin/python scripts/../vendor/systutor-core/../.venv 2>/dev/null; # no-op
# gates del repo:
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/hosting/backend/tests -q
```

Declarado explícito del flujo datos:
- `test_boot_syncs_new_permission_into_catalog`: plugin fake con permiso
  añadido al manifest → boot → fila en `permissions`.
- `test_platform_permission_granted_to_platform_admin_roles`.
- `test_noop_boot_writesNothing_and_no_audit`.
- `test_tenant_admin_role_does_not_gain_platform_permissions`.

## ROLLBACK

`git revert` — no hay schema; los permisos/roles sembrados quedan, sin efecto
(nadie los revoca) y el flujo manual del fix 0042 sigue disponible.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/src/systutor/contracts/plugins.py
    - vendor/systutor-core/src/systutor/kernel/plugins/manifest.py
    - vendor/systutor-core/src/systutor/kernel/plugins/service.py
    - vendor/systutor-core/plugins/hosting/plugin.json
    - vendor/systutor-core/tests/**
    - vendor/systutor-core/README.md
    - aspecs/0045-runtime-plugin-permission-sync.md
  allowed_addendum:
    - vendor/systutor-core/src/systutor/api/seed.py   # solo la llamada sync_plugin_registry en la rama existing de ensure_seed (GAP del generador: sin esto el boot de despliegues existentes nunca sincroniza y la migración por runtime no llega a producción)
  prohibited:
    - vendor/systutor-core/app/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/plugins/hosting/backend/**
    - vendor/systutor-core/plugins/{mail,domains,tenant,filemanager}/**
    - vendor/systutor-core/src/systutor/kernel/{auth,tenants,audit}/**
    - apps/**
    - scripts/**
    - docker-compose.yml
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0012
    - 0042
  must_compose_with:
    - kernel health (seed BASE_PERMISSIONS, sync_tenant_admin_role)
    - plugin runtime discovery (plugins/service.sync_plugin_registry en boot)
```

## Traceability

- Requirement: que un permiso nuevo declarado en el manifest de un plugin
  llegue a catálogo y al rol de plataforma **sin install/enable ni SQL
  manual**, migrando por runtime en cada boot (caso real 0042 producción).
- owner: luc444s
- Deployment: rebuild del app (kernel listo) — bootstrap lo ejecuta solo.

## Definition of Done

- [ ] Manifest parsea `platform_permissions` con default [] (I3).
- [ ] Boot con permiso nuevo: catálogo + asignación de plataforma + audit (I1, I2, I4).
- [ ] Boot sin cambios: cero escrituras ni audit (I4). El rol tenant_admin no recibe platforms (I6).
- [ ] hosting/plugin.json declara hostin.profiles.manage y host s.patch delete en platform_permissions.
- [ ] ensure_seed (deploy existente) llama sync_plugin_registry en cada boot (la migración por runtime llega a producción).
- [ ] Suite de tests del kernel verdes; hooks/backends de plugins intactos (I5).
