# A.SPEC 0029 — Transporte Docker automático para dominios (sin mount ni SSH)

> `risk: high` — cambia cómo el plugin escribe la configuración que consume el
> Traefik real. Un destino mal detectado podría escribir el archivo en el
> contenedor equivocado. Exige `approver:` humano.
>
> Numeración: **0029**. La 0027 entregó el plugin de dominios con transporte
> local/SSH; la 0028 es TLS de correo. El contrato genérico multi-integración
> pasa a **0030**. Esta A.SPEC extiende el transporte de 0027, no cambia su API
> ni su UI.

## WHY

La 0027 dejó dos formas de escribir el YAML del proxy:

- **local**, cuando el directorio dinámico está montado en el proceso;
- **SSH**, cuando hay un host al que conectarse.

En producción ninguna de las dos está disponible:

- `vmi3461574` corre Traefik en el contenedor `traefik`, con
  `/opt/stacks/traefik/config` montado en `/config` y `providers.file.watch=true`.
- La app `spanel-app-1` monta **solo** `/var/run/docker.sock`; no ve ese
  directorio. Corre como root y comparte la red `proxy` con Traefik.
- Sin mount, el plugin responde `503 domains.proxy_unavailable`. Sin SSH
  configurado, tampoco puede usar la otra vía.

Agregar el mount a mano resuelve un servidor, no el problema: cada instalación
nueva requiere editar el compose, recrear el contenedor y conocer la ruta del
host. La app ya tiene el socket de Docker y root: puede pedirle al **contenedor
de Traefik** que escriba en su propio bind mount, sin ver el filesystem del host
y sin SSH.

## WHAT

Agregar un transporte **docker** y detección automática de entorno. Cuando el
plugin corre junto a un Docker accesible:

1. Encuentra el contenedor de Traefik.
2. Descubre el directorio del file provider desde sus mounts/config estática.
3. Lee, lista, escribe y borra los `spanel-*.yml` **dentro** de ese contenedor
   con `docker exec` (el archivo cae en el bind mount del host).
4. Sonda el certificado contra el contenedor por la red compartida.
5. Elige perfil y resolver sin configuración manual.

`SPANEL_PROXY_TRANSPORT=auto` (default) hace esa detección; `local`, `ssh` y
`docker` fuerzan un modo. La API, los permisos, la UI y las reglas de propiedad
de 0027 no cambian.

La verdad falsable: **en un servidor con Docker y sin mount ni SSH, el alta y la
baja de un dominio extra funcionan, y ningún archivo se escribe fuera del
directorio del file provider de Traefik.**

## SCOPE

Entra:

- Nuevo módulo de detección (`proxy/detect.py`): Docker, contenedor Traefik,
  directorio dinámico, red de sondeo, perfil y resolver.
- Nuevo transporte de archivos por `docker exec`: exists, read, list, write
  atómico, delete y writable.
- `Settings` con `transport` y los valores detectados; overrides por entorno.
- `ProxyOperations` usando una abstracción de archivos en vez de `Path` directo,
  sin cambiar su comportamiento observable.
- `doctor` informando qué transporte eligió y por qué.
- Pruebas unitarias con runner falso y una prueba live en un entorno
  **tipo producción** (app sin mount, Traefik con bind mount, solo socket).
- Actualización de `0027` para que el contrato genérico pase a 0030.

No entra:

- Cambiar el compose, Traefik, sus mounts o su configuración estática.
- Crear, recrear o reiniciar contenedores; solo `docker exec` y lecturas.
- SSH nuevo, túneles, agentes, montajes on-the-fly.
- Detección de DNS público, emisión de certificados por otra vía o wildcards.
- Cambios de API, permisos, UI, correo, hosting o kernel.
- Soportar un Traefik sin shell en la imagen (se detecta y se informa el GAP).

## T0 — LÍNEA BASE MEDIDA (2026-10-25, producción `vmi3461574`)

| Dato | Valor medido |
|---|---|
| Contenedor Traefik | `traefik`, imagen `traefik:latest` |
| Provider file | `directory: /config`, `watch: true` (`/opt/stacks/traefik/traefik.yml`) |
| Mount del host | `/opt/stacks/traefik/config` → `/config` (rw) |
| Resolver | `letsencrypt` (HTTP-01); existe `letsencrypt-staging` |
| Redes de Traefik | `proxy` |
| App | contenedor `spanel-app-1`, imagen `spanel-app`, servicio `app` |
| Usuario de la app | `uid=0(root)` |
| Mounts de la app | solo `/var/run/docker.sock` (rw) |
| Redes de la app | `spanel_default`, `proxy` |
| Variables del plugin | ninguna (`SPANEL_PROXY_*` ausentes) |
| Shell en Traefik | `docker exec traefik sh -c ...` funciona |
| API de Traefik | dashboard por router con básica; no escucha en 8080 |

En el laboratorio (ThinkPad) el mismo esquema existe con
`spanel-traefik-lab`, `/config` y resolver `internal`, lo que permite simular
producción sin tocar producción.

## CONTRACT

### 1. Selección de transporte

`SPANEL_PROXY_TRANSPORT` con valores `auto` (default), `local`, `ssh`, `docker`.

Orden en `auto`:

1. Si `SPANEL_PROXY_SSH_HOST` está definido → `ssh` (compatibilidad con 0027:
   quien ya configuró SSH no cambia de comportamiento).
2. Si hay Docker accesible y se detecta un Traefik con directorio de file
   provider escribible → `docker`.
3. Si `SPANEL_PROXY_CONFIG_DIR` existe y es escribible → `local`.
4. Si nada aplica → error `503 domains.proxy_unavailable` con el detalle de cada
   comprobación.

Un modo explícito que falle **no** cae a otro en silencio: responde el error del
modo pedido. La selección se registra una vez por operación en el log del
servidor (sin secretos) y `doctor` la muestra.

### 2. Detección del contenedor Traefik

1. `SPANEL_PROXY_TRAEFIK_CONTAINER` si está definido.
2. `docker ps --format '{{.Names}}\t{{.Image}}'` y filtrar imagen cuyo nombre
   contenga `traefik`.
3. Si hay exactamente uno, usarlo. Si hay varios, preferir `traefik` y luego
   `spanel-traefik-lab`. Si sigue ambiguo → error con la lista y la instrucción
   de definir la variable.
4. Verificar `docker inspect` y estado `running`; si no, error.

Nunca se usa el nombre del contenedor de un sitio ni un valor del cliente.

### 3. Detección del directorio dinámico

1. Leer la config estática: intentar `docker exec <traefik> cat /traefik.yml`
   (y `/etc/traefik/traefik.yml` como fallback), parsear
   `providers.file.directory`; si no, buscar `--providers.file.directory=` en
   `docker inspect .Args`; default `/config`.
2. `docker inspect <traefik> --format '{{json .Mounts}}'` y quedarse con el
   mount cuyo `Destination` sea ese directorio (normalizado sin `/` final).
3. Exigir que `docker exec <traefik> test -w <destination>` pase. Si el mount es
   `ro` o no existe → no es un destino válido.
4. Guardar solo el `Destination` (ruta dentro de Traefik). El `Source` del host
   no se usa y no se expone.

Si hay más de un mount candidato o ninguno, error accionable: “definí
`SPANEL_PROXY_CONFIG_DIR` con la ruta dentro de Traefik” (override explícito).

### 4. Operaciones de archivos por `docker exec`

Todas las rutas se construyen como `<destination>/spanel-<slug>.yml`, pasan por
`validate_managed_path` y se citan con `shlex.quote`. El runner recibe argv, no
una línea de shell armada con datos del usuario.

| Operación | Comando |
|---|---|
| listar | `docker exec <c> sh -c 'ls -1 <dir>/spanel-*.yml 2>/dev/null \|\| true'` |
| existe | `docker exec <c> test -e <ruta>` |
| leer | `docker exec <c> cat <ruta>` |
| escribir | `docker exec -i <c> sh -c 'umask 022 && cat > <tmp> && chmod 0644 <tmp> && mv <tmp> <ruta>'` con el contenido por stdin |
| borrar | `docker exec <c> rm -f -- <ruta>` |
| escribible | `docker exec <c> test -w <dir>` |

- `<tmp>` vive en el mismo directorio (`.<archivo>.tmp.<pid>`) y se renombra con
  `mv`, que es atómico dentro del mismo filesystem.
- Después de escribir se **relee** el archivo y se compara el contenido. Si no
  coincide, se informa error y no se audita como éxito.
- El texto no viaja por argv ni por logs.
- Si `docker exec` devuelve fallo de transporte, se propaga como
  `domains.proxy_unavailable`; no se intenta otro contenedor.

### 5. Sondeo del certificado

- Si el proceso corre en un contenedor y comparte al menos una red con Traefik,
  el host de sondeo es el **nombre del contenedor Traefik** y el puerto `443`
  interno.
- Si no comparte red, se usa `SPANEL_PROXY_PROBE_HOST`; si no está definido, se
  usa el puerto publicado de Traefik en el host (`docker port <c> 443/tcp`).
- Si no hay forma de alcanzarlo, el estado del certificado es `pending_cert` con
  el motivo; **nunca** `verify=False` para reportar `active`.
- La identificación del contenedor propio usa `HOSTNAME` (ID corto) y
  `docker inspect <hostname>`; si falla, se asume fuera de Docker y se usa el
  puerto publicado.

### 6. Perfil y resolver

- `SPANEL_PROXY_CERT_RESOLVER` gana siempre.
- Si no: resolver `internal` cuando el perfil sea `internal`; `letsencrypt` en
  `public`.
- Perfil: `SPANEL_DOMAINS_PROFILE` / `SPANEL_PROXY_PROFILE` si están; si no, se
  leen los `certificatesResolvers` de la config estática: si existe `internal`
  y no existe `letsencrypt` → `internal`; en cualquier otro caso → `public`.
- El perfil solo afecta validación y puerto de sondeo; no emite certificados.

### 7. Overrides y errores

| Variable | Efecto |
|---|---|
| `SPANEL_PROXY_TRANSPORT` | fuerza `auto`/`local`/`ssh`/`docker` |
| `SPANEL_PROXY_TRAEFIK_CONTAINER` | nombre exacto del contenedor |
| `SPANEL_PROXY_CONFIG_DIR` | directorio **dentro de Traefik** (override de detección) |
| `SPANEL_PROXY_PROBE_HOST` | host de sondeo |
| `SPANEL_PROXY_PROBE_TIMEOUT` | segundos por sondeo |
| `SPANEL_PROXY_SSH_*` | sigue funcionando como en 0027 |

Códigos:

- `domains.proxy_unavailable` (503): no hay transporte viable, Docker no
  responde, contenedor ambiguo, directorio no escribible o exec falla.
- El mensaje incluye qué se comprobó y qué variable define el override. Nunca
  incluye credenciales.

### 8. Compatibilidad

- `local` y `ssh` conservan exactamente el comportamiento de 0027 (pruebas
  existentes sin cambios de expectativas).
- `doctor` sigue pasando en el laboratorio; agrega una línea
  `transporte: docker|local|ssh (motivo)`.
- El CLI `scripts/proxy_domain.py` usa el mismo `Settings.from_env` y por lo
  tanto detecta igual cuando corre en el host.
- Sin cambios de schema, migración, API, permisos, eventos ni UI.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Solo se escriben archivos spanel-<slug>.yml con el marcador de propiedad.
    proof: validate_managed_path + verificación del marcador antes de escribir; pruebas con nombres ajenos y contenido no gestionado.
  - id: I2
    statement: Ninguna escritura cae fuera del directorio del file provider detectado.
    proof: El path se compone con el Destination del mount; se rechazan rutas absolutas o con ..; prueba con Destination manipulado y con nombre inválido.
  - id: I3
    statement: El transporte nunca cambia de contenedor ni de host a mitad de una operación.
    proof: La detección se resuelve una vez por operación y se pasa inmutable a las lecturas/escrituras; prueba con dos Traefik y con fallo de exec.
  - id: I4
    statement: Una escritura fallida o parcial no deja el YAML anterior roto.
    proof: Temporal en el mismo directorio + mv; relectura y comparación; prueba con exec que falla en el mv y con contenido distinto al releer.
  - id: I5
    statement: No se reinicia, recrea ni detiene Traefik ni ningún sitio.
    proof: Solo comandos exec/cat/ls/mv/rm; prueba que el runner no recibe run/restart/stop/rm de contenedores; StartedAt sin cambios en la prueba live.
  - id: I6
    statement: Los contratos de 0027 no cambian para el cliente.
    proof: Las pruebas de API/UI y el esquema de respuestas pasan sin ediciones; mismo conjunto de rutas y permisos.
  - id: I7
    statement: Un fallo de detección no degrada a un transporte silencioso.
    proof: auto documenta el orden; modo explícito falla con su error; pruebas de ambigüedad y de Docker caído.
  - id: I8
    statement: No se filtran rutas del host, credenciales ni contenido YAML en logs/auditoría.
    proof: Auditar transporte y motivo, nunca Source ni SSHPASS; pruebas de error.
  - id: I9
    statement: Deshabilitar el plugin deja el ruteo intacto.
    proof: Sin escrituras al deshabilitar; los YAML y routers permanecen.
```

## VERIFICATION

### V0 — Gates

```bash
cd vendor/systutor-core && python3 -m pytest plugins/domains/backend/tests -q
cd /data/data/com.termux/files/home/Proyectos/mailadmin && \
  python3 -m pytest scripts/tests/test_domains_api.py scripts/tests/test_proxy_domain.py -q
cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

### V1 — Detección con runner falso

- Docker ausente + SSH definido → `ssh`.
- Docker ausente, sin SSH, config dir escribible → `local`.
- Nada → error con las tres comprobaciones.
- Un Traefik `traefik:latest`, mount `/config`, `test -w` OK → `docker`.
- Dos contenedores Traefik → error de ambigüedad; con override → gana el
  override.
- Mount `ro` o `test -w` falla → no elige docker.
- Config estática con `providers.file.directory: /etc/traefik/dynamic` → usa ese
  Destination, no `/config`.
- `--providers.file.directory` en Args cuando el archivo estático no se puede
  leer.
- Red compartida → probe host = nombre del contenedor y puerto 443; sin red
  compartida → puerto publicado.
- Perfil: `internal` solo, `letsencrypt`, ninguno.

### V2 — Operaciones exec

- `list` con 0, 1 y varios archivos; ignora archivos ajenos.
- `write` arma argv exacto (`docker exec -i`, `sh -c`, temporal, `mv`), pasa el
  contenido por stdin y no por argv.
- Relectura distinta → error y sin éxito.
- `delete` y `exists` con nombres gestionados y no gestionados.
- Fallo de exec → `ProxyError` de transporte, sin reintento en otro contenedor.

### V3 — Regresión de 0027

- Los 218 tests actuales pasan sin cambios.
- `scripts/tests/test_proxy_domain.py` pasa con `transport=local`.
- `test_domains_api.py` y `test_domains_api_live.py` pasan.

### V4 — Live tipo producción (ThinkPad)

Escenario simulado, sin tocar producción:

1. Traefik de laboratorio con `traefik.yml` y `./config` montados, red `proxy`,
   resolver `internal`.
2. Contenedor de app con **solo** `docker.sock`, sin mount del config y sin
   variables `SPANEL_PROXY_*` (salvo el perfil/permitidas).
3. Ejecutar la suite live de dominios. Debe elegir `docker`, escribir el YAML,
   habilitar el router y emitir el certificado.
4. Verificar que dentro del contenedor de la app **no existe** el archivo ni el
   directorio del host; que el archivo está en el bind mount de Traefik; que
   `docker inspect` de la app no ganó mounts nuevos.
5. Quitar el extra y confirmar que el router desaparece y el primario sigue.

La prueba vive en `scripts/tests/test_proxy_transport_live.py`, opt-in con
`SPANEL_LIVE_TESTS=1`, y solo toca `spanel-lab-*`.

### V5 — Producción

Después de desplegar la imagen:

```bash
docker exec spanel-app-1 python -c "from plugins.domains.backend.proxy import doctor_settings; print(doctor_settings())"
```

Debe informar `transporte: docker` y el contenedor `traefik`. El alta real de un
dominio queda para cuando haya un FQDN delegado; no se prueba con dominios
inventados porque Let's Encrypt no los va a emitir.

## ROLLBACK

- `SPANEL_PROXY_TRANSPORT=ssh` o `local` restaura el comportamiento de 0027 sin
  redesplegar código.
- Deshabilitar `domains` deja de escribir y retira UI/API; los YAML quedan.
- No hay migración ni cambio de schema: revertir el commit vuelve al código de
  0027.
- Los archivos escritos por el transporte docker son los mismos `spanel-*.yml`
  en el mismo bind mount; el rollback no necesita tocar contenedores.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/domains/backend/proxy/detect.py        # nuevo
    - vendor/systutor-core/plugins/domains/backend/proxy/files.py         # nuevo
    - vendor/systutor-core/plugins/domains/backend/proxy/config.py
    - vendor/systutor-core/plugins/domains/backend/proxy/transport.py
    - vendor/systutor-core/plugins/domains/backend/proxy/operations.py
    - vendor/systutor-core/plugins/domains/backend/proxy/doctor.py
    - vendor/systutor-core/plugins/domains/backend/settings.py
    - vendor/systutor-core/plugins/domains/backend/service.py             # solo cableado
    - vendor/systutor-core/plugins/domains/backend/tests/**
    - vendor/systutor-core/plugins/domains/README.md
    - scripts/tests/test_proxy_transport_live.py                          # nuevo
    - scripts/tests/test_domains_api_live.py                              # entorno simulado
    - aspecs/0029-proxy-docker-transport.md
    - aspecs/0027-domains-plugin.md                                       # renumerar 0030
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/contracts/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/domains/frontend/**
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - docker-compose.yml
    - /opt/stacks/traefik/**
    - scripts/traefik-lab.sh
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - seleccion de transporte del plugin domains
    - operaciones de lectura/escritura de los spanel-*.yml
    - doctor del proxy
  indirect:
    - CLI scripts/proxy_domain.py
    - pruebas live de dominios
  must_not_affect:
    - API, permisos, eventos y UI de domains
    - hosting, mail, tenant y kernel
    - contenedores Traefik y sitios: estado, StartedAt y configuracion
    - archivos de Traefik que no sean spanel-*.yml
    - laboratorio del ThinkPad y produccion durante el desarrollo
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0027
  must_compose_with:
    - plugin hosting (contrato de propiedad)
    - plugin mail (lectura de buzones en frontend)
  systemic_invariants:
    - un dueno por Host
    - deshabilitar un plugin no cambia el ruteo
    - aislamiento por tenant
  composition_checks:
    - pytest plugins/domains/backend/tests plugins/hosting/backend/tests plugins/mail/backend/tests
    - scripts/tests/test_domains_api.py
    - scripts/tests/test_proxy_transport_live.py (opt-in)
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: deteccion pura y testeable separada del transporte y de las operaciones
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - plugins/domains/backend/proxy/detect.py
    - plugins/domains/backend/proxy/files.py
```

## Traceability

- Requirement: operar dominios extra en un servidor con Docker y sin mount ni
  SSH, con detección automática y sin tocar el compose.
- owner: luc444s
- approver:
- Commit:
- Deployment: ThinkPad simulado primero; luego `vmi3461574` con la imagen nueva
  y `SPANEL_PROXY_TRANSPORT=auto`.

## Definition of Done

- [ ] Objetivo: alta/baja funcionan en el escenario sin mount ni SSH.
- [ ] Alcance respetado: sin cambios de API, UI, compose ni Traefik.
- [ ] Contrato: detección, operaciones exec y sondeo implementados.
- [ ] Invariantes I1–I9 probados.
- [ ] V0–V4 ejecutados; V5 documentado.
- [ ] Rollback probado con `transport=local` y `ssh`.
- [ ] Sin archivos fuera de `change_surface`.
- [ ] Nada >600 líneas; ideal <400.
- [ ] Trazabilidad completa.

## FUERA DE ALCANCE

- Montar directorios, cambiar el compose de producción o recrear la app.
- Instalar o configurar Traefik, sus plugins o su shell.
- Soportar imágenes de Traefik sin `sh`.
- Automatizar DNS o certificados fuera de los resolvers existentes.
- Cambiar la UI para mostrar el transporte detectado.
