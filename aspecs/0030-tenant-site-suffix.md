# A.SPEC 0030 — Tenant site suffix and domain namespace

> `risk: high` — toca tenancy y decide qué dominios puede ver y usar un tenant.
> Un filtro incorrecto puede ocultar dominios legítimos o exponer dominios de
> otro namespace. Exige `approver:` humano.
>
> Esta A.SPEC entrega la **Fase 1** de `docs/proposals/0030-crear-sitio-tipos-y-dominios.md`.
> La creación de sitios `static` y la UI “Crear sitio” quedan para A.SPECs
> posteriores; esta verdad es independiente y falsable por sí sola.

## WHY

Hoy no existe una regla de namespace para los dominios de sitios:

- `hosting_sites.domain` dice qué sitio usa un dominio, pero no a qué tenant
  pertenece el FQDN.
- `GET /api/v1/plugins/domains` filtra por dueño del sitio, no por sufijo. Por
  eso el tenant `demo` (dominio de correo `spanel.site`) ve `lucas.spanel`,
  un extra de `spanel-lucas-wp`, aunque esté fuera de su namespace.
- No hay forma de configurar qué dominios puede usar un tenant para sitios.
- No hay un chequeo de disponibilidad reutilizable por Hosting y por la UI.

`tenant.domain` no sirve para esto: lo usa `mail` para las cuentas. Cambiarlo
para ampliar el namespace de sitios rompería el correo. Hace falta un valor
separado.

## WHAT

Agregar un **sufijo de sitios por tenant** (`tenant_site_settings.site_suffix`)
y hacer que el namespace se aplique de forma observable:

1. El sufijo se guarda en una tabla propia del plugin `tenant`, separada de
   `tenants.domain`.
2. `GET /api/v1/plugins/tenant/tenants` devuelve `site_suffix`.
3. `PUT /api/v1/plugins/tenant/tenants/{tenant_id}` puede actualizar `domain`,
   `site_suffix` o ambos sin borrar el otro.
4. Dos tenants no pueden tener sufijos iguales ni solapados
   (`spanel` vs `spanel.site`) → `409 tenant_site_suffix_conflict`.
5. `GET /api/v1/plugins/domains/check?domain=...` responde si un dominio es
   usable para el tenant actual, sin reservar nada. Un dominio mal formado es
   `422`, no una respuesta `available=false`.
6. `GET /api/v1/plugins/domains` oculta los dominios fuera del sufijo. El
   administrador de plataforma los sigue viendo, marcados
   `outside_tenant=true`.

La verdad nueva: **un tenant solo ve y valida dominios dentro de su
`site_suffix`; el sufijo es configuración de tenant y no toca el correo.**

## SCOPE

- Contrato de namespace en `systutor.contracts`: helpers puros
  (`normalize_site_suffix`, `domain_in_suffix`) y un provider registrado por el
  plugin `tenant` para que `domains` lea el sufijo sin importar su servicio.
- Modelo, migración, servicio, schemas y router del plugin `tenant`.
- Edición de `site_suffix` en la página de tenants.
- Endpoint `check` y filtro de visibilidad en el plugin `domains`.
- Campo `outside_tenant` en la respuesta y su indicador en la UI de dominios.
- Pruebas unitarias, de servicio y de migración.

## OUT OF SCOPE

- Crear sitios `static`, cambiar `hosting_sites`, `kind`, `admin_email` o la
  provisioning de Hosting.
- La UI “Crear sitio” con tipo y dominio.
- Borrar, mover o reasignar dominios existentes.
- Migrar automáticamente `lucas.spanel` a otro tenant.
- DNS, Cloudflare, certificados o Traefik.
- Cambiar el funcionamiento de `tenant.domain` en `mail`.
- Inventario de dominios disponibles.

## CONTRACT

Precondiciones:

- El plugin `tenant` está instalado y puede correr una migración nueva.
- Existen `tenants` y, si hay filas, `hosting_sites` y el file provider de
  `domains` en el estado actual.
- `site_suffix` es nullable: la ausencia de sufijo es un estado válido.

Postcondiciones:

1. Existe `tenant_site_settings` con PK `tenant_id`, `site_suffix` nullable y
   único, y timestamps.
2. `systutor.contracts.site_namespace` expone los helpers puros y un registry
   (`register_site_suffix_provider` / `get_site_suffix`). El plugin `tenant`
   registra el provider; `domains` lo consume sin importar el servicio de
   `tenant`.
2. La migración hace backfill: para cada tenant con `domain`, el sufijo inicial
   es el dominio normalizado en minúsculas. Un tenant sin `domain` queda con
   `site_suffix = NULL`.
3. Crear un tenant después de la migración inserta su fila de settings:
   `site_suffix = normalize(domain)` si hay `domain`, o `NULL` si no lo hay.
   No se permite un tenant sin fila de settings.
4. `TenantResponse` incluye `site_suffix`; `domain` no cambia de significado.
5. Semántica de `PUT /tenants/{tenant_id}` (campos opcionales, `model_fields_set`):
   - `{"domain": "x"}` actualiza solo el dominio y conserva el sufijo;
   - `{"domain": null}` limpia el dominio de correo y conserva el sufijo;
   - `{"site_suffix": "x"}` actualiza solo el sufijo y conserva el dominio;
   - `{"site_suffix": null}` o `{"site_suffix": ""}` limpia el sufijo;
   - `{"domain": "x", "site_suffix": "y"}` actualiza ambos;
   - `{}` es `422` (no hay nada que actualizar).
6. `site_suffix` se normaliza a minúsculas, sin punto final, sin esquema,
   puerto, ruta, espacios ni comodines. Un string vacío se guarda como `NULL`.
   Se permiten sufijos de una sola etiqueta (`spanel`) y de varias
   (`spanel.site`).
7. El solapamiento de sufijos se comprueba en el servicio **antes** de escribir:
   un sufijo igual o solapado (`spanel` vs `spanel.site`) con el de otro tenant
   devuelve `409 tenant_site_suffix_conflict`; el mensaje nombra al tenant que
   lo tiene. El índice único de la base solo cubre igualdad; el solapamiento
   jerárquico no depende de `IntegrityError`. Un `IntegrityError` de igualdad
   se traduce igual a `409`, nunca a `500`.
8. `GET /domains/check?domain=paginadelucas.spanel` usa el tenant de
   `get_current_tenant_context()` y el permiso `domains.domains.read`. No acepta
   `tenant_id` en el request. Para el administrador de plataforma también se
   usa su `current_tenant_id`. Con `site_suffix=spanel` devuelve
   `available=true`, `reason=null`, `suffix="spanel"`.
9. El check devuelve `available=false` con un `reason` legible cuando:
   - el tenant no tiene sufijo;
   - el dominio no pertenece al sufijo;
   - el dominio es primario de un sitio;
   - el dominio ya está reclamado por un YAML del proxy.
   Un dominio con formato inválido es `AppError 422`, no `available=false`.
10. `DomainsService.list_domains(tenant_id, can_manage, include_outside)` filtra
    por el sufijo del tenant. El router pasa
    `include_outside=tenant_context.is_platform`.
    - Sin `core.platform.admin`: no se devuelve ningún dominio fuera del
      sufijo, sea primario o extra.
    - Con `core.platform.admin`: se devuelve con `outside_tenant=true`.
    - Los dominios dentro del sufijo se devuelven con
      `outside_tenant=false`.
11. Ningún chequeo escribe, reserva o modifica rutas, sitios o tenants:
    no llama a `add`, `remove`, `db.add`, `db.flush` ni `db.commit`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: tenant.domain sigue siendo el dominio de correo y no se reescribe al cambiar site_suffix.
    proof: test de actualizar solo site_suffix y comparar tenant.domain antes/después; mail tests verdes.
  - id: I2
    statement: La migración up/down/up no toca tenants, hosting_sites ni YAML del proxy.
    proof: test de migración en SQLite que inspecciona tablas y columnas; downgrade solo elimina tenant_site_settings.
  - id: I3
    statement: Un tenant sin core.platform.admin nunca recibe en GET /domains un FQDN fuera de su site_suffix cuando el sufijo está configurado, sea primario o extra.
    proof: test de servicio con un primario y un extra dentro/fuera del sufijo; el fuera se omite sin el permiso.
  - id: I4
    statement: El administrador de plataforma conserva visibilidad de los dominios fuera del sufijo, marcados outside_tenant=true; los de dentro llevan outside_tenant=false.
    proof: test de servicio con include_outside=True y asserts de ambos flags; test de router que verifica include_outside=is_platform.
  - id: I5
    statement: GET /domains/check no escribe ni reserva; dos llamadas seguidas devuelven lo mismo.
    proof: test con FakeOps y FakeDb que registran add/remove/add/flush/commit; asserts de cero escrituras y de filas de tenants/hosting_sites sin cambios.
  - id: I6
    statement: Los conflictos de dominio del tenant y de sufijo siguen devolviendo 409, no 500; la igualdad por IntegrityError también se traduce.
    proof: test_domain_conflict existente + tests nuevos de sufijo; se captura IntegrityError en router solo para igualdad.
  - id: I7
    statement: Un sufijo no puede solaparse con otro tenant.
    proof: test de igualdad y de spanel/spanel.site en ambos órdenes; AppError 409 tenant_site_suffix_conflict.
  - id: I8
    statement: La respuesta y los logs no incluyen secretos ni datos de otros tenants más allá del nombre/slug del dueño del sufijo.
    proof: revisión de mensajes de error y ausencia de credenciales; tests de mensaje de conflicto.
  - id: I9
    statement: Los permisos y el aislamiento no cambian; check no acepta tenant_id y PUT sigue exigiendo tenant.tenants.manage.
    proof: test de router con dependency overrides para permitido/denegado; check resuelve el tenant desde get_current_tenant_context y no lee un tenant_id del query/body.
  - id: I10
    statement: La migración nueva es aditiva y no altera las cadenas de kernel, hosting ni mail.
    proof: git diff --name-only (solo el archivo nuevo bajo tenant/migrations) + pytest de hosting, mail y kernel con sus revisiones intactas.
```

## VERIFICATION

### V0 — Gates obligatorios

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/tenant/backend/tests \
  vendor/systutor-core/plugins/domains/backend/tests -q

PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/hosting/backend/tests \
  vendor/systutor-core/plugins/mail/backend/tests -q

cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

### V1 — Contrato puro

- `normalize_site_suffix(" Spanel.SITE. ")` → `spanel.site`.
- Vacío y `None` → `None`; espacios, `/`, `:`, `*` → error de validación.
- `domain_in_suffix("paginadelucas.spanel", "spanel")` → `True`.
- `domain_in_suffix("paginadelucas.spanel.site", "spanel")` → `False`.
- `domain_in_suffix("spanel", "spanel")` → `True`.
- `default_suffix_from_domain("spanel.site")` → `spanel.site`.

### V2 — Tenant settings

- Crear un tenant con `domain="demo.com"` deja `site_suffix="demo.com"`.
- Actualizar solo `site_suffix="spanel"` conserva `domain`.
- Actualizar solo `domain="otro.com"` conserva `site_suffix`.
- Limpiar `site_suffix` con `""` lo deja en `NULL`.
- Dos tenants con `spanel` y `spanel` → `409`.
- Dos tenants con `spanel` y `spanel.site` → `409` en ambos órdenes.
- `PUT {}` → `422`.
- La respuesta del router incluye `site_suffix`.

### V3 — Dominios

- Con `site_suffix="spanel"`, el check de `paginadelucas.spanel` es
  `available=true`.
- `otro.com` → `available=false`, `reason` menciona el sufijo.
- Un primario de Hosting → `available=false`, `reason` nombra el contenedor.
- Un extra del proxy → `available=false`, `reason` nombra el contenedor.
- Tenant sin sufijo → `available=false` con motivo explícito.
- Dominio inválido → `AppError 422`.
- `list_domains` oculta un extra fuera del sufijo (`lucas.spanel` con
  `spanel.site`) y lo incluye con `spanel`.
- `list_domains` también oculta un **primario** fuera del sufijo; con
  `include_outside=True` lo incluye con `outside_tenant=true`.
- Los dominios dentro del sufijo llevan `outside_tenant=false`.
- Test de router con dependency overrides: sin `core.platform.admin` el
  servicio recibe `include_outside=False`; con el permiso recibe `True`.
- Test de router: `GET /domains/check` exige `domains.domains.read`; un query
  `tenant_id` no cambia el tenant resuelto.
- El check no llama a `add` ni a `remove`, no usa `db.add/flush/commit`, y las
  filas de `tenants`/`hosting_sites` quedan idénticas.

### V4 — Migración

- `test_tenant_settings_migration.py` ejecuta upgrade → verifica tabla y
  columnas → downgrade → verifica ausencia → upgrade otra vez.
- El backfill inserta una fila por tenant con dominio y deja NULL a los demás.
- Crear un tenant después de la migración inserta su fila de settings con el
  sufijo derivado (o NULL) y `TenantResponse` lo devuelve.

### V5 — Composición

- Los tests de `hosting` y `mail` siguen verdes: no cambia `tenant.domain` ni
  `hosting_sites`.
- El test existente `test_domain_conflict.py` sigue verde.
- `git diff --name-only` no muestra cambios en migraciones de kernel, hosting
  ni mail; solo el archivo nuevo de `tenant/migrations/`.

## ROLLBACK

- La migración `001_tenant_site_settings.py` define `downgrade()` y se prueba
  ejecutándolo en V4. El downgrade elimina **solo** `tenant_site_settings`;
  no toca `tenants`, `hosting_sites` ni YAML.
- Revertir el commit vuelve a la API y UI anteriores. Los dominios y sitios
  siguen intactos.
- Si se había configurado un `site_suffix` distinto del derivado, el downgrade
  lo pierde. Antes de un rollback real: exportar
  `tenant_id, site_suffix` y volver a cargarlo después. Es configuración
  reingresable, no datos de negocio.
- No hay cambio de rutas, certificados ni contenedores: no hace falta
  compensación de infraestructura.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/src/systutor/contracts/site_namespace.py   # nuevo
    - vendor/systutor-core/plugins/tenant/backend/models.py           # nuevo
    - vendor/systutor-core/plugins/tenant/migrations/001_tenant_site_settings.py  # nuevo
    - vendor/systutor-core/plugins/tenant/backend/register.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/tenant/backend/schemas.py
    - vendor/systutor-core/plugins/tenant/backend/router.py
    - vendor/systutor-core/plugins/tenant/backend/tests/**
    - vendor/systutor-core/plugins/tenant/frontend/api.ts
    - vendor/systutor-core/plugins/tenant/frontend/pages/TenantsPage.tsx
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/tests/**
    - vendor/systutor-core/plugins/domains/frontend/api.ts
    - vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx
    - docs/proposals/0030-crear-sitio-tipos-y-dominios.md
    - aspecs/0030-tenant-site-suffix.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/plugins/domains/backend/proxy/**
    - docker-compose.yml
    - scripts/**
    - /opt/stacks/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - configuracion de tenant (site_suffix)
    - respuesta de GET /domains y GET /domains/check
    - visibilidad de dominios para tenants y platform admin
  indirect:
    - UI de tenants
    - UI de dominios
    - futura validacion de Hosting (consumira el contrato)
  must_not_affect:
    - tenant.domain y cuentas de correo
    - hosting_sites y sus contenedores
    - YAML del proxy, rutas y certificados
    - autenticacion, permisos y aislamiento de otros tenants
    - migraciones existentes de kernel, hosting y mail
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013
    - 0027
  must_compose_with:
    - plugin mail (tenant.domain)
    - plugin hosting (dominios primarios)
    - plugin domains (extras del proxy)
  systemic_invariants:
    - un dueno por Host
    - aislamiento por tenant
    - deshabilitar un plugin no cambia el ruteo
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest plugins/tenant/backend/tests plugins/domains/backend/tests plugins/hosting/backend/tests plugins/mail/backend/tests
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la regla de namespace es pura y vive en contracts; tenant registra un provider y domains solo consume el contrato
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/src/systutor/contracts/site_namespace.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/service.py
```

## Traceability

- Requirement: un tenant solo puede ver y validar dominios dentro de su
  `site_suffix`; el sufijo es configuración separada del dominio de correo.
- owner: luc444s
- approver: luc444s
- Commit: pendiente (al ejecutar)
- Deployment: local primero; el laboratorio configura `site_suffix="spanel"`
  para demo después del deploy.

## Definition of Done

- [ ] Objetivo: el sufijo se configura y el namespace se aplica a lista y check.
- [ ] Alcance respetado: sin cambios en Hosting, mail, proxy ni contenedores.
- [ ] Contrato: tabla, API, check y visibilidad implementados.
- [ ] Invariantes I1–I8 probados.
- [ ] V0–V5 ejecutados.
- [ ] Rollback de migración ejecutado (up/down/up).
- [ ] Sin archivos fuera de `change_surface`.
- [ ] Nada >600 líneas; ideal <400.
- [ ] Trazabilidad completa.
