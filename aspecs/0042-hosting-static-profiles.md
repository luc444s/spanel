# A.SPEC 0042 — Static profiles managed by the platform admin

> `risk: high` — crea contenedores con imágenes configuradas por el admin y
> decide cómo se publica cada estático. Una imagen o health check mal definido
> provoca builds eternos, sitios muertos o rutas publicadas con el contenido
> equivocado. Exige `approver:` humano.
>
> Generaliza la A.SPEC 0031: el estático deja de tener grabados en piedra la
> imagen (`nginx:alpine`), el web root y el marcador de health check, y pasa a
> ser parametrizado por **perfiles** que el admin de la plataforma administra.
> Sin Dockerfile del tenant, sin imagen elegida por el tenant.

## Lab evidence — verificación previa en el laboratorio (2026-11-06)

La idea de esta spec se verificó de punta a punta en el laboratorio real
(ThinkPad, Traefik del lab + CA ACME local) con un CLI determinista:
`scripts/spanel_profiles/` (entrada `scripts/spanel-profiles.py`), que reutiliza
el transporte y las convenciones de `scripts/spanel_static`. Cuatro perfiles
reales, mismos checks, mismo dominio de marcadores:

| Perfil | Imagen | Web root | Contenido |
|---|---|---|---|
| `plain` | nginx:alpine | /usr/share/nginx/html | HTML/CSS/JS seed de 0031 |
| `react` | caddy:2-alpine | /usr/share/caddy | SPA React 18 (CDN) |
| `boot` | httpd:alpine | /usr/local/apache2/htdocs | Landing Bootstrap 5 |
| `astro` | node:24-alpine + npm | /site | Astro 7 build + preview :80 |

`verify` = 6 checks por perfil (container running, imagen correcta, labels de
propiedad, `spanel.profile`, marker interno por 127.0.0.1:80, HTTPS por
Traefik con el marker) — **24/24 PASS**.

Hallazgos que la spec incorpora (y que el prototipo ya resolvió):

1. **Un contenedor recién creado puede fallar los primeros `docker exec`**
   (`No such container` / `is restarting`): el provisioner debe esperar
   `running` antes de sembrar y mantener la política de reintentos corta.
2. **El CMD del perfil puede arrancar antes de que el seed esté completo**
   (Astro compila justo tras crear el contenedor). Imperativo del contrato:
   los profiles con `command` esperan el ÚLTIMO archivo del seed (`wait_file`)
   antes de ejecutar `npm install && build && preview` — sin esto, el build
   usa configuración viejo y el resultado no es determinista.
3. **Astro 4.16 no pasa `allowedHosts` a Vite preview** (el file provider
   sirve por `Host(<dominio>)` y Vite bloquea): `astro.config.mjs` del seed
   declara `vite.preview.allowedHosts: [<dominio>]`. El perfil `astro` fue
   actualizado a Astro 7, donde el wiring existe; el panel no depende del
   mismo fix para perfiles que el admin cree.
4. **El marcador propio del perfil es la clave del health check**:
   `http_200` no alcanza cuando el perfil sirve contenido desconocido. El seed
   escribirá `data-spanel-profile="<id>"` en el HTML que el perfil sirve, y
   `--force` recrea contenedor y volumen para que seed → config → build queden
   coherentes en un solo arranque.
5. **La ruta es idempotente y va separada del health interno**: `up` asegura
   la ruta SIEMPRE (aunque el contenedor ya esté healthy) y la valida por HTTPS
   (200 + marker) antes de declarar UP. Y en el lab hay que refrescar el
   `dns/hosts` cuando cambia la IP de los contenedores: el fix ya está en
   `scripts/traefik-lab.sh` (la CA se recrea si su `--dns` quedó fijo a una IP
   vieja del dnsmasq).
6. El flujo del lab es idempotente y sin estado: labels `com.spanel.lab=true`,
   red propia del lab y `down` solo toca recursos de los perfiles (contenedor,
   volumen y su ruta), nunca el Traefik real ni `/opt/stacks`.

Estos puntos ya están en el prototipo; la implementación de esta spec los
traslada a `static.py` + `profiles.py` del plugin, no hay que re-descubrirlos.

## Próxima etapa — implementación en el plugin (M1–M4)

El prototipo se queda como verificación; la etapa siguiente convierte este
comportamiento en el plugin `hosting`, en este orden:

- **M1 — Modelo + migración `003`**: tabla `hosting_static_profiles` + columna
  `profile_id` + fila builtin `nginx` (backfill de los estáticos existentes) +
  up/down/up en SQLite (`test_static_profiles_migration.py`).
- **M2 — Backend**: `profiles.py` (validación + resolución), `static.py`
  parametrizado (imagen/web_root/env/command/wait_file/marker), CRUD del
  superadmin (`router.py` + `hosting.profiles.manage`), guard de uso
  (`hosting_profile_in_use`, `hosting_profile_builtin`),
  `create_site(..., profile=...)` con `404 hosting_profile_unknown`.
- **M3 — Frontend**: selector de perfil en "Crear sitio" (`GET
  /hosting/static-profiles` llena el select) + gestor de perfiles visible con
  `hosting.profiles.manage`.
- **M4 — Gates y cierre**: V0–V6 de esta spec, `blast_radius` intacto
  (nada fuera del change_surface) y DoD marcado.

## WHY

La A.SPEC 0031 dejó dos tipos de sitio duros en el código:

- `KIND`, `WEB_ROOT`, `MARKER`, `CONTENT` y `settings.static_image` son
  constantes de `static.py`; un segundo tipo de estático (Hugo, Zola, Vite
  preview, cualquier imagen con su servidor) exigiría clonar el provisioner.
- `schemas.py` valida `kind: Literal["wordpress", "static"]`, y
  `build_static_run_args()` ejecuta una sola imagen con un solo puerto.

El usuario pide "varios tipos de estáticos (o los que elija la persona)". La
decisión de diseño adoptada:

- El **admin de la plataforma** define perfiles (imagen, puerto, web root,
  health check). Decidir el runtime es infraestructura, y el catálogo es
  cerrado y curado: sin Dockerfile del tenant, sin imagen libre del tenant.
- El **tenant** solo elige el perfil al crear un sitio `static`, y edita el
  contenido por el filemanager igual que hoy.

## WHAT

1. Nueva tabla `hosting_static_profiles` (migración `003` del plugin hosting):
   - `id` VARCHAR(32) PK, `name` VARCHAR(120) NOT NULL UNIQUE,
   - `image` VARCHAR(255) NOT NULL,
   - `web_root` VARCHAR(255) NOT NULL,
   - `container_port` INTEGER NOT NULL (default `80`),
   - `health_mode` VARCHAR(16) NOT NULL (`"marker"` | `"http_200"`),
   - `env_json` TEXT NULL (objeto `{name: value}` aplicado con `--env`),
   - `command` TEXT NULL (comando `sh -c` del perfil; NULL = CMD de la imagen),
   - `created_at`, `updated_at`.
   El campo `command` lo exige la evidencia del laboratorio (ver abajo):
   Astro build on the fly necesita un comando de arranque que espera el seed.
2. Fila builtin obligatoria, creada por la migración:
   `id="nginx"`, `name="Nginx (estático)"`,
   `image=<SPANEL_HOSTING_STATIC_IMAGE del entorno>`,
   `web_root=/usr/share/nginx/html`, `container_port=80`,
   `health_mode="marker"`, `env_json=NULL`.
3. `HostingSites.profile` VARCHAR(32): nueva columna en `hosting_sites`
   (migración `003`), `NULL` para wordpress, backfill: filas existentes con
   `kind="static"` quedan con `profile_id="nginx"`.
   Canónica: FK `profile_id` a `hosting_static_profiles.id`, nullable.
4. `static.py` pasa a parametrizado (sin clonar provisioner):
   - `StaticNames` gana el label `spanel.profile=<id>`.
   - `build_static_run_args()` recibe el perfil: imagen, `--env`, montaje en
     `web_root` y el `-p`/label de Traefik con `container_port`
     (`loadbalancer.server.port=<port>`).
   - `_seed()` usa la regla validada en el laboratorio: el `index.html` sin
    marcador propio SE REEMPLAZA SIEMPRE (el de bienvenida de la imagen es el
    caso); el archivo con el marcador del perfil se respeta salvo `--force`,
    que además recrea contenedor y volumen para que seed → build queden
    coherentes en un solo arranque.
  - `_wait_http()` respeta `health_mode`: `"marker"` igual que ahora;
    `"http_200"` acepta cualquier respuesta 200 del puerto del perfil.
  - `files_root_for()` devuelve el `web_root` del perfil del sitio.
  - Perfiles `command`: arranque con `sh -c <command>` que espera al ÚLTIMO
    archivo del seed antes de compilar/servir (evidencia de lab #2). Sin
    `command`, manda el CMD de la imagen.
5. API de perfiles (superadmin = RBAC, nunca `is_superadmin`):
   - `GET /api/v1/plugins/hosting/static-profiles` — requiere
     `hosting.sites.read`; lista perfiles (para poblar el selector de la UI).
   - `POST` / `PUT` / `DELETE /api/v1/plugins/hosting/static-profiles/{id}` —
     requieren el permiso nuevo `hosting.profiles.manage`; cada cambio audita
     `hosting.profile.{created,updated,deleted}` con el id, sin valores de env.
   - `DELETE`: si hay sitios con ese `profile_id` → `409 hosting_profile_in_use`.
   - La fila `nginx` builtin no se puede borrar ni renombrar (`409
     hosting_profile_builtin`); su imagen sí se actualiza.
   - Validación de imagen: `^[A-Za-z0-9._/-]+(:[\w.-]+)?$`; `web_root` debe ser
     ruta absoluta; `container_port` 1–65535; `env_json` un objeto plano de
     strings. `422` por Pydantic en todo caso de forma inválida.
6. Creación de sitios:
   - `CreateSiteRequest` gana `profile: str = "nginx"` (solo se acepta cuando
     `kind="static"`; con `kind="wordpress"` y `profile≠"nginx"` → `422`).
   - `create_site(..., profile=...)` valida que el perfil exista → `404
     hosting_profile_unknown` si el id dado no está en la tabla.
   - `_create_static` usa el perfil resuelto; el publisher, el claim de
     dominio, la fila y el audit (`"kind": "static", "profile": id`)
     siguen siendo exactamente los de la A.SPEC 0031.
7. `GET /sites/: HostingSiteSummary` gana `profile` (`null` para wordpress).
8. `start`, `stop`, `restart`, `logs` y `DELETE` funcionan igual para un
   estático con cualquier perfil: borrar un estático custom elimina ruta,
   contenedor y volumen y libera el dominio, como hoy.
9. Guards: `_require_wordpress` conserva el mensaje y el `409
   hosting_site_wrong_kind` de la 0031; un estático con cualquier perfil no
   puede ejecutar las rutas WordPress-only.

Se decide **no cubrir**: builds server-side (compilar en el host), compose,
apps multi-contenedor, imágenes arbitrarias elegidas por el tenant y
Dockerfile libre — quedan como `out of scope` con su riesgo documentado, para
una spec futura (catálogo de apps).

## SCOPE

- `models.py`: `StaticProfile` nuevo + `profile_id` en `HostingSite`.
- Migración `003_add_hosting_static_profiles.py` (tabla + columna + backfill
  + fila builtin).
- `schemas.py`: `StaticProfileSummary`, `StaticProfileCreate`,
  `StaticProfileUpdate`, campos nuevos de `CreateSiteRequest` y
  `HostingSiteSummary`.
- `profiles.py` (nuevo): validador de imagen/ruta/puerto/env + resolución de
  perfil por id para el servicio. Lógica pura, testeada sin Docker.
- `static.py`: parametrización de nombres/run/seed/health por perfil.
- `service.py`: create con perfil, listado con perfil, CRUD de perfiles.
- `router.py`: rutas CRUD + listado de perfiles.
- `permissions/hosting.json`: permiso nuevo `hosting.profiles.manage`.
- Frontend `apps/web`: selector de perfil en "Crear sitio" y gestor de
  perfiles visible solo con `hosting.profiles.manage`.
- Tests: migración, schemas, CRUD de perfiles (incluye builtin-inmutable y
  in-use), creación con perfil custom, run args puros y health http_200.

## OUT OF SCOPE

- Dockerfile, build en el host y "imagen que elija el tenant": riesgo
  documentado y rechazado; el catálogo de perfiles es cerrado, admin-managed.
- Apps multi-contenedor (compose), catálogo de apps y aislamiento por red de
  tenant: spec futura.
- Migrar un sitio de perfil, ni cambiar el `kind` de un sitio creado.
- `www` automático, DNS, certificados públicos y transporte del proxy
  (A.SPEC 0029/0026).
- Kernel, mail, WordPress config/database, phpMyAdmin.

## CONTRACT

Precondiciones:

- Plugin `hosting` habilitado con migraciones `001` y `002` aplicadas.
- El usuario de gestión con `hosting.profiles.manage` figura explícitamente
  en el RBAC (la verificación usa `TenantContext.is_platform` /
  `require_permission`, nunca `users.is_superadmin`).
- La red Docker de `SPANEL_HOSTING_NETWORK` existe.

Postcondiciones:

1. `hosting_static_profiles` existe; fila builtin `nginx` presente tras la
   migración, idempotente bajo upgrade/downgrade/upgrade.
2. `hosting_sites.profile_id` existe, nullable, con backfill a `nginx` para
   filas `kind="static"`; wordpress queda `NULL`.
3. Formulario/payload: `{"kind":"static","name":"x","domain":"x.spanel",
   "profile":"hugo"}` crea el estático con imagen, puerto, web root, env y
   health del perfil `hugo`.
4. `profile` inexistente → `404 hosting_profile_unknown`, sin recursos.
5. `profile` con `kind="wordpress"` → `422` (schema).
6. El estático custom sirve HTTP 200 en `127.0.0.1:<port>` dentro del
   contenedor antes de marcar el sitio `running`; con `health_mode="marker"`
   se exige el marcador, como hoy.
7. `GET /sites` incluye `profile` por sitio.
8. La imagen, env y health del perfil solo se tocan por la API del admin, con
   audit `hosting.profile.*`.
9. Borrar un sitio de perfil custom limpia exactamente sus recursos con sus
   labels (`spanel.site`, `spanel.tenant`, `spanel.kind=static`,
   `spanel.profile=<id>`) y la ruta publicada.
10. Ninguna respuesta expone secretos ni valores de `env_json`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El perfil builtin nginx reproduce byte-a-byte el argv y el health de la A.SPEC 0031.
    proof: test de build_static_run_args del perfil nginx comparado con los argv de 0031 (mismas partes).
  - id: I2
    statement: Un perfil inválido no crea contenedor, volumen, ruta ni fila.
    proof: test de servicio con provisioner espía; asserts de cero llamadas.
  - id: I3
    statement: El tenant no puede elegir imagen fuera del catálogo.
    proof: schemas que rechazan image/profile libre; test 404/422.
  - id: I4
    statement: El admin gestiona perfiles solo con hosting.profiles.manage, decidido por RBAC y no por is_superadmin.
    proof: test de dependencia require_permission, sin BinaryTree de superadmin.
  - id: I5
    statement: La imagen del perfil builtin nginx sigue siendo actualizable por el admin sin tocar migraciones.
    proof: PUT sobre nginx cambia image; DELETE sobre nginx devuelve 409 hosting_profile_builtin.
  - id: I6
    statement: Un fallo de provisioning o de publish limpia solo lo creado y no deja fila.
    proof: mismo test de 0031 con cleanup; asserts de ausencia de fila.
  - id: I7
    statement: Un perfil con sitios es ineliminable; el builtin nginx es ineliminable siempre.
    proof: test DELETE in-use → 409 hosting_profile_in_use; test DELETE nginx → 409 hosting_profile_builtin.
  - id: I8
    statement: La gestión CRUD de perfiles audita cada cambio y no filtra valores de env_json.
    proof: revisión de argv/logs; test de details del audit con el id del perfil y sin valores.
  - id: I9
    statement: Un perfil custom (health http_200) no se marca running cuando su servidor web no responde.
    proof: test del wait parametrizado con timeout en `_wait_http`; mismo camino de static.py.
  - id: I10
    statement: Los sitios WordPress existentes no se ven afectados y los endpoints viejos no cambian.
    proof: tests de hosting existentes verdes; kind sigue siendo wordpress para las filas viejas.
```

## VERIFICATION

### V0 — Gates

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/hosting/backend/tests \
  vendor/systutor-core/plugins/domains/backend/tests \
  vendor/systutor-core/plugins/tenant/backend/tests \
  vendor/systutor-core/plugins/mail/backend/tests -q

cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

### V1 — Migración y fila builtin

- `test_static_profiles_migration.py`: upgrade crea tabla + fila `nginx`;
  downgrade la quita y elimina `profile_id`; upgrade de nuevo. El backfill
  mapea los estáticos existentes a `nginx`.
- Nombres/nulos validados con SQLite.

### V2 — Perfiles puros

- `validate_profile()` rechaza: imagen vacía o con `--privileged`, puertos
  fuera de rango, `web_root` relativa, `env_json` no-objeto o con valores no
  string.
- `build_static_run_args()` para un perfil custom: contiene `--env`,
  `loadbalancer.server.port=<port>` y montaje `<vol>:<web_root>`.

### V3 — CRUD de perfiles

- `GET` devuelve el builtin y los creados.
- `POST` con imagen inválida → `422`; con buen id → `201`.
- `PUT` cambia `image` de `nginx`.
- `DELETE nginx` → `409 hosting_profile_builtin`.
- `DELETE` con sitios → `409 hosting_profile_in_use`, fila intacta.
- Audit por acción en cada caso, sin `env_json` en los details.

### V4 — Creación de sitios con perfil

- `{"kind":"static","profile":"king"}` inexistente → `404
  hosting_profile_unknown`, cero llamadas Docker.
- `{"kind":"wordpress","profile":"king"}` → `422`.
- Crear estático con perfil king: contenedor con labels
  `spanel.profile=king`, imagen del perfil, publisher único de ruta.
- Fallo de provisioning → cleanup y sin row (igual que I6 de 0031).

### V5 — Listado, lifecycle y compatibilidad

- `GET /sites` incluye `profile` (`null` para wordpress).
- `start/stop/restart/logs` pasan para un estático custom.
- `config`, `database`, `phpmyadmin`, `users`, `salts`, `reset` siguen con
  `409 hosting_site_wrong_kind`.
- Borrar un estático custom llama cleanup y unpublish.
- Los tests preexistentes de hosting, domains, tenant y mail no cambian de
  resultado.

### V6 — Composición

- `pytest` verde (V0). `tsc --noEmit` verde. Ruff/pyright del vendedor verde.
- La migración `003` no toca kernel, mail ni las migraciones `001`/`002` del
  plugin.

## ROLLBACK

- `003` define `downgrade()` y el test hace up/down/up en SQLite; el
  downgrade elimina la tabla y la columna, no toca Docker.
- El CSV de perfiles creados por el admin y no existentes en el downgrade se
  pierde: la reversión borra la tabla completa. Los contenedores de sitios
  creados con perfiles custom no se borran solos al revertir; se eliminan por
  API o CLI como los estáticos de 0031.
- `spanel.profile` en los labels queda como dato inerte si se revierte el
  downgrade; ningún código lo exige después.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/hosting/backend/profiles.py          # nuevo
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/static.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/settings.py          # solo lectura de static_image vigente para el backfill del builtin
    - vendor/systutor-core/plugins/hosting/migrations/003_add_hosting_static_profiles.py  # nuevo
    - vendor/systutor-core/plugins/hosting/permissions/hosting.json
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - vendor/systutor-core/plugins/hosting/README.md
    - apps/web/src/**   (pantalla hosting: selector de perfil + gestor)
    - aspecs/0042-hosting-static-profiles.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/domains/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/plugins/tenant/**
    - docker-compose.yml
    - scripts/**
    - /opt/stacks/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - creacion de sitios estaticos (argv y health dependeran del perfil)
    - CRUD de perfiles y su tabla nueva
  indirect:
    - listado y borrado de sitios (nuevo campo profile)
    - UI de hosting (selector y gestor)
    - filemanager (files_root por perfil, ya parametrizado)
  must_not_affect:
    - sitios WordPress existentes y sus contenedores
    - publisher del plugin domains y su contrato (site_domain_publisher)
    - mail, tenant y kernel
    - migraciones 001 y 002 de hosting
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013
    - 0027
    - 0030
    - 0031
    - 0032
  must_compose_with:
    - plugin domains (publisher del file provider)
    - plugin tenant (site_suffix)
    - plugin filemanager (files_root parametrizable)
  systemic_invariants:
    - un dueno por Host
    - aislamiento por tenant
    - deshabilitar un plugin no cambia el ruteocomposition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest plugins/hosting/backend/tests plugins/domains/backend/tests plugins/tenant/backend/tests plugins/mail/backend/tests
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

## Prototype outside this change_surface

`scripts/spanel_profiles/` + `scripts/spanel-profiles.py` son el prototipo de
verificación (fuera del plano de la implementación); no prueban el flujo del
plugin. Su existencia no contradice `prohibited: scripts/**` de la
implementación: puede correr por su lado con el transporte del `.env` del
despliegue, sin pasar por la API del kernel.

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la API expone perfiles; static.py arma argv y health a partir del perfil; la decision runtime nunca la toma el tenant
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/profiles.py
    - vendor/systutor-core/plugins/hosting/backend/static.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
```

## Traceability

- Requirement: que el superadmin pueda habilitar varios tipos de sitios
  estáticos (Hugo, Zola, Vite preview, otra imagen) sin crear un provisioner
  nuevo por cada uno, y que el tenant elija entre ellos sin definir
  infraestructura.
- owner: luc444s
- approval: luc444s
- Deployment: laboratorio primero (migración + builtin nginx + un perfil de
  prueba), producción tras gates verdes.

## Definition of Done

- [x] Protocolo de laboratorio: los 4 perfiles reales (plain nginx, react
      Caddy, boot httpd, astro Node build+preview) ARMAN Y SIRVEN verificados
      por el prototipo `scripts/spanel_profiles` — `verify` 24/24 PASS y un
      flujo up/verify/down idempotente (estado al 2026-11-06).
- [ ] Objetivo: perfiles definidos por el admin; un estático con perfil king
      responde HTTP 200 en el laboratorio.
- [ ] Alcance respetado: sin Dockerfile de tenant, sin kernel, sin proxy.
- [ ] Contrato: tabla de perfiles, columnas, bindings, API CRUD, guards.
- [ ] Invariantes I1–I10 probados.
- [ ] V0–V6 ejecutados.
- [ ] Rollback de migración ejecutado (up/down/up).
- [ ] Sin archivos fuera de `change_surface`.
- [ ] Nada >600 líneas; ideal <400.
- [ ] Trazabilidad completa.
