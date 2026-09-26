# A.SPEC 0001 — Cache mail accounts list in Redis with TTL

> `risk: normal` — reversible, no migración, no cambio de esquema, blast radius acotado a
> un endpoint de lectura de un plugin. Señales §4.1 verificadas: sin rollback destructivo,
> sin invariante en riesgo, sin migración, sin tocar componente crítico (`core/cache.py` se
> consume, no se modifica). Mode: `extreme-poverty`.

## WHY

La página "Correos" no carga rápido porque cada render abre un viaje completo al servidor
de correo:

```
MailAccountsPage.tsx:52   useQuery(queryKey: mailKeys.accounts)   staleTime = 0
  → router.py:39          GET /api/v1/plugins/mail/mail/accounts
    → service.py:48       provider.list_accounts()                 sin caché
      → provider.py:104   docker exec {container} setup email list
        → provider.py:82  sshpass -e ssh -t {user}@{host}          timeout 30s
```

`MAIL_USE_SSH=true` en producción, así que cada request paga handshake SSH + `docker exec`
en el contenedor DMS. react-query corre con `staleTime = 0` (`providers.tsx:13-20` solo
declara `refetchOnWindowFocus: false` y `retry: false`), por lo que navegar fuera de la
página y volver vuelve a pagar el viaje. Lo mismo en `MailAccountsPage.tsx:65` y `:78`,
donde `invalidateQueries` tras cambiar contraseña o crear cuenta dispara otro round-trip
completo.

El resultado medible: la carga de la lista depende de la latencia de un servidor remoto,
cuando el dato es una lista de strings derivada y volátil.

## WHAT

`GET /api/v1/plugins/mail/mail/accounts` deja de consultar al servidor de correo cuando
existe una entrada válida en caché, y la UI deja de repedir esa consulta en cada
navegación.

Verdad nueva, independiente y falsable, verificable ahora mismo: **dos requests
consecutivos a `GET /mail/accounts` producen una sola llamada al `MailProvider`.**

## SCOPE

- Caché con TTL de la lista **cruda** de cuentas, en el `CacheBackend` que el kernel ya
  inicializa con Redis en `lifecycle.py:100`.
- Lectura transparente (read-through) en `MailService.list_accounts`.
- Invalidación explícita tras `create_account` exitoso.
- `staleTime` en el `useQuery` del frontend para no repedir en cada navegación.
- Dos variables de configuración con default sensato e interruptor de apagado.
- Tests de service y de router con provider fake y cache fake.

## OUT OF SCOPE

- Lectura de mensajes, adjuntos o cuerpos de correo. No existe hoy; sería feature nueva,
  no optimización.
- Cualquier tabla nueva en PostgreSQL, y por tanto cualquier migración de kernel o de
  plugin. Ver "Restricción de diseño" abajo.
- Sincronización en background, push desde DMS, o cron de revalidación.
- Historial, métricas o auditoría de buzones eliminados.
- Búsqueda de texto sobre correos.
- Refactor oportunista de `provider.py`, `router.py` o `schemas.py`.
- Cambios en los defaults globales de react-query (`providers.tsx`).

## Restricción de diseño: por qué Redis y no una tabla

`SPEC_MAIL_PLUGIN.md:7` establece:

> **DMS is the sole source of truth for mailboxes.** No mailbox data stored in PostgreSQL.

Esta A.SPEC no lo contradice: no agrega esquema, y Redis es caché derivada y descartable.
Perder la caché no pierde información — el siguiente request la reconstruye desde DMS.

El espejo en PostgreSQL se evaluó y queda **descartado por esta restricción**, con
independencia de que fuera la opción de menor esfuerzo.

## CONTRACT

**Precondiciones**

- `init_cache(settings.redis_url)` se ejecutó en el lifespan; `systutor.core.cache.cache()`
  devuelve un backend operativo.
- `provider.list_accounts()` cumple su contrato actual: retorna `list[str]` de emails
  válidos. Sin cambios en esta A.SPEC.

**Postcondiciones**

- Con entrada de caché válida, el request responde **sin** invocar al provider.
- Tras un `create_account` exitoso, la entrada de caché queda eliminada; el siguiente
  request relee de DMS.
- Con caché deshabilitada, el comportamiento es byte a byte el actual.

**Verdad nueva establecida**

Dos requests consecutivos a `GET /mail/accounts` producen una sola llamada al
`MailProvider`, y un fallo de la caché nunca produce una respuesta 5xx.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      La forma de la respuesta no cambia. GET /mail/accounts sigue devolviendo
      {domain: str, accounts: list[str]}. Un cliente compilado contra el contrato
      actual no puede detectar el cambio.
    proof: test_response_contract_unchanged

  - id: I2
    statement: >
      El aislamiento por tenant no se relaja. Un tenant sigue viendo únicamente
      los buzones de su propio dominio, y superadmin sigue viendo la lista completa
      con domain="all".
    proof: test_list_accounts_filters_by_tenant_domain + test_cache_hit_preserves_tenant_isolation

  - id: I3
    statement: >
      El mapeo de error se preserva: un AppError 500 del provider sigue traduciéndose
      a 503 service_unavailable. Caché fría + provider caído = 503, igual que hoy.
    proof: test_provider_error_still_maps_to_503

  - id: I4
    statement: >
      create_account sigue levantando 409 cuando el buzón ya existe. La invalidación
      de caché no puede tragarse ese error.
    proof: test_create_account_conflict_still_409

  - id: I5
    statement: >
      Un fallo de la caché degrada el rendimiento, nunca la disponibilidad. Redis caído,
      corrupto o lento no produce 5xx en el endpoint de lectura.
    proof: test_cache_read_failure_falls_back_to_provider + test_cache_write_failure_does_not_break_request

  - id: I6
    statement: >
      change_password NO invalida la caché, porque no modifica el conjunto de cuentas.
    proof: test_change_password_does_not_invalidate_cache

  - id: I7
    statement: >
      La caché no altera qué es una escritura. create_account sigue siendo la única
      operación que muta estado en DMS, y siguerequiriendo su llamada al provider.
    proof: test_create_account_still_calls_provider + test_cache_hit_does_not_short_circuit_writes

  - id: I8
    statement: >
      La clave de caché es única y compartida entre tenants. El cacheo de un tenant no
      puede provocar que otro reciba una lista filtrada ajena.
    proof: test_single_cache_key_shared_across_tenants

  - id: I9
    statement: >
      El comportamiento con la caché deshabilitada es indistinguible del actual.
    proof: test_cache_disabled_by_setting

  - id: I10
    statement: >
      Los defaults globales de react-query no se modifican. staleTime se declara en el
      query de mail, no en providers.tsx, para no alterar otros plugins.
    proof: diff check — providers.tsx ausente de change_surface
```

## VERIFICATION

Suite nueva en `plugins/mail/backend/tests/`, con `MailProvider` fake (contador de
llamadas) y `CacheBackend` fake en memoria.

**Service** (`test_service.py`)

| Test | Invariante | Qué falsaría |
|---|---|---|
| `test_response_contract_unchanged` | I1 | Forma de respuesta alterada |
| `test_list_accounts_filters_by_tenant_domain` | I2 | Fuga cross-tenant |
| `test_cache_hit_preserves_tenant_isolation` | I2 | HIT sirve la lista equivocada |
| `test_list_accounts_populates_cache_on_miss` | — | MISS no rellena |
| `test_second_list_does_not_call_provider` | — | **La verdad nueva** |
| `test_single_cache_key_shared_across_tenants` | I8 | Clave por tenant |
| `test_cache_hit_does_not_short_circuit_writes` | I7 | HIT evita un create |
| `test_create_account_invalidates_cache` | — | Buzón nuevo invisible hasta TTL |
| `test_change_password_does_not_invalidate_cache` | I6 | Invalidación de más |
| `test_create_account_still_calls_provider` | I7 | Write sin efecto en DMS |
| `test_create_account_conflict_still_409` | I4 | 409 tragado |
| `test_provider_error_still_maps_to_503` | I3 | Mapeo perdido |
| `test_cache_read_failure_falls_back_to_provider` | I5 | Redis caído = 5xx |
| `test_cache_write_failure_does_not_break_request` | I5 | Redis caído = 5xx |
| `test_corrupt_cache_value_treated_as_miss` | I5 | Basura interpretada como lista |
| `test_cache_disabled_by_setting` | I9 | Interruptor inoperante |

**Router** (`test_router.py`)

| Test | Invariante | Qué falsaría |
|---|---|---|
| `test_two_requests_one_provider_call` | — | **La verdad nueva, end-to-end** |
| `test_response_contract_unchanged` | I1 | Contrato roto por capa |

**Gates** (los tres, sin excepción)

```bash
cd vendor/systutor-core && ruff check .
cd vendor/systutor-core && python3 -m pyright
cd vendor/systutor-core && python3 -m pytest tests plugins/mail/backend/tests -q
```

**Proof de composición**: `grep -n "staleTime" apps/web/src/app/providers.tsx` debe
retornar vacío. La ausencia es la prueba de I10.

**Lo que esta A.SPEC NO prueba**, declarado explícitamente: no hay medición de latencia
antes/después. El host de producción y el contenedor DMS no son accesibles desde el
entorno de desarrollo. La mejora de rendimiento es consecuencia mecanica de I-verify
`test_two_requests_one_provider_call`; la magnitud en milisegundos no se afirma aquí.

## ROLLBACK

Reversible, sin residuos. No hay migración, ni cambio de esquema, ni datos persistidos.

```bash
git revert <commit>
```

Deshace backend y frontend en un paso. Lo que queda en Redis son claves con prefijo
`systutor:cache:mail:accounts:raw`, que expiran solas por TTL y nunca se leen de nuevo.
No hace falta limpieza manual.

Rollback manual, sin depender de git: poner `MAIL_ACCOUNTS_CACHE_ENABLED=false` y
reiniciar. Deja la UI lenta pero funcional. Es la válvula de escape para producción.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/settings.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_router.py
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
    - .env.docker.example
    - vendor/systutor-core/plugins/mail/README.md
    - README.md
  prohibited:
    - vendor/systutor-core/src/systutor/core/cache.py     # se consume, no se modifica
    - vendor/systutor-core/plugins/mail/backend/provider.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/schemas.py
    - vendor/systutor-core/plugins/mail/migrations/        # no hay migración
    - vendor/systutor-core/migrations/                    # no hay migración
    - docker-compose.yml                                  # Redis ya está desplegado
    - apps/web/src/app/providers.tsx                      # defaults globales intocables
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - GET /api/v1/plugins/mail/mail/accounts   # lectura, ahora servida desde Redis
    - POST /api/v1/plugins/mail/mail/accounts  # única escritura, ahora invalida
    - PUT  /api/v1/plugins/mail/mail/accounts/{email}/password  # toca cache, no la invalida
    - MailAccountsPage.tsx                      # un useQuery, cambia cuándo dispara
    - Redis                                     # nueva carga de lecturas, O(1) por request
  indirect:
    - Otros tenants                           # comparten la entrada de caché
    - Frontend de otros plugins                 # comparten react-query, defaults intactos
    - Memoria del proceso                       # fallback MemoryCacheBackend si Redis cae
  must_not_affect:
    - surface: tenant_isolation
      surfaces: [MailService.list_accounts]
      invariant: I2
    - surface: error_mapping_500_to_503
      surfaces: [MailService.list_accounts]
      invariant: I3
    - surface: write_path_to_dms
      surfaces: [MailService.create_account, MailService.change_password]
      invariant: I7
    - surface: cache_backend_implementation
      surfaces: [systutor.core.cache]
      invariant: prohibido en change_surface
    - surface: react_query_global_defaults
      surfaces: [apps/web/src/app/providers.tsx]
      invariant: I10
    - surface: postgres_schema
      surfaces: [todas las tablas]
      invariant: no hay migración en change_surface
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with: []
  systemic_invariants:
    - El kernel sigue siendo agnóstico de dominio: mail es un plugin, no un módulo core.
      cache() es genérico y no se modifica.
    - Ningún otro plugin cambia de comportamiento: la superficie de este cambio es
      un solo plugin más dos variables de entorno.
  composition_checks:
    - python3 -m pytest tests -q   # suite completa del kernel, no solo mail
    - python3 -m pyright
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
    - plugins/mail/backend/service.py   # la lógica de read-through vive acá, no en el router
```

`router.py` queda igual: la lógica de caché va en `service.py`. El entrypoint sigue siendo
coordinación. `service.py` va de 129 a ~175 líneas, debajo del umbral de revisión.

## Traceability

- **Requirement**: la lista de correos no debe depender de la latencia de SSH/DMS
- **A.SPEC**: este documento
- **Code**: `plugins/mail/backend/service.py`, `plugins/mail/backend/settings.py`,
  `plugins/mail/frontend/pages/MailAccountsPage.tsx`
- **Migration**: ninguna (por diseño, ver "Restricción de diseño")
- **Test**: `plugins/mail/backend/tests/test_service.py`, `test_router.py`
- **Commit**: `TBD` — se llena con el SHA literal al integrar (`SPECIFICATION.md` §11)
- **Deployment**: rebuild de la imagen Docker; sin cambios en compose ni migraciones

- **owner**: `TBD` — responsable del cambio
- **approver**: `TBD` — quien libera la integración (`SPECIFICATION.md` §10.2)

> `owner` y `approver` están sin resolver. `SPECIFICATION.md` §10.2 los hace obligatorios:
> sin `owner` y `approver` esto es un GAP, no un cierre honesto. Se llenan antes de
> integrar.

## Definition of Done

- [ ] Objective satisfied
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
