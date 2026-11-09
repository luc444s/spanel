# A.SPEC 0053 — GitHub App Manifest Flow y puesta operativa de Apps

> `risk: high` — reemplaza credenciales OAuth personales por GitHub Apps
> creadas por cada instalación de SPanel, cifra claves privadas, emite tokens de
> instalación y conserva despliegues Docker/SSH en un worker Dramatiq separado.
> Se entrega como **una sola spec con cinco commits secuenciales**. Requiere
> `approver: luc444s` y un E2E real antes de declarar Apps listo para producción.

## WHY

A.SPEC 0052 dejó el runtime Compose, API async, worker y frontend iniciales, pero
el plugin todavía no es operativo:

- en localhost `GET /github/connection` responde 500 porque intenta abrir
  `/run/secrets/apps_key` incluso cuando no existe ninguna conexión;
- la UI muestra simultáneamente “Sin conexión” y un error;
- el OAuth App actual exige client ID, client secret y callback globales;
- no existe un worker Apps ejecutándose en el entorno local;
- la fuente pertenece a un token personal amplio, no a una instalación con
  repositorios seleccionados;
- la API no expone todavía todo el historial, metadata, candidatos Compose y
  servicios que necesita la experiencia completa;
- el E2E real de segundo release y rollback no se ha ejecutado.

El producto deseado es autoservicio: un administrador autorizado pulsa
**Conectar GitHub**, registra una GitHub App desde un manifest, la instala en su
cuenta u organización, elige repositorios y vuelve a SPanel. Cada instancia
genera sus propias credenciales; no existe un secreto OAuth global de SPanel.

## WHAT

Sustituir el OAuth personal de 0052 por GitHub App Manifest Flow y terminar la
puesta operativa de Apps:

1. corregir carga del plugin y estados fail-closed;
2. registrar una GitHub App por manifest para cuenta personal u organización;
3. verificar y persistir instalaciones tenant-scoped;
4. sincronizar únicamente metadata no secreta de repositorios mediante jobs;
5. emitir installation tokens de una hora, restringidos al repositorio, sólo en
   el worker;
6. completar API, UI y un CLI API-first para registrar/desplegar aplicaciones;
7. retirar el OAuth antiguo y demostrar GitHub → Dramatiq → Docker/SSH → proxy.

Esta A.SPEC no reemplaza las reglas de auditoría, ownership, non-root, rollback,
RBAC ni aislamiento de secretos de 0052/0051. Sólo reemplaza el modelo de
autenticación de fuente y completa superficies que quedaron incompletas.

## PRINCIPIOS NO NEGOCIABLES

- PostgreSQL es la fuente durable de verdad de aplicaciones y operaciones.
- Dramatiq/Redis transporta únicamente `operation_id`.
- Ningún mensaje contiene token, PEM, manifest, variables privadas o comandos.
- HTTP no ejecuta clone, Docker ni SSH. Los intercambios cortos del handshake
  Manifest y la verificación de instalación son las únicas llamadas GitHub
  permitidas en callbacks, siempre con timeout.
- Listados GET ordinarios leen DB; sincronizar GitHub es un job `github_sync`.
- El worker de Apps empieza con un proceso, un hilo y ejecución serializada.
- Una installation token nunca se persiste y se limita a un solo repositorio.
- No se confía en `Host`, `X-Forwarded-Host`, `installation_id`, `state`, tenant,
  usuario, owner, repo ni redirect recibidos del browser.
- No se usa `users.is_superadmin`; plataforma se decide con
  `core.platform.admin`.

## MODELO OBJETIVO

### `apps_github_providers`

Una GitHub App registrada desde SPanel:

- `id`, `tenant_id`, `created_by_user_id`;
- `github_app_id` globalmente único, `app_slug`, nombre y owner metadata;
- PEM privado cifrado AES-256-GCM, nonce y versión de clave;
- estado `pending_installation`, `active`, `revoked` o `error`;
- timestamps de creación, último uso y revocación.

No se persisten el client secret ni el webhook secret devueltos por la
conversión. Esta fase no usa OAuth de usuario ni webhooks: el manifest declara
`request_oauth_on_install=false` y webhook inactivo/omitido. Auto-deploy por push
queda fuera de alcance.

### `apps_github_installations`

- ID interno, `provider_id`, `tenant_id` redundante para guardas;
- `github_installation_id` globalmente único;
- account id/login/type verificados mediante API autenticada como la App;
- `repository_selection`, permisos efectivos y estado;
- timestamps de instalación, sync, suspensión y eliminación.

Una respuesta `installation_id` del setup URL nunca es prueba suficiente. El
backend consume un state one-time ligado al flujo y consulta
`GET /app/installations/{id}` con JWT firmado por la clave del provider antes de
persistir o actualizar la instalación.

### `apps_github_repositories`

Catálogo no secreto sincronizado por worker:

- instalación, GitHub repository id y `owner/name` canónico;
- visibilidad privada/pública, default branch, archived/disabled;
- timestamp de última observación y tombstone cuando deja de estar autorizado.

No se copian contenidos, secrets, deploy keys ni tokens a esta tabla.

### Cambios compatibles a Apps

- `apps_applications.source_installation_id` y `source_repository_id` pasan a ser
  la identidad autorizada de fuente;
- `repository` y `branch` siguen siendo snapshots legibles y auditables;
- `apps_operations.application_id` admite null sólo para operaciones de control
  como `github_sync`, con `subject_type/subject_id` obligatorios en ese caso;
- agregar timestamps de inicio/fin y resultados necesarios para historial;
- conservar la tabla OAuth antigua durante esta entrega para rollback de DB,
  pero ningún código final nuevo la lee o escribe.

No se inventa una migración automática de tokens OAuth a instalaciones. Una app
legacy queda `source_reconnect_required` hasta seleccionar una instalación y un
repositorio autorizados. Nunca se elige otra credencial silenciosamente.

## MANIFEST FLOW

### Configuración pública

Apps recibe `SPANEL_APPS_PUBLIC_URL`, origen absoluto sin path, query ni fragment:

- HTTPS obligatorio en producción;
- `http://localhost` y loopback permitidos sólo fuera de producción;
- callbacks se construyen exclusivamente desde esta configuración;
- headers proxy/Host no cambian URLs de seguridad.

La única credencial estática es `SPANEL_APPS_ENCRYPTION_KEY_FILE`, compartida
read-only por web y worker. Debe decodificar exactamente 32 bytes. No se deriva
de JWT ni se guarda en `.env`.

### Inicio

`POST /github/manifest/start` exige sesión y `apps.github.connect`. Body:

```json
{"owner_type":"personal","organization":null}
```

Para organización se exige slug validado. El backend crea dos estados aleatorios
independientes, one-time, en Redis:

- registration state ligado a user, tenant, owner target y redirect interno;
- installation state ligado al mismo actor/tenant y al provider que se creará.

TTL máximo 15 minutos. Redis caído responde 503 antes de abandonar SPanel.
Devuelve `action_url`, `state` y manifest JSON. El frontend crea un `<form
method="post">` y lo envía a uno de estos hosts exactos:

```text
https://github.com/settings/apps/new
https://github.com/organizations/<slug>/settings/apps/new
```

El manifest contiene:

- nombre único editable de máximo 34 caracteres;
- homepage `SPANEL_APPS_PUBLIC_URL`;
- `redirect_url` de conversión y `setup_url` con installation state;
- `public=false`, `request_oauth_on_install=false`;
- permiso mínimo `contents: read` y metadata de solo lectura;
- sin eventos ni webhook activo.

### Conversión

GitHub vuelve a `/github/manifest/callback?code&state`. El callback:

1. consume state atómicamente y recupera tenant/actor server-side;
2. intercambia `code` una vez en `/app-manifests/{code}/conversions`;
3. valida tipos, límites, App ID y PEM RSA recibido;
4. cifra el PEM inmediatamente con AAD provider+tenant+versión;
5. descarta client secret y webhook secret sin log ni persistencia;
6. crea provider `pending_installation` y redirige al install URL del `app_slug`.

Code/state/PEM/secrets nunca aparecen en logs, auditoría, errores públicos,
query de retorno a la UI o mensajes Dramatiq. Fallo parcial no deja un provider
marcado active.

### Instalación

El setup URL vuelve a `/github/install/callback` con installation state e
`installation_id`. El backend verifica state, descifra PEM, firma JWT de vida
corta y consulta la instalación como App. Sólo entonces:

- persiste account metadata y permisos efectivos;
- marca provider/installation activos;
- audita IDs no secretos;
- encola `github_sync` y redirige a `/app/apps?github=installed`.

Cancelación, replay, state expirado, instalación perteneciente a otra App,
provider/tenant distinto o instalación suspendida fallan cerrado.

## TOKENS Y WORKER

Para `github_sync`, inspect o deploy:

1. el worker recarga operation, tenant, provider, installation y repository;
2. exige provider/installation activos y repo no tombstoned;
3. descifra PEM just-in-time;
4. crea JWT GitHub App con `iat` tolerante a clock skew y `exp <= 10 min`;
5. solicita `/app/installations/{id}/access_tokens` restringiendo por
   `repository_ids: [source_repository_id]` y `contents: read`;
6. acepta tokens opacos de longitud/formato variable y expiración máxima de una
   hora; no asume el formato histórico de 40 caracteres;
7. inyecta `GH_TOKEN` y `GH_PROMPT_DISABLED=1` sólo al child `gh`;
8. elimina referencias alcanzables al terminar y nunca cachea el token en DB,
   Redis, disco, logs, argv, manifest o resultado.

`github_sync` pagina `GET /installation/repositories`, actualiza catálogo y
tombstones. Un repo retirado bloquea nuevas inspecciones/deploys, pero lifecycle
de containers ya desplegados continúa según RBAC.

## API OBJETIVO

Base `/api/v1/plugins/apps`:

```http
GET    /github/status
POST   /github/manifest/start
GET    /github/manifest/callback
GET    /github/install/callback
GET    /github/providers
GET    /github/installations
POST   /github/installations/{id}/sync
GET    /github/installations/{id}/repositories
DELETE /github/providers/{id}

POST   /applications
GET    /applications
GET    /applications/{id}
PATCH  /applications/{id}/source
POST   /applications/{id}/inspect
POST   /applications/{id}/deployments
GET    /applications/{id}/deployments
GET    /applications/{id}/operations
POST   /applications/{id}/actions/{refresh|logs|start|stop|restart|down}
DELETE /applications/{id}
GET    /operations/{operation_id}
```

`GET /github/status` siempre es DB-only y funciona aunque la clave secreta no
esté disponible. Devuelve estado de readiness explícito, nunca un falso
“desconectado”. Ningún response expone PEM, ciphertext, nonce, JWT, installation
token, code o state.

Create requiere `installation_id` interno y `repository_id` interno; el backend
deriva `owner/repo` canónico y default branch desde DB. No acepta una URL Git ni
un repositorio arbitrario. Inspect persiste candidatos Compose, servicios y
puertos detectados. Detail devuelve deployment activo y metadata requerida; los
historiales sobreviven a reload.

## UI Y CLI

### UI

La tarjeta GitHub tiene estados mutuamente excluyentes:

```text
loading | unconfigured | registering | pending_installation |
syncing | connected | suspended | error
```

Nunca muestra “Sin conexión” junto a un error. Flujo:

1. **Conectar GitHub**;
2. elegir cuenta personal u organización;
3. POST del manifest a GitHub;
4. registrar App;
5. instalar y seleccionar repositorios;
6. volver a SPanel y observar sync;
7. elegir instalación, repositorio y rama en Nueva aplicación.

La UI valida hosts GitHub exactos antes de navegación/form submit, no maneja
secrets y conserva gates RBAC de 0052. Debe mostrar historial de operaciones,
deployment activo, commit, servicios, Compose candidates y findings después de
recargar.

### CLI API-first

Añadir `scripts/spanel_apps.py`, sin imports del runtime ni acceso DB/Docker/SSH:

```text
spanel_apps.py providers
spanel_apps.py repositories --installation <id>
spanel_apps.py add --installation <id> --repository <id> --name <name> [--branch ...]
spanel_apps.py inspect <app-id> [--wait]
spanel_apps.py deploy <app-id> --env-file <0600-file> [--wait]
spanel_apps.py status <operation-id>
spanel_apps.py list
spanel_apps.py logs|start|stop|restart|down|delete <app-id>
```

Lee URL/token SPanel desde entorno o stdin seguro, nunca acepta tokens en argv,
usa `Idempotency-Key`, polling acotado y salida JSON opcional. El registro de la
GitHub App sigue siendo browser-based.

## RBAC Y AUDITORÍA

- `apps.github.connect`: registrar, instalar, sincronizar y retirar providers
  del tenant; tenant admin lo conserva por defecto.
- `apps.applications.read`: metadata e historiales tenant-scoped.
- `apps.applications.create`: crear, seleccionar fuente e inspeccionar.
- `apps.applications.manage`: lifecycle y logs.
- deploy/delete siguen exigiendo permiso Apps correspondiente más
  `core.platform.admin`.

Auditar registration start, conversion success/failure, instalación verificada,
sync, suspensión/revocación, source selection y todos los jobs. Sólo IDs, actor,
tenant, account/repo metadata y códigos públicos; nunca material criptográfico.

## SECUENCIA DE COMMITS

### Commit 1 — 0053A: baseline operativo

```text
fix(apps): stabilize plugin loading and readiness (A.SPEC 0053A)
```

- conservar y terminar los fixes locales de carga dinámica del plugin;
- validar estructura bundled (`backend`, `frontend`, `migrations`, `permissions`,
  `events`) sin romper plugins externos;
- hacer connection/readiness DB-only;
- corregir estados UI excluyentes;
- añadir tests de carga real y regresión del 500 sin secret.

No cambia todavía OAuth ni modelos de fuente.

### Commit 2 — 0053B: providers y Manifest Flow

```text
feat(apps): register github app providers from manifests (A.SPEC 0053B)
```

- migración aditiva de providers/installations/repositories;
- cifrado purpose/tenant/provider-bound;
- Redis one-time states;
- manifest start, conversion e install callbacks;
- configuración de public URL y tests contract/security;
- status/list/remove tenant-aware.

OAuth antiguo coexiste internamente; ninguna app se migra silenciosamente.

### Commit 3 — 0053C: instalaciones en worker

```text
feat(apps): deploy through github installations (A.SPEC 0053C)
```

- `github_sync` durable por Dramatiq;
- JWT e installation tokens repo-scoped;
- catálogo y selección de repositorios;
- nueva identidad de fuente en aplicaciones;
- inspect/deploy usan sólo instalación;
- operaciones/historial/metadata/candidatos/servicios completos;
- recuperación, idempotencia y secret canaries.

### Commit 4 — 0053D: experiencia y CLI

```text
feat(apps ui): add manifest setup and api cli (A.SPEC 0053D)
```

- UI registrar → instalar → sincronizar → seleccionar repo;
- wizard, detalle e historial contra API completa;
- `scripts/spanel_apps.py` API-first y tests;
- documentación operativa local/producción.

### Commit 5 — 0053E: retiro OAuth y E2E

```text
test(apps): retire oauth and prove manifest deployments (A.SPEC 0053E)
```

- eliminar endpoints, servicio y UI OAuth personal;
- retirar client ID/client secret y mount `apps_github` de Compose;
- conservar sólo encryption key secret;
- E2E opt-in de manifest/instalación, deploy, segundo release y rollback;
- pruebas negativas de replay, cross-tenant, repo retirado y worker caído;
- actualizar README/env examples y evidencia redactada.

La tabla OAuth legacy queda sin uso para rollback de datos y se podrá eliminar en
otra migración después de una versión estable.

Cada commit pasa sus gates y no depende de cambios sin commit del siguiente.
Ningún commit se hace push automáticamente.

## E2E Y DEFINITION OF DONE

En laboratorio con URL HTTPS pública o túnel controlado:

1. tenant admin registra App personal y/o de organización;
2. replay de ambos states falla;
3. instalación spoofed/cross-tenant falla;
4. repositorios seleccionados aparecen tras job `github_sync`;
5. repo no autorizado no puede registrarse ni clonarse;
6. add+inspect devuelve 202 y el web process no ejecuta `gh`;
7. deploy produce servicios healthy y efectivos non-root;
8. dominio HTTPS publica el servicio/puerto elegidos;
9. refresh/logs/stop/start/restart pasan por Dramatiq;
10. segundo commit sano crea deployment inmutable nuevo;
11. release unhealthy falla y restaura el deployment anterior;
12. retirar repo bloquea inspect/deploy pero lifecycle existente funciona;
13. suspender/eliminar instalación marca source reconnect required;
14. reiniciar web/worker conserva historial y reconcilia jobs según contrato;
15. logs/DB/Redis/API/evidencia no contienen canarios PEM/token/env.

Apps se considera operativo sólo si app y worker arrancan desde Compose, health
del worker es observable, no hay operaciones `queued` huérfanas y el E2E real
queda documentado sin secretos.

## VERIFICACIÓN

Cada commit ejecuta el subconjunto afectado; antes de 0053E se exige el gate
completo:

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

.venv/bin/python -m pytest \
  scripts/tests/test_app_deploy.py \
  vendor/systutor-core/plugins/apps/backend/tests \
  vendor/systutor-core/tests/test_plugin_registry.py -q

.venv/bin/ruff check \
  scripts/spanel_apps.py \
  vendor/systutor-core/plugins/apps \
  vendor/systutor-core/src/systutor/kernel/plugins/runtime.py \
  vendor/systutor-core/tests/test_plugin_registry.py

cd apps/web
npm test
npm run build

cd /data/data/com.termux/files/home/Proyectos/mailadmin
SPANEL_LIVE_TESTS=0 .venv/bin/python -m pytest \
  scripts/tests/test_app_deploy_live.py -q

POSTGRES_USER=verify POSTGRES_PASSWORD=verify POSTGRES_DB=verify \
  docker compose config >/dev/null

cd vendor/systutor-core
../../.venv/bin/ruff check .
../../.venv/bin/python -m pyright
../../.venv/bin/python -m pytest tests -q

cd /data/data/com.termux/files/home/Proyectos/mailadmin
git diff --check
```

El live deshabilitado debe reportar skip explícito. El gate real usa
`SPANEL_LIVE_TESTS=1`, una instalación GitHub de laboratorio y secretos externos
al checkout; nunca imprime sus valores.

## OUT OF SCOPE

- GitHub Enterprise Server.
- Webhooks, auto-deploy por push y preview environments.
- OAuth de usuario, Device Flow, PAT pegado o `gh auth login`.
- Crear una GitHub App global compartida por todos los SPanel.
- Registry privado, GitLab/Bitbucket y repositorios Git arbitrarios.
- Paralelizar builds no confiables o distribuir workers entre hosts.
- Editor web de Compose/Dockerfile, shell/terminal y bypass de auditoría.
- Borrar la tabla OAuth legacy en esta misma versión.

## INVARIANTS

```yaml
invariants:
  - id: M1
    statement: Callbacks y manifest usan sólo el public URL configurado, nunca Host arbitrario.
    proof: tests forwarded-host/production HTTPS y snapshots de manifest.
  - id: M2
    statement: States de registro/instalación son aleatorios, one-time, tenant/user-bound y expiran.
    proof: replay, expiry, cancellation y cross-tenant tests con Redis real/fake atómico.
  - id: M3
    statement: Installation ID se verifica autenticando como la App antes de persistir.
    proof: setup spoofed, wrong App y suspended installation fixtures.
  - id: M4
    statement: PEM queda cifrado con AAD provider+tenant y nunca sale por API/log/queue.
    proof: tamper/wrong-tenant/key-version y secret-canary tests.
  - id: M5
    statement: Installation tokens se crean sólo en worker, repo-scoped y no se persisten.
    proof: HTTP external-I/O spies, token request body y canaries DB/Redis/files/logs.
  - id: M6
    statement: Dramatiq recibe solamente operation_id y duplicados no repiten mutaciones.
    proof: actor signature, serialized message y duplicate/recovery tests.
  - id: M7
    statement: Toda lectura tenant-scoped y todo deploy/delete conserva doble gate plataforma.
    proof: matriz RBAC, IDOR/cross-tenant y platform permission tests.
  - id: M8
    statement: Repositorio de aplicación siempre procede del catálogo autorizado server-side.
    proof: arbitrary owner/repo, tombstone y installation mismatch tests.
  - id: M9
    statement: Estado GitHub se consulta sin descifrar secrets y la UI no mezcla error/desconectado.
    proof: missing-key API test y estados exclusivos de componente.
  - id: M10
    statement: Auditoría/non-root/ownership/rollback de 0051/0052 no se relajan.
    proof: 49 runner tests más E2E segundo release/fallo/rollback.
```

## CHANGE SURFACE Y GUARDAS

Permitido durante la secuencia, limitado a lo necesario:

```yaml
change_surface:
  allowed:
    - aspecs/0053-github-app-manifest-and-apps-readiness.md
    - vendor/systutor-core/plugins/apps/**
    - vendor/systutor-core/src/systutor/kernel/plugins/runtime.py
    - vendor/systutor-core/tests/test_plugin_registry.py
    - scripts/spanel_apps.py
    - scripts/tests/test_spanel_apps.py
    - scripts/tests/test_app_deploy_live.py
    - apps/web/src/plugins/**
    - apps/web/src/shell/**
    - Dockerfile
    - docker-compose.yml
    - .env.docker.example
    - vendor/systutor-core/pyproject.toml
    - vendor/systutor-core/README.md
  prohibited:
    - apps/web/src/features/auth/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - .env
    - .env.docker
```

- Los fixes locales preexistentes de plugin loader/register/tests/events son
  trabajo válido de 0053A y no se descartan ni reescriben sin inspección.
- Cambios a Domains sólo se permiten si una regresión demostrable del contrato
  de puerto de 0052 lo exige; de otro modo quedan prohibidos.
- No se relajan permisos, auditoría ni reglas runtime para facilitar el E2E.

## ROLLBACK

- 0053A es corrección independiente y no se revierte al retirar Manifest Flow.
- Antes de 0053E, revertir B–D mantiene OAuth legacy disponible.
- Después de 0053E, rollback de código puede reactivar OAuth porque su tabla no
  se borró, pero exige restaurar configuración/secrets antiguos explícitamente.
- Revertir nunca borra providers, instalaciones, aplicaciones, deployments ni
  recursos Docker. Limpieza se realiza sólo mediante operaciones Apps
  autorizadas y auditadas.

## Referencias normativas

- https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest
- https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
