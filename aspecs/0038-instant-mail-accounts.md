# A.SPEC 0038 — Instant mail account list: stale-while-revalidate and a persisted snapshot

> `risk: normal` — solo cambia cuándo se consulta DMS y qué pinta la UI mientras revalida.
> Sin migración, sin cambio de contrato HTTP, sin esquema nuevo. Rollback: revert + borrar
> dos variables de entorno.

## WHY

La página **Correos** se siente lenta aunque exista caché. La razón no es que falte caché:
es que la caché **expira en el camino del usuario**.

Trazado actual (A.SPEC 0001 + 0006):

```
MailAccountsPage.tsx  useQuery(staleTime = 60 s)
  → GET /api/v1/plugins/mail/mail/accounts
    → MailService.list_accounts
      → Redis: systutor:cache:mail:accounts:raw (TTL 300 s)
        MISS o vencida → provider.list_accounts()
          → docker exec ... setup email list   (o ssh + docker exec)
```

Con visitas espaciadas más de 5 minutos, la clave siempre está vencida y **cada visita paga
el viaje completo**: handshake/`docker exec` + el arranque de `setup email list` dentro de
DMS. El TTL de 300 s no ayuda a quien entra cada 10 o 20 minutos, que es exactamente el uso
normal de una pantalla de administración.

Dos capas de latencia se suman y ninguna se puede eliminar del todo:

| Capa | Costo | ¿Se puede evitar? |
|---|---|---|
| Red del cliente | medido desde una red móvil: ~0.2 s TCP + ~0.2 s TLS hasta el `ttfb` de cualquier endpoint | no, salvo pintar antes |
| Backend (auth + DB) | ~0.2 s por request autenticado | no sin tocar auth |
| `docker exec setup email list` | desconocido en este entorno, del orden de cientos de ms a segundos | sí: no hacerlo en el request |
| Arranque del bundle la primera vez | fuera de alcance | — |

La verdad que esta A.SPEC establece, falsable con tests: **una visita con la entrada vencida
responde sin llamar al provider, y el provider se consulta en segundo plano.** Además, una
recarga de pestaña pinta la última lista conocida antes de que el request salga.

## WHAT

1. **Backend — stale-while-revalidate.** La entrada de Redis guarda
   `{"accounts": [...], "stored_at": <epoch>}`.
   - `age <= MAIL_ACCOUNTS_CACHE_TTL` (300 s): se responde y no se consulta nada.
   - `TTL < age <= MAIL_ACCOUNTS_CACHE_STALE_TTL` (86 400 s): se responde **ya** y se
     dispara un refresh en segundo plano, con un solo vuelo por proceso.
   - `age > STALE_TTL` o entrada ausente: se consulta al provider en línea, como hoy.
   - `MAIL_ACCOUNTS_CACHE_SWR=false`: comportamiento anterior (bloquear al vencer el TTL).
   - Entradas con el formato viejo (lista JSON sin `stored_at`) se sirven una vez y se
     actualizan en segundo plano: un deploy no convierte la caché en un arranque en frío.
2. **Alta de buzón.** En vez de borrar la clave, se agrega el buzón a la lista cacheada y se
   agenda el refresh. Así el `invalidateQueries` que dispara la UI encuentra datos y no paga
   otro viaje.
3. **Frontend — pintar antes de revalidar.** La última respuesta se guarda por tenant en
   `sessionStorage` y se usa como `initialData`. La tabla aparece sin esperar el request; si
   la revalidación falla se muestra la lista conocida con un aviso, no una pantalla de error.
   `staleTime` pasa a 300 s. Un alta se refleja de forma optimista con el email que devuelve
   la API, sin refetch bloqueante.
4. **Observabilidad.** Los warnings de fallo de refresh y de caché ya existen; no se agregan
   logs con datos sensibles.

## SCOPE

Entra:

- `plugins/mail/backend/service.py` (formato de caché, SWR, refresh, alta optimista).
- `plugins/mail/backend/settings.py` y `.env.docker.example` (dos variables).
- `plugins/mail/frontend/pages/MailAccountsPage.tsx` (snapshot, `initialData`, estados).
- Tests de servicio y del contrato de TTL del frontend.
- `plugins/mail/README.md`.

No entra:

- Cambiar el contrato `{domain, domains, accounts}` ni los códigos de error.
- Tocar autenticación, Traefik, Redis, DMS o `docker-compose.yml`.
- Espejar buzones en PostgreSQL (sigue prohibido por `SPEC_MAIL_PLUGIN.md`).
- Cambiar el transporte SSH (`MAIL_SSH_CONTROL_*` de 0006) ni el provider.
- Un scheduler en background permanente: el refresh solo se dispara cuando alguien lee.
- Persistir el snapshot más allá de la pestaña (`sessionStorage`, no `localStorage`).

## CONTRACT

**Precondiciones**

- `MAIL_ACCOUNTS_CACHE_ENABLED=true` y un `CacheBackend` operativo (Redis o memoria).
- `provider.list_accounts()` sigue devolviendo `list[str]`.

**Postcondiciones**

- Dos lecturas dentro del TTL producen una sola llamada al provider (se conserva 0001).
- Una lectura con la entrada vencida y `age <= STALE_TTL` responde con la lista vieja y
  programa exactamente un refresh.
- Ese refresh, al ejecutarse, reescribe la clave con la lista nueva y `stored_at` nuevo.
- Si el refresh falla, la respuesta servida no cambia y la entrada no se borra.
- Un alta exitosa deja la lista cacheada con el buzón nuevo, sin obligar a un refetch.
- Sin snapshot previo, la UI mantiene el `Cargando...` de hoy.

**Verdad falsable**

`test_expired_entry_is_served_and_refresh_scheduled`: antes de esta A.SPEC, una entrada
vencida llamaba al provider dentro del request; después, devuelve la lista vieja con
`provider.list_calls == 0` y el refresh ocurre recién al ejecutar el runner.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: La forma de la respuesta HTTP no cambia; un cliente viejo no nota nada.
    proof: test_response_contract_unchanged (0001) sigue verde.
  - id: I2
    statement: El filtrado por tenant ocurre después de la caché, nunca dentro.
    proof: test_cache_hit_preserves_tenant_isolation + test_single_cache_key_shared_across_tenants.
  - id: I3
    statement: Un fallo de caché o de refresh degrada rendimiento, nunca disponibilidad.
    proof: test_cache_read_failure_falls_back_to_provider, test_background_refresh_failure_keeps_serving_stale.
  - id: I4
    statement: El refresh es de un solo vuelo por proceso; un burst no multiplica docker exec.
    proof: _REFRESH_LOCK no bloqueante; el hilo que lo toma lo libera en finally.
  - id: I5
    statement: Una entrada más vieja que STALE_TTL nunca se sirve; se consulta en línea.
    proof: test_entry_older_than_stale_cap_blocks_on_provider.
  - id: I6
    statement: Con SWR apagado el comportamiento es el de 0006.
    proof: test_swr_disabled_blocks_on_expired_entry.
  - id: I7
    statement: El alta no se declara exitosa sin haber escrito en DMS.
    proof: se actualiza la caché solo después del provider, igual que 0001; test_create_account_still_calls_provider.
  - id: I8
    statement: El snapshot del navegador nunca se comparte entre tenants.
    proof: clave por tenant + validación de tenantId al leer + sessionStorage.
```

## VERIFICATION

Suite `plugins/mail/backend/tests/test_service.py` (nuevos):

| Test | Invariante |
|---|---|
| `test_expired_entry_is_served_and_refresh_scheduled` | verdad nueva |
| `test_fresh_entry_is_served_without_refresh` | I1 |
| `test_entry_older_than_stale_cap_blocks_on_provider` | I5 |
| `test_swr_disabled_blocks_on_expired_entry` | I6 |
| `test_legacy_list_entry_is_served_and_upgraded` | compatibilidad de deploy |
| `test_background_refresh_failure_keeps_serving_stale` | I3 |
| `test_create_account_updates_cache_without_refetch` | alta instantánea |

Gate ejecutado en este entorno:

```text
pytest plugins/mail/backend/tests plugins/hosting/backend/tests plugins/domains/backend/tests -q
  -> 354 passed
ruff check (archivos tocados) -> All checks passed
pyright plugins/mail/backend/service.py settings.py -> 0 errors
apps/web tsc --noEmit -> sin errores
```

**No verificado acá**: latencia real contra DMS producción (no hay Docker ni acceso
autenticado desde este entorno). La mejora se afirma como consecuencia de I-verify, no como
una cifra de milisegundos. Medición pendiente en el host: `docker logs` con el warning de
refresh y dos `GET` consecutivos con la clave vencida a propósito.

## ROLLBACK

- `MAIL_ACCOUNTS_CACHE_SWR=false` y reiniciar: vuelve a bloquear al vencer el TTL.
- `git revert <sha>`: deshace backend y frontend; el snapshot en `sessionStorage` se
  descarta al cerrar la pestaña.
- No hay migración ni dato persistido que limpiar. Las claves viejas (lista JSON) siguen
  siendo legibles por el código anterior.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/settings.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_provider.py
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
    - vendor/systutor-core/plugins/mail/README.md
    - .env.docker.example
    - aspecs/0038-instant-mail-accounts.md
  prohibited:
    - vendor/systutor-core/plugins/mail/backend/provider.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/schemas.py
    - vendor/systutor-core/src/systutor/core/cache.py
    - vendor/systutor-core/src/**            # el kernel no sabe de correo
    - docker-compose.yml
    - .env.docker                             # valores de despliegue, no de código
```

## Traceability

- Requirement: la lista de correos no debe esperar a DMS ni en la primera visita tras el TTL
  ni al recargar la página.
- owner / approver: pendientes de asignar antes de integrar.
- Commit: pendiente.
- Deployment: rebuild de la imagen. Las variables nuevas tienen default seguro; no hace
  falta cambiarlas.
