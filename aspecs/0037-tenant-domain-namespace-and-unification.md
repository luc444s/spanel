# A.SPEC 0037 — Tenant-owned domain namespace, multi-domain administration and account unification

> `risk: high` — toca tenancy, visibilidad de dominios, creación de sitios, correo
> y una migración de datos. Un error puede exponer dominios de otro tenant,
> reclamar un dominio ajeno o dejar cuentas de correo inaccesibles. Exige
> `approver:` humano.
>
> Esta A.SPEC sustituye la inferencia por `site_suffix` de la A.SPEC 0030 como
> regla de propiedad. El sufijo sigue existiendo como valor por defecto, pero
> **deja de decidir qué dominios puede usar un tenant**.
>
> Meta operativa concreta: un único tenant (`demo`, dominio de correo
> `spanel.site`, sufijo `spanel`) administra `spanel.site`, `debug.com` y
> `test2.com`; los tenants antiguos se unifican en él y se desactivan. La
> ejecución real sobre una base concreta es un paso de despliegue, no parte de
> los gates.

## WHY

Hoy la propiedad de un dominio se deduce del `tenant_site_settings.site_suffix`:

- `GET /domains/check` rechaza cualquier FQDN que no cuelgue del sufijo, así que
  `debug.com` y `test2.com` no se pueden añadir a `demo` aunque el cliente sea
  el mismo.
- `GET /domains` oculta esos dominios y `DomainsService.add` no tiene una noción
  explícita de dueño.
- `HostingService.create_site` exige sufijo y rechaza dominios fuera de él.
- El correo usa `tenants.domain` como **único** dominio de buzones: un tenant con
  seis dominios no puede administrar el correo de los otros cinco.
- No existe forma de declarar desde el módulo de tenants "esta cuenta administra
  estos dominios", ni de consolidar varias cuentas viejas en una.

La consecuencia es que un cliente con varios dominios necesita varias cuentas, y
los dominios quedan repartidos entre tenants que ya no representan al cliente.

## WHAT

La propiedad de un dominio pasa a ser **explícita y globalmente única**, y se
administra desde el módulo de tenants:

1. Nueva tabla `tenant_domains(domain PK, tenant_id, created_at, updated_at)`:
   un dominio pertenece a exactamente un tenant.
2. El módulo de tenants expone listar/añadir/quitar dominios administrados.
3. `domains` resuelve visibilidad y disponibilidad contra esa tabla (+ dominios
   primarios de sitios + extras del proxy de los contenedores del tenant), no
   contra el sufijo.
4. `hosting` acepta crear un sitio con cualquier dominio **libre o ya propiedad
   del tenant**; si está libre, lo reclama en la misma transacción.
5. `mail` administra buzones en **todos** los dominios propiedad del tenant, no
   solo en `tenants.domain`.
6. Un script idempotente (`scripts/unify_tenants.py`) consolida varios tenants en
   uno: reclama dominios, mueve sitios y usuarios, desactiva los orígenes y deja
   un plan reversible. Solo borra filas de tenant con `--purge` y dependencias
   cero.

La verdad nueva: **un tenant administra un conjunto explícito de dominios,
independiente de su sufijo, y ese conjunto gobierna dominios, hosting y correo.**

## SCOPE

- Contrato puro y registry en `systutor.contracts.domain_namespace`
  (`normalize_domain_name`, providers, `list_tenant_domains`, `domain_owner`,
  `domain_in_namespace`).
- Modelo `TenantDomain`, migración aditiva `002_tenant_domains` con backfill y
  `downgrade`, y provider registrado por el plugin `tenant`.
- Servicio, schemas y router de `tenant`: CRUD de dominios administrados.
- UI de tenants: sección "Dominios administrados" en el diálogo de edición, con
  alta y baja.
- `domains`: `check`, `list_domains` y `add` usan la propiedad explícita; se
  elimina la exigencia de sufijo. Se retiran `outside_tenant` e
  `include_outside`: un dominio publicado por un contenedor del tenant siempre
  pertenece a su vista, y el aislamiento se resuelve por contenedor, no por un
  flag global.
- `hosting.create_site`: reemplaza la validación de sufijo por propiedad;
  reclama el dominio libre en la misma transacción.
- `mail`: lista, crea y cambia contraseñas usando el conjunto de dominios
  propiedad del tenant; `tenant.domain` sigue siendo el dominio por defecto.
- UI de correos: selector de dominio cuando el tenant tiene más de uno.
- `scripts/unify_tenants.py` + tests: consolidación con `--apply`, plan JSON,
  `--rollback` y `--purge` seguro.
- `scripts/sync_tenant_domains.py` + tests: limpieza idempotente de filas de
  `tenant_domains` que duplican un dominio primario, y reporte de conflictos.
- Tests unitarios, de servicio, de router y de migración.

## OUT OF SCOPE

- El "actuar como tenant" del administrador de plataforma (Fase B).
- La vista global de plataforma con escritura.
- Borrar o renombrar contenedores, reescribir YAML del proxy, tocar Traefik,
  DNS o certificados.
- Transferir dominios entre tenants por API (el script de unificación es la
  única vía y es de operador).
- Fusionar usuarios con el mismo email (se desactiva el duplicado y se reporta).
- Cambiar el modelo `users`, `is_superadmin` o el kernel de permisos.
- Descubrimiento de contenedores no registrados.
- Migrar contenido de WordPress entre sitios.

## CONTRACT

Precondiciones:

- Existen `tenants`, `tenant_site_settings` y `hosting_sites`.
- Las migraciones del kernel y de los plugins `tenant`, `hosting`, `domains` y
  `mail` están aplicadas en su revisión actual.
- `core.platform.admin` es el único permiso que concede `is_platform`.

Postcondiciones:

### Modelo y contrato

1. Existe `tenant_domains` con `domain VARCHAR(255) PRIMARY KEY`,
   `tenant_id` FK a `tenants.id` no nulo e indexado, y timestamps. La PK hace
   imposible que dos tenants posean el mismo dominio.
2. `systutor.contracts.domain_namespace` expone:
   - `normalize_domain_name(value) -> str`: minúsculas, sin punto final, sin
     esquema/puerto/ruta/espacios/comodines; `ValueError` si es inválido. Acepta
     ápices (`debug.com`) y subdominios (`mail.spanel.site`).
   - `register_domain_providers(app, name, *, list_domains, owner)` con dos
     callables: `(db, tenant_id) -> Sequence[str]` y `(db, domain) -> str | None`.
   - `list_tenant_domains(app, db, tenant_id) -> set[str]`
   - `domain_owner(app, db, domain) -> str | None`
   - `domain_in_namespace(app, db, tenant_id, domain) -> bool`
   Sin providers registrados, `list_tenant_domains` devuelve `set()` y
   `domain_owner` devuelve `None`.
3. El plugin `tenant` registra el provider en `register()`. `domains`, `hosting`
   y `mail` consumen el contrato; ninguno importa modelos del otro plugin.

### Migración y backfill

4. `002_tenant_domains` es aditiva e idempotente:
   - crea la tabla solo si no existe;
   - inserta `tenants.domain` normalizado para cada tenant con dominio;
   - inserta `hosting_sites.domain` normalizado con el `tenant_id` del sitio;
   - ante un dominio duplicado entre dos sitios, conserva la primera fila (orden
     por `container_name`) y omite el resto sin fallar;
   - un dominio histórico inválido se omite sin romper la migración.
5. `downgrade()` elimina solo `tenant_domains`; no toca `tenants`,
   `hosting_sites` ni ninguna otra tabla. `up/down/up` es estable.

### Módulo de tenants

6. `GET /api/v1/plugins/tenant/tenants/{id}/domains` devuelve
   `{"domains": [{"domain": "..."}]}` ordenado, con `tenant.tenants.read` y
   `platform_only=True`.
7. `POST .../domains` con `{"domain": "..."}` normaliza y:
   - si el dominio ya es del mismo tenant → `201` idempotente con la fila;
   - si pertenece a otro tenant → `409 tenant_domain_conflict`;
   - si es el dominio de correo (`tenants.domain`) de otro tenant →
     `409 tenant_domain_conflict` con un mensaje que lo nombra y pide cambiarlo
     o unificar la cuenta;
   - si es primario de un sitio de otro tenant → `409 tenant_domain_conflict`;
   - si está libre → lo inserta y devuelve `201`.
8. `DELETE .../domains/{domain}`:
   - si el dominio es el dominio de correo del tenant → `409
     tenant_domain_in_use`: se cambia en "Dominio y sufijo", no se quita;
   - si el dominio es primario de un sitio del tenant o extra de uno de sus
     contenedores → `409 tenant_domain_in_use` con el contenedor;
   - si no existe → `404`;
   - si existe y está libre → `204` y elimina la fila.
9. Todos los endpoints de tenant (`read`/`manage`, incluidos los existentes)
   pasan a `platform_only=True`: un rol de tenant con el permiso por error
   sigue recibiendo `403`.
10. `tenant.domain` no se modifica por añadir o quitar dominios administrados.

### Dominios

11. `check_domain(tenant_context, domain)`:
    - dominio inválido → `AppError 422`;
    - primario de un sitio (de cualquier tenant) → `available=false`,
      `reason` nombra el contenedor;
    - extra del proxy de otro contenedor → `available=false`, `reason` nombra el
      contenedor;
    - propiedad de otro tenant → `available=false`, `reason` indica que otro
      tenant lo administra;
    - libre o propiedad del tenant actual → `available=true`;
    - `suffix` es informativo: el `site_suffix` del tenant o `null`. **No**
      condiciona `available`.
12. `list_domains` para un tenant incluye:
    - primarios de sus sitios,
    - extras cuyo contenedor es suyo,
    - cualquier dominio del conjunto propiedad del tenant que ya esté publicado
      (no inventa filas sin sitio).
    Un dominio primario o extra publicado por un contenedor del tenant se
    considera parte de su namespace aunque todavía no tenga fila en
    `tenant_domains` (la migración no puede leer el YAML del proxy). Un tenant
    nunca recibe dominios de otro tenant.
    `outside_tenant` e `include_outside` se eliminan del contrato, del router,
    del schema y de la UI: ya no hay un caso observable que los active y
    mantenerlos sugeriría una semántica inexistente. `certificate` valida el
    dominio contra esa misma lista, sin flag adicional.
13. `DomainsService.add` reclama en `tenant_domains` el dominio libre en la
    misma transacción; si otro tenant lo posee → `409 domains.already_owned`.
14. `remove` sigue siendo solo para extras del proxy y no borra filas de
    `tenant_domains` de dominios primarios.

### Hosting

15. `create_site` deja de exigir `site_suffix`:
    - dominio poseído por el tenant actual → permitido;
    - dominio libre (sin dueño, sin primario, sin extra) → permitido y se
      inserta la fila de propiedad en la misma transacción;
    - dominio de otro tenant → `409 hosting_site_domain_conflict`;
    - dominio extra de otro contenedor → se mantiene
      `409 hosting_site_conflict`;
    - si el alta del sitio falla después de reclamar, la fila de
      `tenant_domains` no queda (rollback de la transacción).
    `app=None` (tests aislados) conserva el comportamiento actual sin validar
    propiedad.

### Correo

16. `MailService` resuelve el conjunto de dominios del tenant como
    `{tenant.domain}` ∪ `tenant_domains`. `tenant.domain` sigue siendo el
    dominio por defecto.
17. `GET /mail/accounts` devuelve `domains: list[str]` (ordenados, con el
    primario primero) además de `domain`. Sin `can_read_all`, filtra cuentas por
    cualquier dominio del conjunto. Con `can_read_all` sigue devolviendo todas y
    `domain="all"`.
18. `POST /mail/accounts` acepta `domain` opcional. Si falta, usa
    `tenant.domain`. Si el dominio no está en el conjunto → `403
    domain_mismatch`.
19. `PUT /mail/accounts/{email}/password` acepta cualquier dominio del conjunto,
    salvo `can_manage_all`, que mantiene el comportamiento actual.
20. La UI de correos muestra un selector de dominio cuando el conjunto tiene más
    de uno y usa el elegido al crear la cuenta; el sufijo `@dominio` refleja la
    selección.

### Unificación

21. `scripts/unify_tenants.py`:
    - `--target-slug` (obligatorio), `--domain` repetible, `--from-slug`
      repetible, `--all-others`, `--move-all-sites`, `--apply`, `--plan PATH`,
      `--rollback PATH`, `--purge`.
    - **Dry-run por defecto**: sin `--apply` no escribe. Siempre escribe el plan
      JSON (`--plan`, por defecto `logs/unify-tenants-<timestamp>.json`).
    - Resuelve target y orígenes; falla con mensaje claro si el target no existe
      o si un slug no existe.
    - Reclama cada `--domain` para el target; si pertenece a otro tenant y ese
      tenant no está entre los orígenes → error antes de escribir.
    - Mueve a target los `hosting_sites` de los orígenes cuyo dominio esté en
      `--domain`; con `--move-all-sites`, todos los de los orígenes. Nunca
      cambia `container_name`, `domain` ni `kind`.
    - Mueve usuarios de los orígenes a target si su email no existe allí; si
      existe, desactiva el usuario origen y lo reporta. Nunca borra usuarios.
    - Desactiva (`is_active=false`) los tenants origen. No cambia su `slug` ni
      su `domain`.
    - Registra auditoría por cada cambio con `tenant_id` del target, módulo
      `tenant`, acciones `tenant.unify.site`, `tenant.unify.user`,
      `tenant.unify.domain`, `tenant.unify.deactivate`.
    - Es idempotente: una segunda ejecución con los mismos argumentos no cambia
      filas y reporta `0 cambios`.
22. `--rollback PLAN` revierte exactamente lo registrado en el plan: devuelve
    sitios y usuarios a su tenant original, reactiva orígenes y elimina solo las
    filas de `tenant_domains` creadas por la ejecución. No toca filas
    preexistentes.
23. `--purge` elimina un tenant origen **solo** si no quedan dependencias en
    `users`, `hosting_sites`, `branches`, `user_context_claims`, `documents`,
    `signatures`, `tenant_permissions`, `events` ni `audit_logs`. Si queda
    alguna, lista los conteos, no borra nada y sale con código `1`. Por defecto
    los orígenes quedan desactivados, nunca borrados: la auditoría se preserva.
24. El script no ejecuta Docker, no reescribe YAML, no reinicia contenedores ni
    emite certificados.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Un dominio tiene como máximo un tenant propietario, garantizado por la PK de tenant_domains y por 409 en API y script.
    proof: test de inserción duplicada en dos tenants (IntegrityError traducido a 409) y test del script que se niega a reclamar un dominio de un tercero.
  - id: I2
    statement: El site_suffix sigue siendo configuración del tenant pero ya no decide disponibilidad, visibilidad ni creación de sitios.
    proof: tests con sufijo spanel y dominios debug.com/test2.com disponibles y creables; el sufijo solo aparece como dato informativo.
  - id: I3
    statement: tenants.domain no se reescribe al añadir o quitar dominios administrados ni al unificar cuentas.
    proof: comparación antes/después en tests de servicio y del script.
  - id: I4
    statement: Un tenant sin core.platform.admin solo ve dominios de su conjunto de propiedad o publicados por sus propios contenedores.
    proof: test de list_domains con dominios de otro tenant, primarios y extras; nunca aparecen.
  - id: I5
    statement: Crear un sitio reclama el dominio libre y, si el alta falla, la transacción no deja la reclamación.
    proof: test con provisioner que falla; assert de tenant_domains sin la fila.
  - id: I6
    statement: El correo no expone cuentas ni permite crear/editar en dominios que el tenant no posee.
    proof: tests de list/create/change_password con dominios propios y ajenos.
  - id: I7
    statement: La unificación no borra auditoría ni usuarios y no toca contenedores, YAML, Traefik ni certificados.
    proof: test del script que verifica audit_logs intactos, ausencia de llamadas a Docker/proxy y usuarios desactivados o movidos, nunca eliminados.
  - id: I8
    statement: Purge solo borra tenants sin dependencias; con dependencias falla y no escribe.
    proof: test con un tenant con auditoría y otro vacío.
  - id: I9
    statement: Los contratos de hosting siguen sin exponer tenant_id y los tests existentes de hosting/mail/tenant/domains siguen verdes.
    proof: pytest de los cuatro plugins; test_los_contratos_no_exponen_tenant_id verde.
  - id: I10
    statement: La migración es aditiva, idempotente y reversible; up/down/up no altera datos de otras tablas.
    proof: test de migración con SQLite: upgrade, columnas/PK, downgrade, ausencia, upgrade otra vez.
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

PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest scripts/tests -q

cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

### V1 — Contrato y migración

- `normalize_domain_name(" Debug.COM. ")` → `debug.com`.
- Inválidos (`https://x`, `x:443`, `*.x`, `x y`, vacío) → `ValueError`.
- `domain_in_namespace` es `True` para el dueño y `False` para otro tenant.
- Sin providers: `list_tenant_domains` → `set()` y `domain_owner` → `None`.
- Migración: crea `tenant_domains`; backfill de `tenants.domain` y de
  `hosting_sites.domain`; duplicado entre sitios conserva el primero;
  downgrade elimina solo la tabla; up/down/up estable.

### V2 — API de tenants

- Listar dominios de un tenant devuelve los suyos ordenados.
- Añadir uno libre → `201`; repetirlo → idempotente.
- Añadir el de otro tenant → `409 tenant_domain_conflict`.
- Añadir el primario de un sitio ajeno → `409`.
- Quitar un dominio en uso por un sitio o extra → `409 tenant_domain_in_use`.
- Quitar uno libre → `204`.
- Usuario sin `core.platform.admin` → `403` en todos los endpoints de tenant.

### V3 — Dominios y hosting

- Con sufijo `spanel`, `debug.com` y `test2.com` son `available=true`.
- `check` no depende del sufijo y no escribe.
- `list_domains` de `demo` muestra `spanel.site`, `debug.com` y `test2.com`
  cuando son primarios de sus sitios; no muestra dominios de otro tenant.
- `create_site` con dominio libre lo reclama; con dominio de otro tenant → `409
  hosting_site_domain_conflict`; con extra ajeno → `409 hosting_site_conflict`.
- Si la creación falla, `tenant_domains` no conserva la fila.

### V4 — Correo

- `list_accounts` devuelve `domains` con el primario primero y filtra cuentas
  de todos los dominios propios.
- `create_account` con `domain="debug.com"` funciona si es propio; con un
  dominio ajeno → `403 domain_mismatch`.
- `change_password` acepta cualquier dominio propio y rechaza uno ajeno.

### V5 — Unificación

- Dry-run: no escribe nada y genera plan JSON.
- `--apply`: mueve sitios y usuarios, reclama dominios, desactiva orígenes y
  audita; segunda ejecución reporta `0 cambios`.
- `--rollback`: restaura tenants de sitios y usuarios, reactiva orígenes y
  elimina solo las reclamaciones creadas por la ejecución.
- `--purge` con dependencias → no borra y código `1`; sin dependencias → borra.
- `tenants.domain` y `audit_logs` intactos; ninguna llamada a Docker.

## ROLLBACK

- La migración define `downgrade()` y V1 lo ejecuta. Elimina solo
  `tenant_domains`; los datos de tenants, sitios y correo quedan intactos.
- El script escribe un plan JSON antes de aplicar y `--rollback` lo revierte.
  Es la vía para deshacer una unificación real.
- Revertir el commit restaura la validación por sufijo. Los dominios añadidos
  después quedarían fuera de la vista de un tenant normal hasta reconfigurar su
  sufijo o mantener la tabla: exportar `tenant_domains` antes del rollback y
  recargarla después.
- No hay cambios de infraestructura: no hay contenedores, rutas ni certificados
  que compensar.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0037-tenant-domain-namespace-and-unification.md
    - vendor/systutor-core/src/systutor/contracts/domain_namespace.py
    - vendor/systutor-core/plugins/tenant/backend/models.py
    - vendor/systutor-core/plugins/tenant/migrations/002_tenant_domains.py
    - vendor/systutor-core/plugins/tenant/backend/register.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/tenant/backend/schemas.py
    - vendor/systutor-core/plugins/tenant/backend/router.py
    - vendor/systutor-core/plugins/tenant/backend/tests/**
    - vendor/systutor-core/plugins/tenant/frontend/api.ts
    - vendor/systutor-core/plugins/tenant/frontend/pages/TenantsPage.tsx
    - vendor/systutor-core/plugins/tenant/README.md
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/register.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/tests/**
    - vendor/systutor-core/plugins/domains/frontend/api.ts
    - vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/schemas.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/tests/**
    - vendor/systutor-core/plugins/mail/frontend/api.ts
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
    - scripts/unify_tenants.py
    - scripts/tests/test_unify_tenants.py
    - scripts/sync_tenant_domains.py
    - scripts/tests/test_sync_tenant_domains.py
    - docs/tenant-domains.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/src/systutor/core/**
    - vendor/systutor-core/migrations/**
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/domains/backend/proxy/**
    - vendor/systutor-core/plugins/mail/backend/certificates/**
    - vendor/systutor-core/plugins/mail/backend/mailserver.py
    - vendor/systutor-core/plugins/filemanager/**
    - docker-compose.yml
    - scripts/spanel_static/**
    - scripts/traefik-lab/**
    - scripts/spanel_cert_agent/**
    - systutor-installer/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - propiedad de dominios (tenant_domains)
    - API y UI del módulo de tenants
    - GET /domains, GET /domains/check, POST /domains
    - POST /hosting/sites
    - GET/POST /mail/accounts y PUT de contraseña
    - scripts/unify_tenants.py
  indirect:
    - visibilidad de dominios por autorización
    - UI de correos (selector de dominio)
    - operación de consolidación de cuentas
  must_not_affect:
    - tenants.domain y su semántica de correo primario
    - hosting_sites (filas, contenedores, dominios, kind)
    - YAML del proxy, Traefik, rutas y certificados
    - autenticación, RBAC, aislamiento y contratos de hosting
    - auditoría y migraciones del kernel y de otros plugins
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013
    - 0027
    - 0030
    - 0031
    - 0035
  must_compose_with:
    - plugin tenant (tenant.domain, tenant_site_settings)
    - plugin hosting (dominios primarios)
    - plugin domains (extras del proxy)
    - plugin mail (cuentas por dominio)
  systemic_invariants:
    - un dueño por Host
    - un dueño por dominio
    - aislamiento por tenant derivado del token
    - auditoría de toda escritura
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest plugins/tenant/backend/tests plugins/domains/backend/tests plugins/hosting/backend/tests plugins/mail/backend/tests -q
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest scripts/tests -q
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la propiedad vive en tenant_domains y se consume por contrato; cada plugin mantiene una sola responsabilidad
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/src/systutor/contracts/domain_namespace.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/service.py
    - scripts/unify_tenants.py
```

## Traceability

- Requirement: un tenant administra un conjunto explícito de dominios
  (spanel.site, debug.com, test2.com en `demo`), desde el módulo de tenants, y
  las cuentas antiguas se consolidan en él sin perder auditoría.
- owner: luc444s
- approver: luc444s
- Commit:
- Deployment: local; la consolidación real se ejecuta con
  `scripts/unify_tenants.py --target-slug demo --domain spanel.site --domain debug.com --domain test2.com --all-others --apply` y se revisa el plan JSON. Requiere backup previo de la base.

## Definition of Done

- [ ] Objetivo: `demo` administra sus tres dominios y los antiguos quedan unificados.
- [ ] Alcance respetado.
- [ ] Contrato satisfecho (tabla, API, dominios, hosting, correo, script).
- [ ] Verdad independiente y falsable existe ya.
- [ ] Invariantes I1–I10 preservados y probados.
- [ ] V0–V5 ejecutados.
- [ ] Rollback de migración y del script demostrado.
- [ ] Composición con 0030/0031/0035 verificada.
- [ ] Sin archivos fuera de `change_surface`.
- [ ] Nada >600 líneas; ideal <400.
- [ ] Trazabilidad completa.
