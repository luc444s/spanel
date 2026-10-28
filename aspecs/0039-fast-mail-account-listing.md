# A.SPEC 0039 — Read the mailbox list from the DMS accounts file

> `risk: normal` — cambia el comando que enumera buzones, no el contrato ni el esquema.
> Rollback con `MAIL_ACCOUNTS_SOURCE=setup` y reinicio, sin revertir código.

## WHY

`GET /mail/accounts` obtenía la lista con:

```
docker exec <dms> setup email list
```

Ese comando **no es una lectura de archivo**. En docker-mailserver `setup email list`
(`target/bin/listmailuser`) recorre la lista de cuentas y, por cada buzón:

1. si `ENABLE_QUOTAS=1`, ejecuta `doveadm quota get -u <cuenta>` y formatea el resultado;
2. busca los alias del buzón en `postfix-virtual.cf` con `grep`;
3. imprime `* email ( cuota ) [aliases]`.

Con N buzones eso son N subprocesos `doveadm` y N `grep`. La lista se "pinta uno a uno"
porque el costo es O(N), y con una cantidad real de cuentas el comando tarda segundos o
minutos. Después de A.SPEC 0038 ese costo quedó fuera del request normal (refresh en
segundo plano), pero seguía golpeando el primer arranque, el refresh y el caso de Redis
vacío. No hace falta adivinar: el código de DMS lo muestra.

## WHAT

Cuando el provisioner es `FILE`, las cuentas viven en una sola línea por buzón en
`/tmp/docker-mailserver/postfix-accounts.cf`:

```
email@dominio|$hash|atributos
```

Leer ese archivo con `cat` es O(1) en procesos: una sola ejecución dentro del contenedor.
El provider toma únicamente la primera columna y descarta hash y atributos.

`MAIL_ACCOUNTS_SOURCE`:

| Valor | Comportamiento |
|---|---|
| `auto` (default) | `cat` del archivo; si falla, cae a `setup email list` |
| `file` | solo `cat`; un archivo ausente es error |
| `setup` | solo `setup email list` (necesario con `ACCOUNT_PROVISIONER=LDAP`) |

Verdad falsable: **con `auto` y el archivo presente, `list_accounts()` ejecuta un solo
comando (`cat`) y no ejecuta `setup email list`.**

## SCOPE

- `plugins/mail/backend/provider.py`: `parse_accounts_file`, `parse_setup_email_list`,
  `accounts_source`, fallback.
- `settings.py`, `router.py`, `.env.docker.example`, `README.md`.
- Tests del provider.

No entra:

- Cambiar el formato de respuesta, el cache, el SWR ni el transporte SSH.
- Escribir cuentas por archivo: las altas y los cambios de contraseña siguen usando
  `setup email add/update`, que es la API de DMS.
- Leer alias, cuotas, contraseñas o atributos. Solo la columna del email.
- Migrar el plugin a otra versión de DMS.

## CONTRACT

**Precondiciones**

- `ACCOUNT_PROVISIONER=FILE` (default de DMS) para que el archivo sea la verdad.
- Lectura permitida dentro del contenedor (el provider ya ejecuta `docker exec`).

**Postcondiciones**

- `list_accounts()` devuelve exactamente los emails válidos, sin duplicados, sin hashes
  ni atributos.
- Con `auto`, si el archivo no se puede leer, la llamada sigue funcionando por el
  camino anterior.
- Con `setup`, el comportamiento es byte a byte el actual.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: La forma de la lista y del endpoint no cambia; solo cambia cómo se obtiene.
    proof: test_parse_accounts_file_* + test_response_contract_unchanged (0001).
  - id: I2
    statement: El hash y los atributos nunca salen del parser ni se registran.
    proof: parse_accounts_file solo agrega line.split("|", 1)[0]; tests con atributos.
  - id: I3
    statement: Un archivo ausente no rompe el endpoint con auto; degrada al comando viejo.
    proof: test_list_accounts_falls_back_when_the_file_is_unavailable.
  - id: I4
    statement: Con setup forzado no se toca el archivo; con file forzado no hay fallback.
    proof: test_explicit_setup_source_never_reads_the_file, test_explicit_file_source_does_not_fall_back.
  - id: I5
    statement: Los comentarios, líneas vacías y duplicados no producen buzones fantasma.
    proof: test_parse_accounts_file_ignores_comments_hashes_and_duplicates.
```

## VERIFICATION

```text
pytest plugins/mail/backend/tests -q      -> 115 passed
ruff check (archivos tocados)            -> All checks passed
```

Tests nuevos en `test_provider.py`:

| Test | Invariante |
|---|---|
| `test_parse_accounts_file_ignores_comments_hashes_and_duplicates` | I2, I5 |
| `test_parse_accounts_file_accepts_extra_pipes_without_leaking_them` | I2 |
| `test_parse_setup_email_list_keeps_the_existing_contract` | I1 |
| `test_list_accounts_reads_only_the_accounts_file` | verdad nueva |
| `test_list_accounts_falls_back_when_the_file_is_unavailable` | I3 |
| `test_explicit_setup_source_never_reads_the_file` | I4 |
| `test_explicit_file_source_does_not_fall_back` | I4 |
| `test_default_accounts_source_is_auto` | default seguro |

**No verificado acá**: tiempo real de `setup email list` contra el DMS de producción. La
mejora se apoya en el código de DMS (O(N) subprocesos) y en que el camino nuevo ejecuta un
solo `cat`; la magnitud exacta se mide en el host comparando ambos comandos.

## ROLLBACK

```bash
echo 'MAIL_ACCOUNTS_SOURCE=setup' >> .env.docker
docker compose up -d app
```

Sin migración, sin estado, sin cambio de contrato. `git revert` también es seguro.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/mail/backend/provider.py
    - vendor/systutor-core/plugins/mail/backend/settings.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_provider.py
    - vendor/systutor-core/plugins/mail/README.md
    - .env.docker.example
    - aspecs/0039-fast-mail-account-listing.md
  prohibited:
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/frontend/**
    - vendor/systutor-core/src/**
    - docker-compose.yml
```

## Traceability

- Requirement: la lista de buzones no debe costar O(N) subprocesos dentro de DMS.
- owner / approver: pendientes de asignar antes de integrar.
- Commit: pendiente.
- Deployment: rebuild de la imagen. El default `auto` no requiere variables nuevas.
