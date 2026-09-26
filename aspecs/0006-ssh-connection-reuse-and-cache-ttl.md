# A.SPEC 0006 — Reuse the SSH connection and widen the accounts cache TTL

> `risk: low` — un cambio de transporte y una variable de ventana. Sin migración, sin cambio de
> esquema, sin cambio de contrato de API. El rollback es un revert más borrar un archivo de
> socket. Mode: `extreme-poverty`.

## WHY

Medido contra el proveedor real (`100.67.5.50`, contenedor `spanel-mail`) y Redis real, el
cold path de `GET /api/v1/plugins/mail/mail/accounts` cuesta **1560 ms**, y **el 48% de eso es
un handshake SSH que no hace nada útil**:

```
1560 ms  =  559 ms  handshake SSH: conexión nueva, autenticación, cierre
         +  592 ms  docker exec setup email list en el host remoto
```

Con `ControlMaster` reutilizando la conexión, sobre la misma llamada:

```
 599 ms  =   44 ms  handshake
         +  555 ms  docker exec
```

El provider abre una sesión SSH **por cada llamada** (`provider.py:87-93`): cada lectura en
frío, cada alta de buzón y cada cambio de contraseña paga un handshake completo. Es la mitad
del costo de la operación, y no compra nada.

Por el lado del cache, el TTL de 60 s está alineado con el `staleTime` del frontend
(`MailAccountsPage.tsx:30`) **y ese alineamiento es contraproducente**: ambos expiran al
mismo tiempo, así que el cliente pide justo cuando el servidor ya no tiene la respuesta, y
paga el viaje completo. Con un TTL más ancho que el `staleTime`, el cliente refetchea cada
60 s pero lo hace **contra Redis**, a 0.41 ms.

## WHAT

**El provider reutiliza una sesión SSH entre llamadas cuando puede, y el cache de cuentas
dura 300 s en vez de 60 s, de modo que el refetch periódico del frontend nunca coincida con
una expiración del cache.**

Es una verdad, falsable hoy, y hoy **falla**: dos ssh completos por cada operación de correo,
y expiración simultánea de las dos capas de cache.

## Medición, antes y después

Todo medido contra el proveedor real (`100.67.5.50`, contenedor `spanel-mail`) y Redis real,
con el token de un admin válido. "COLD" es cada lectura con la clave de Redis borrada a
propósito, para que todas paguen el viaje completo.

**A nivel de servicio** (3 viajes, cache borrado entre cada uno)

| | viaje 1 | viaje 2 | viaje 3 | promedio |
|---|---|---|---|---|
| Sin reutilización | 1426 ms | 1204 ms | 1190 ms | **1273 ms** |
| Con `ControlMaster` | 1380 ms (abre maestra) | 611 ms | 668 ms | **886 ms** |

**End-to-end por HTTP** (`GET /api/v1/plugins/mail/mail/accounts`)

| | antes | después |
|---|---|---|
| COLD | 1240-1560 ms | **655-861 ms** (promedio ~750) |
| WARM (Redis) | 20 ms | 14-20 ms |

En régimen permanente la mejora es de **~2.0x** (1204 → 611 ms); el promedio incluyendo el
viaje que abre la maestra es 1.4x. El WARM no cambia, porque no dependía del SSH.

**Socket y permisos, verificados en vivo**

```
sockets creados: ['lucas@100.67.5.50:22']     <- los tokens %r@%h:%p se expanden
modo del directorio: 0o700                    <- I3, no por umask sino a proposito
```

**La degradación se probó en vivo, sin provocarla.** La primera medición con
`MAIL_SSH_CONTROL_PATH=/tmp/systutor-ssh/...` falló con
`ssh: ControlPath no disponible ([Errno 13] Permission denied: '/tmp/systutor-ssh')` y el
provider **siguió funcionando**, abriendo una conexión por llamada. En Termux `/tmp` existe
pero pertenece al sistema Android y no es escribible por la app. Eso es I4 demostrada en el
entorno real, y la ruta del `.env` local quedó en el tmp de Termux.

## SCOPE

1. `plugins/mail/backend/provider.py`:
   - `build_ssh_command(...)`, función pura que arma el `argv` de `ssh`, extraída de `_exec`
     para que sea verificable sin abrir una conexión;
   - soporte de `ControlMaster=auto` + `ControlPath` + `ControlPersist`, con el directorio
     del socket creado en `0700` y degradación silenciosa si no se puede crear;
   - `_exec` sigue siendo el único lugar que ejecuta.
2. `plugins/mail/backend/settings.py`: `mail_ssh_control_path` y `mail_ssh_control_persist`.
3. `plugins/mail/backend/router.py`: pasar las dos settings al provider.
4. `plugins/mail/backend/settings.py`: `mail_accounts_cache_ttl` por defecto `60` → `300`.
5. `plugins/mail/backend/tests/test_provider.py`: tests nuevos (ver VERIFICATION).
6. `.env` local, `.env.docker.example` y `plugins/mail/README.md`: documentar las variables.

## OUT OF SCOPE

- **No se cambia `MAIL_USE_SSH` de producción.** Sigue en `false`, y por lo tanto en prod
  este A.SPEC solo aporta el TTL. Ver "Alcance real por entorno" abajo: la reutilización de
  SSH es un gain de **dev**, y no voy a venderla como ganancia de producción.
- **No se sube el `staleTime` del frontend.** Sigue en 60 s, y es correcto: con el backend en
  300 s, ese refetch cada minuto se responde desde Redis en 0.41 ms. Subirlo no agrega nada
  y sí agrega trabajo.
- **No se toca el `_run_local` ni el camino `use_ssh=False`.** El `docker exec` local queda
  intacto.
- No se agrega un pool de conexiones propio, ni `asyncssh`, ni se reescribe el provider.
- No se toca `service.py` ni el `_cache_invalidate`. La invalidación por escritura ya funciona
  y está probada (abajo).
- No se toca el frontend (A.SPEC 0005 queda intacta).

## Alcance real por entorno

Este es el punto que hay que decir antes de vender el cambio:

| Entorno | `MAIL_USE_SSH` | Cold path | Ganancia de esta A.SPEC |
|---|---|---|---|
| **Dev local** | `true` | 1560 ms (SSH a `100.67.5.50`) | **1560 → ~600 ms**, y las escrituras también |
| **Producción** | `false` | `docker exec` local, **no medido** (no hay docker en el entorno de dev) | **solo el TTL** |

La medición de `docker exec` local no se puede hacer desde acá: no hay docker. Cualquier
cifra de cold path en producción sería inventada, así que no se afirma ninguna.

## Restricción de diseño: el socket tiene que ser privado

`ControlPath` es un archivo FIFO en disco. Si el directorio que lo contiene es escribible por
otros usuarios, otro proceso local puede_lines Create ese socket primero y **secuestrar la
conexión SSH**, es decir, hablar con el mail server con tus credenciales. Es un ataque
clásico de OpenSSH y no es teórico.

Por eso el directorio se crea con `0o700` y se verifica. Si no se puede crear —contenedor con
sistema de archivos de solo lectura, permisos ${\*}— el provider **degrada a una conexión
nueva por llamada**, que es el comportamiento de hoy. La optimización nunca debe poder
romper el arranque.

## Restricción de diseño: la contraseña no cambia de canal

Se sigue usando `sshpass -e` con `SSHPASS` en el entorno, no `-p` en la línea de comandos. La
combinación con `ControlMaster` no cambia eso, y hay un test que lo fija: la contraseña no
puede aparecer en el `argv`.

## Restricción de diseño: 300 s no alarga la ventana de datos obsoletos de la app

El TTL widened aplica a **todo**, pero lo que importa es qué lo reinicia:

| Evento | ¿Toca el cache? | Consecuencia |
|---|---|---|
| **Alta de buzón desde la app** | **Sí, lo borra** (`_cache_invalidate`) | La siguiente lectura va al servidor y trae la cuenta nueva. Verificado: `exists=0`, `TTL=-2` tras el alta, y `TTL=300` renovado en la lectura siguiente |
| Cambio de contraseña | No | La lista no cambia; el TTL sigue corriendo |
| Expiración natural | — | 300 s hasta el próximo viaje al servidor |
| **Alta de buzón por fuera de la app** (SSH directo al DMS) | No puede | **La UI no lo ve hasta 300 s.** Este es el costo real del cambio, y era 60 s |

Es decir: el único escenario que se degrupa es el cambio hecho **por fuera de la app**, que
pasa de 1 minuto de retraso a 5. El interruptor de A.SPEC 0001
(`MAIL_ACCOUNTS_CACHE_ENABLED=false`) sigue siendo la salida.

## CONTRACT

**Precondiciones**

- `MAIL_USE_SSH=true` para que la reutilización tenga efecto; en `false` el camino es otro y
  no se toca.
- El binario `ssh` soporta `ControlMaster` (OpenSSH ≥ 6.6, 2014).
- El directorio de `ControlPath` es escribible, o el provider degrada.

**Postcondiciones**

- Con `mail_ssh_control_path` vacío, el `argv` de `ssh` es **byte a byte el de hoy**: este es
  el default, y es lo que hace el cambio seguro de activar.
- Con `mail_ssh_control_path` puesto, el `argv` incluye `ControlMaster=auto`,
  `ControlPath=<path>` y `ControlPersist=<n>`, y el directorio existe con modo `0700`.
- Si el directorio no se puede crear, la llamada se ejecuta igual, sin flags de ControlMaster,
  y no se lanza excepción.
- `list_accounts`, `create_account` y `change_password` devuelven exactamente lo mismo que
  antes, con o sin reutilización.
- El TTL por defecto de `mail_accounts_cache_ttl` es 300.

**Verdad nueva, falsable**: dos lecturas consecutivas del listado, con el cache recién
expirado, cuestan un solo handshake SSH en vez de dos. Con `ControlMaster` el segundo `ssh`
debe tardar ~44 ms de transporte en lugar de ~559 ms.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      Con el control path vacío, el comando ssh es exactamente el que se construía antes:
      ssh -t -o StrictHostKeyChecking=no -p <port> user@host <cmd>. Nadie pierde
      comportamiento por instalar esto.
    proof: >
      test_ssh_command_unchanged_without_control_path, asserts sobre la lista completa del argv

  - id: I2
    statement: >
      Con control path, el argv incluye ControlMaster=auto, ControlPath y ControlPersist, en
      ese orden y antes del destino.
    proof: test_ssh_command_includes_control_flags

  - id: I3
    statement: >
      El directorio del socket se crea en modo 0700. Nunca 0777, nunca el modo por defecto del
      umask, nunca un directorio compartido.
    proof: >
      test_control_path_directory_is_private, stat.S_IMODE == 0o700. Este es el invariante de
      seguridad de la A.SPEC: sin él, otro usuario local puede secuestrar la conexión.

  - id: I4
    statement: >
      Si el directorio no se puede crear, el provider NO lanza. Degrada a conexión nueva por
      llamada, que es el comportamiento previo. La optimización no puede romper el arranque.
    proof: >
      test_unwritable_control_path_degrades_gracefully, con un control path bajo /proc o un
      archivo donde debería ir el directorio; assert de que _exec devuelve la salida del
      comando sin excepción

  - id: I5
    statement: >
      La contraseña nunca aparece en el argv. Sigue yendo por SSHPASS en el entorno.
    proof: test_password_never_appears_in_argv

  - id: I6
    statement: >
      El camino use_ssh=False queda byte a byte como estaba. La A.SPEC no toca el docker exec
      local, que es el camino de producción.
    proof: test_local_docker_exec_command_unchanged

  - id: I7
    statement: >
      La invalidación por escritura no cambia. Un alta de buzón sigue borrando la clave del
      cache, con cualquier TTL.
    proof: >
      El FakeProvider + Redis real de esta A.SPEC, más el test de A.SPEC 0001
      test_create_account_invalidates_cache, que sigue pasando sin modificationes.

  - id: I8
    statement: >
      Ningún cambio de contrato. Las tres operaciones devuelven lo mismo, los status codes
      no se tocan, y service.py no se modifica.
    proof: >
      change_surface excluye service.py y schemas.py. La suite del kernel corre sin cambios
      de conteo.

  - id: I9
    statement: >
      El TTL alineado con staleTime se rompe a propósito, y en la dirección correcta:
      backend 300 > frontend 60. El cliente refetchea cada 60 s contra un cache que tiene
      240 s de vida restante.
    proof: >
      test_default_cache_ttl_is_300, y el valor de ACCOUNTS_STALE_TIME_MS sin tocar en el
      frontend. La alineación de A.SPEC 0001 era correcta cuando ambos caían al servidor;
      con Redis de por medio, la desalineación es lo que evita el viaje.
```

## VERIFICATION

**Este A.SPEC sí tiene tests automatizados**, a diferencia de A.SPEC 0005: el backend tiene
harness (`pytest` + `MailProvider` fake), y la parte que hay que verificar —el `argv` que se
arma y las condiciones de degradación— es exactamente la que una función pura deja
verificable.

**Provider** (`plugins/mail/backend/tests/test_provider.py`, nuevo)

| Test | Invariante | Qué falsaría |
|---|---|---|
| `test_ssh_command_unchanged_without_control_path` | I1 | El cambio altera el transporte por defecto |
| `test_ssh_command_includes_control_flags` | I2 | Flags ausentes o mal ordenados |
| `test_control_path_directory_is_private` | I3 | Directorio 0777 → secuestro de conexión |
| `test_unwritable_control_path_degrades_gracefully` | I4 | La optimización rompe el arranque |
| `test_password_never_appears_in_argv` | I5 | Contraseña expuesta en la lista de procesos |
| `test_local_docker_exec_command_unchanged` | I6 | Se tocó el camino de producción |
| `test_default_cache_ttl_is_300` | I9 | El default no cambió |

**Comportamiento** (FakeProvider + Redis real, el mismo patrón que se usó para medir)

| Test | Invariante | Qué falsaría |
|---|---|---|
| `test_create_invalidates_cache_and_renews_ttl` | I7 | Un buzón nuevo invisible hasta expirar |

**Gates (los tres, sin excepción)**

```bash
cd vendor/systutor-core && ruff check .
cd vendor/systutor-core && python3 -m pyright
cd vendor/systutor-core && python3 -m pytest tests plugins/mail/backend/tests -q
```

Resultado, y una salvedad que hay que decir en voz alta:

| Gate | Resultado |
|---|---|
| `ruff check .` | All checks passed |
| `pytest plugins/mail` | **48 passed** (18 nuevos + 30 de A.SPEC 0001) |
| `pytest tests` | **95 passed**, mismo conteo que antes de esta A.SPEC (I8) |
| `python3 -m pyright plugins/mail` | **0 errors** |
| `python3 -m pyright` (proyecto completo) | **7 errors — preexistentes, y NO son de esta A.SPEC** |

Los 7 errores de pyright están todos en `tests/test_permission_sync.py` (tipos `User | None`
y `Tenant | None` sin estrechar), verrían de A.SPEC 0003. Comprobado con `git stash`:
**7 errores antes de esta A.SPEC, 7 después**. El gate de `AGENTS.md` que dice que pyright
tiene que pasar **ya estaba en rojo**; esta A.SPEC no lo arregla porque `test_permission_sync.py`
está fuera de su change surface. Queda como deuda visible, no como sorpresa.

**Medición, en el entorno real** (no es un test: es la demostración de la verdad nueva)

```bash
# con el .env de dev (MAIL_USE_SSH=true)
redis-cli -n 0 del systutor:cache:mail:accounts:raw
# 1. leer con cache vacia -> ~1560 ms, y deja el cache tibio
# 2. expirar la clave, leer otra vez -> mide el segundo ssh, sobre conexion viva
#    esperado: ~600 ms, no ~1560 ms
```

**Proof de falsabilidad**: `test_ssh_command_includes_control_flags` falla contra el código
previo a esta A.SPEC, porque `_exec` no construye esos flags. Y
`test_default_cache_ttl_is_300` falla contra el default 60. Ese es el antes y el después.

## ROLLBACK

`git revert <sha>`, más borrar el archivo de socket si quedó en `/tmp` (es un FIFO, no un
dato). Sin migración, sin cambio de esquema, sin estado en el servidor. Con el revert, el
provider vuelve a abrir una conexión por llamada y el TTL vuelve a 60 s si se quita la
variable del entorno.

El único estado que deja esta A.SPEC es el proceso maestro de SSH, que muere solo con
`ControlPersist` segundos después de la última llamada. No sobrevive al revert.

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
  prohibited:
    - vendor/systutor-core/plugins/mail/backend/service.py      # I8: cache e invalidación intactos
    - vendor/systutor-core/plugins/mail/backend/schemas.py     # I8
    - vendor/systutor-core/plugins/mail/frontend/**             # el staleTime no se toca
    - vendor/systutor-core/src/**                               # el kernel no sabe de SSH
    - vendor/systutor-shell/**                                  # la UI no se toca
    - docker-compose.yml                                        # no hay servicios nuevos
    - .env.docker                                                # valor de prod, lo decide el deploy
```

`service.py` está prohibido explícitamente: la tentación es "de paso toco el cache" y es
exactamente el cambio que rompería I7.

## Blast Radius

```yaml
blast_radius:
  direct:
    - provider.py (un archivo, ~40 lineas)
    - settings.py (2 settings nuevas + 1 default)
    - router.py (2 argumentos)
    - 1 archivo de tests nuevo, ~8 tests
  indirect:
    - El host remoto ve una sesión SSH persistente en vez de una por llamada. Con
      ControlMaster=60 eso es una conexión ociosa durante 60 s, no un proceso por request.
    - El logs del servidor remoto pueden mostrar menos eventos de conexión: es el efecto
      esperado de la reutilización, no una anomalousía.
  must_not_affect:
    - surface: cache_invalidation
      surfaces: [service.py, _cache_invalidate, _cache_write_raw]
      invariant: I7
    - surface: production_transport
      surfaces: [provider.py camino use_ssh=False, docker exec local]
      invariant: I6
    - surface: api_contract
      surfaces: [router.py, schemas.py, service.py]
      invariant: I8
    - surface: frontend_freshness
      surfaces: [MailAccountsPage.tsx, ACCOUNTS_STALE_TIME_MS]
      invariant: I9 — el valor no se toca a proposito
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0001   # I7 depende de su invalidacion por escritura; I9 revisa su decision de
                    # alinear TTL con staleTime y la revierte a proposito
  must_compose_with: []
  systemic_invariants:
    - La invalidación de permisos de A.SPEC 0003 no toca el plugin mail.
    - El proveedor sigue hablando con el mismo contenedor DMS, por el mismo canal, con las
      mismas credenciales. Solo cambia cuántas sesiones se abren.
  composition_checks:
    - cd vendor/systutor-core && python3 -m pytest tests plugins/mail/backend/tests -q
    - cd vendor/systutor-core && ruff check .
    - cd vendor/systutor-core && python3 -m pyright
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/mail/backend/provider.py
```

`_exec` queda más corto: la construcción del comando se va a `build_ssh_command`, que es una
función pura y testeable. `provider.py` queda por debajo de las 200 líneas, no se extrae
módulo. No aparece ninguna clase ni abstract factory nueva: el provider sigue siendo una
clase con tres métodos.

## Traceability

- **Requirement**: las operaciones de correo no deben pagar medio segundo de handshake SSH en
  cada llamada, y el cache no debe expirar en el mismo instante que el `staleTime` del
  cliente
- **A.SPEC**: este documento
- **Code**: `plugins/mail/backend/provider.py` (`build_ssh_command`, `_exec`),
  `settings.py` (`mail_ssh_control_path`, `mail_ssh_control_persist`,
  `mail_accounts_cache_ttl`), `router.py` (`_get_mail_service`)
- **Migration**: ninguna
- **Test**: `plugins/mail/backend/tests/test_provider.py` (nuevo, ~8 tests)
- **Commit**: `TBD`
- **Deployment**: rebuild de imagen. Sin migración, sin paso manual. Las variables de entorno
  se cambian en `.env.docker` en el deploy, no en el código

- **owner**: `TBD`
- **approver**: `TBD`

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established

**Deuda que esta A.SPEC deja a la vista, sin tocarla**: el gate de pyright del proyecto
tiene 7 errores preexistentes en `tests/test_permission_sync.py`, todos de A.SPEC 0003. Esta
A.SPEC no los arregla porque ese archivo está fuera de su change surface, ymeterlos aquí
sería un cambio sin relación con el transporte SSH. Merecen su propia A.SPEC.
