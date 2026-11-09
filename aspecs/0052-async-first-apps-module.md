# A.SPEC 0052 — Módulo Apps async-first para aplicaciones Compose

> `risk: high` — permite a un administrador de plataforma clonar repositorios,
> ejecutar builds Docker no confiables y operar aplicaciones multi-contenedor
> en el host de laboratorio. Todo I/O de repositorio, Docker y SSH ocurre fuera
> del request web, en un worker serializado; únicamente los endpoints OAuth hacen
> intercambios cortos de credenciales con GitHub. Conserva las guardas fail-closed de
> A.SPEC 0051. Requiere `approver: luc444s`.

## WHY

A.SPEC 0051 validó la primitiva antes de integrarla al panel:

- descubre Compose y Dockerfiles sin conocer tecnologías concretas;
- informa riesgos con archivo, línea, servicio y remediación;
- bloquea privilegios, escapes y recursos ajenos;
- exige usuario no-root en Dockerfile, imagen y procesos efectivos;
- despliega por Docker local o SSH con manifiesto y labels de propiedad;
- demostró el flujo real con frontend, backend y database.

Falta convertir esa primitiva en el módulo `apps` de SPanel. Copiar el script
dentro del plugin crearía dos políticas de seguridad que divergirían. Ejecutarlo
directamente durante un request HTTP mantendría conexiones abiertas durante
clones/builds de varios minutos y no sobreviviría a reinicios del proceso web.

Por eso el módulo nace **async-first**: cada request de operación sólo autoriza,
persiste y encola; un worker separado ejecuta el motor; la UI consulta el estado
persistido. No existe una primera versión síncrona que luego se migra.

## WHAT

Crear el plugin `apps`, tenant-aware, que permite:

1. conectar la cuenta GitHub de cada usuario mediante OAuth web;
2. registrar una aplicación desde un repositorio privado accesible por esa
   conexión personal;
3. inspeccionarla de forma asíncrona y mostrar el informe de A.SPEC 0051;
4. seleccionar servicio/puerto de entrada, dominio y variables privadas;
5. desplegarla de forma asíncrona, siempre con procesos non-root;
6. consultar estado y logs redactados mediante operaciones asíncronas;
7. iniciar, detener, reiniciar, redesplegar y retirar sólo recursos propios;
8. publicar el dominio por el plugin `domains` después de validar health.

`scripts/app_deploy.py` queda como CLI delgada sobre el mismo motor importable
que usa el worker. Se copia la **forma** de los plugins existentes —manifest,
RBAC, migraciones, router/service, auditoría y frontend—, no su lógica de
WordPress ni el código del runner.

## SCOPE

### 1. Un solo motor de Apps

La implementación probada de `scripts/app_deploy.py` se extrae a módulos bajo:

```text
vendor/systutor-core/plugins/apps/backend/runtime/
  audit.py
  source.py
  transport.py
  deployment.py
  lifecycle.py
  manifests.py
```

La división exacta puede variar para mantener cohesión, pero se cumplen estas
reglas:

- una sola implementación de auditoría, ownership, staging, non-root y
  lifecycle;
- la CLI y el worker llaman APIs Python tipadas y reciben resultados
  estructurados; no parsean el `stdout` uno del otro;
- `scripts/app_deploy.py` conserva flags, salida JSON/humana y exit codes de
  A.SPEC 0051 mediante un adaptador fino;
- los 49 tests offline existentes siguen validando el mismo comportamiento;
- no se relaja ninguna regla de A.SPEC 0051;
- los módulos nuevos respetan el límite de 600 líneas y se dividen antes de
  alcanzar 400 cuando mezclen responsabilidades.

Esta A.SPEC reemplaza únicamente la restricción estructural “script monolítico”
de A.SPEC 0051. Sus invariantes de seguridad continúan vigentes.

### 2. Plugin, permisos y persistencia

Crear `vendor/systutor-core/plugins/apps/` con manifest, permisos, backend,
migraciones, frontend y tests.

#### `apps_applications`

- `id`, UUID server-side;
- `tenant_id`, obligatorio e indexado;
- `name` y `slug` tenant-scoped;
- `repository`, exclusivamente `owner/repo` de GitHub;
- `source_connection_user_id`, dueño de la conexión OAuth usada por la fuente;
- `branch`;
- `compose_file`, nullable hasta resolver una inspección ambigua;
- `runtime_project`, generado por servidor y globalmente único;
- `domain`, nullable y globalmente único cuando está presente;
- `ingress_service` e `ingress_port`, nullable hasta configurar entrada;
- `status` denormalizado para listado;
- `active_deployment_id`, nullable;
- `active_operation_id`, nullable y usado como lock lógico;
- `deleted_at`, nullable, para tombstone seguro;
- timestamps.

#### `apps_github_connections`

Una conexión personal por usuario de SPanel:

- `user_id`, único e indexado;
- `github_user_id`, login y avatar metadata no secreta;
- scopes concedidos y timestamps de conexión/último uso;
- access token y refresh token cifrados por separado;
- expiraciones, nonce/tag y versión de clave;
- estado `connected`, `expired` o `revoked`.

Los tokens nunca se guardan en claro. La conexión pertenece al usuario, no al
tenant ni a la aplicación. Una aplicación referencia qué usuario autorizó su
fuente para que otro operador no sustituya silenciosamente esa identidad. Si el
dueño desconecta GitHub, pierde acceso al tenant o queda inactivo, nuevas
inspecciones/deploys fallan cerrado con `apps_github_reconnect_required`; otro
usuario autorizado debe reconectar explícitamente la fuente con su propia
cuenta antes de continuar.

#### `apps_deployments`

Registro inmutable de cada intento de deploy:

- `id`, `application_id` y `operation_id`;
- repo, branch, commit, tree hash y Compose efectivos;
- servicio, puerto y dominio de entrada efectivos;
- estado y release remoto;
- manifiesto JSON sin secretos;
- resumen de error público, nullable;
- timestamps.

Nunca se sobrescribe un deployment histórico para presentarlo como otro commit.
`active_deployment_id` sólo cambia después de health, validación non-root y
publicación satisfactoria.

#### `apps_operations`

Es la fuente durable de verdad para jobs:

- `id`, `application_id`, `deployment_id` nullable;
- `kind`: `inspect`, `deploy`, `refresh`, `logs`, `start`, `stop`, `restart`,
  `down` o `delete`;
- `status`: `queued`, `running`, `succeeded`, `failed` o `interrupted`;
- `phase`, sin porcentajes ficticios;
- actor, tenant, request/correlation id;
- `idempotency_key` y hash del request;
- message id, timestamps, heartbeat y attempt;
- informe, resultado y error público JSON, siempre sin secretos.

Una operación terminal es inmutable salvo campos explícitos de auditoría. La
respuesta de la UI se reconstruye desde DB, no desde memoria del web worker.

### 3. Cola y worker async-first

Añadir un worker Dramatiq dedicado para la cola `apps`, con un proceso y un hilo
en esta fase. El actor recibe **solamente `operation_id`**. No recibe tokens,
variables, repo, argumentos Docker ni objetos serializados por el browser.

Flujo de enqueue:

1. autorizar tenant + RBAC;
2. validar el request sin I/O externo;
3. crear/actualizar aplicación y crear `apps_operations(status=queued)`;
4. adquirir atómicamente `active_operation_id`;
5. guardar y hacer commit;
6. encolar el ID;
7. devolver `202`.

Si Redis/dispatcher no está disponible, no se simula asincronía en thread ni se
ejecuta inline: la operación queda terminal `failed` con código
`apps_queue_unavailable`, se libera el lock y la API responde 503.

El actor reclama atómicamente `queued -> running`. Un mensaje duplicado observa
que la operación ya fue reclamada/terminó y sale sin repetir Docker. Los actores
de Apps usan `max_retries=0`: una caída no repite a ciegas un `compose up` o
`down`. Al arrancar, el worker reconcilia bajo lock:

- jobs `queued` sin message id por una caída entre commit y enqueue se vuelven a
  encolar una vez;
- jobs `running` con heartbeat vencido pasan a `interrupted`, liberan el lock y
  exigen una operación explícita posterior;
- nunca se adopta ni elimina un recurso durante reconciliación.

Cada cambio de fase actualiza heartbeat y commit corto. No se sostiene una
sesión DB durante clone, SSH, build, espera de health o lectura de logs.

Todas las operaciones runtime, incluso refresh y logs, pasan por el worker. Los
GET del módulo son lecturas DB y nunca invocan `gh`, Docker ni SSH.

### 4. Idempotencia y exclusión

Todo POST async exige `Idempotency-Key` opaco con longitud acotada:

- misma key + mismo tenant/endpoint/body devuelve la operación original;
- misma key con otro body responde 409;
- una aplicación admite una sola operación activa;
- contención responde 409 `apps_operation_busy` con el ID activo;
- además existe un lock global de worker al ejecutar builds/deploys, de modo que
  esta fase nunca corre dos builds no confiables en paralelo.

El frontend genera una key por intención de usuario, deshabilita doble submit y
reutiliza la misma key sólo al reintentar la misma petición incierta.

### 5. GitHub OAuth por usuario, fuente e inspección

La página Apps muestra **Conectar GitHub**. Es un OAuth App de GitHub con web
application flow; no ejecuta `gh auth login`, device flow ni pide al usuario que
pegue un PAT.

#### Flujo OAuth

1. `POST /github/oauth/start` exige sesión SPanel y permiso
   `apps.github.connect`.
2. El backend crea `state` aleatorio y PKCE `S256`; guarda state, user ID,
   redirect interno y verifier en Redis con TTL máximo 10 minutos y uso único.
3. Devuelve la URL exacta `https://github.com/login/oauth/authorize` con
   `client_id`, callback exacto, `state`, challenge y scopes.
4. GitHub vuelve al callback público con `code` + `state`. El callback no confía
   en cookies ni bearer del browser: consume atómicamente el state Redis y
   recupera allí la identidad SPanel original.
5. Intercambia el code con timeout corto, `Accept: application/json`, client
   secret y PKCE verifier; luego consulta `GET /user` con el token para fijar la
   identidad GitHub real.
6. Cifra access/refresh tokens antes del commit y redirige a
   `/app/apps?github=connected`; token, code y state nunca aparecen en esa URL.

Se solicitan `repo offline_access`: `repo` es necesario para repositorios
privados y `offline_access` habilita access tokens expirables/refresh tokens.
La UI explica que un OAuth App concede acceso amplio a los repositorios que el
usuario puede leer; selección fine-grained por repositorio mediante GitHub App
queda para otra A.SPEC.

Callback exacto HTTPS en producción, sin wildcard. Redis real es obligatorio
para state one-time entre web workers; Memory backend responde 503 antes de
redirigir. Cancelación, state inválido/consumido/expirado o intercambio fallido
no crean ni reemplazan una conexión existente.

#### Cifrado y refresh

Access y refresh token se cifran con AES-256-GCM usando una clave dedicada de
Apps montada desde archivo secreto en app y worker. No se deriva de
`SYSTUTOR_JWT_SECRET_KEY`. AAD liga ciphertext con provider, user ID y versión;
una alteración falla cerrado. La DB conserva nonce, ciphertext/tag y key
version, nunca plaintext.

Si GitHub devuelve expiración, el worker refresca bajo lock de la fila antes de
clonar y rota access + refresh token en una sola transacción. `bad_refresh_token`,
revocación o scopes insuficientes marcan conexión `expired/revoked` y la
operación falla con reconnect requerido. Nunca se reintenta con otra conexión o
credencial global.

`POST /github/disconnect` intenta revocación remota con timeout acotado y siempre
elimina localmente los ciphertexts. Aplicaciones existentes permanecen, pero no
pueden inspeccionar/redesplegar hasta reconectar su fuente. No se borran
deployments ni se detienen containers por desconectar OAuth.

El client ID es configuración pública. Client secret y encryption key se montan
como secret files: el web necesita ambos para callback/cifrado y el worker ambos
para refresh/descifrado. No se escriben en `.env.docker`, imagen ni repositorio.

#### Uso por el worker

El MVP acepta sólo slugs `owner/repo`, no URLs arbitrarias. Al crear o
reconectar una fuente, la aplicación guarda `source_connection_user_id` del
actor. El worker revalida usuario activo, pertenencia al tenant y conexión,
descifra el token just-in-time y lo expone como `GH_TOKEN` únicamente al proceso
`gh repo clone --single-branch`. `GH_PROMPT_DISABLED=1` impide fallback
interactivo. Después elimina el token del entorno efímero y memoria alcanzable.

La imagen instala `gh`. El token no viaja en mensaje Redis, operación, bundle de
variables, argv, manifiesto, logs ni respuesta.

Una inspección:

1. clona a un temporal privado;
2. descubre Compose o devuelve todos los candidatos ambiguos;
3. ejecuta `audit_project(..., non_root=True)`;
4. persiste commit, tree hash y findings estructurados;
5. elimina el checkout temporal;
6. termina en `succeeded` aunque existan findings `error`: el resultado válido es
   un informe bloqueante, no un fallo técnico.

El informe conserva severidad, code, service, path, line, message y remediation.
La UI advierte que heurísticas estáticas no demuestran seguridad y que un build
ejecuta código no confiable.

### 6. Deploy y non-root obligatorio

No existe toggle para desactivar non-root. Todo deploy vuelve a clonar y auditar
la revisión efectiva; nunca confía únicamente en una inspección anterior. Si
aparece un finding `error`, termina `failed/apps_audit_blocked` antes de tocar
Docker.

El motor mantiene las capas de A.SPEC 0051:

1. `USER` final estático no-root en Dockerfiles construidos;
2. override administrado con `cap_drop: [ALL]` y
   `no-new-privileges:true`;
3. `.Config.User` no-root en imágenes;
4. usuario configurado y procesos efectivos no-root después de `up`;
5. ownership labels y manifiesto antes de lifecycle posterior.

Servicios basados en `image:` siguen permitidos; también deben superar
`.Config.User` y runtime non-root. Registry privado y autenticación de registry
quedan fuera de alcance.

El proyecto Compose se deriva del UUID de aplicación, nunca del nombre enviado.
Antes de mutar se comprueba que todos los recursos homónimos tengan labels Apps
esperados. No se admite `container_name` del repositorio.

#### Releases y redespliegue

El layout remoto es server-owned:

```text
<SPANEL_APP_REMOTE_ROOT>/<runtime_project>/
  releases/<deployment_id>/source/...
  private/<deployment_id>/app.env
  manifests/<deployment_id>.json
  current.json
```

Cada release es inmutable. El proyecto Compose permanece estable para conservar
volúmenes propios entre deployments. Un redespliegue construye primero; sólo
después sustituye servicios. Si falla después de comenzar `up`, intenta restaurar
el deployment activo anterior y verifica health/non-root. El resultado distingue
`failed` con rollback exitoso de `degraded` cuando el rollback también falla; en
ningún caso marca el nuevo deployment como activo sin verificarlo.

Esta fase conserva el deployment activo anterior y el nuevo intento. Retención
automática de historiales adicionales queda fuera de alcance; `delete` es la
operación que limpia releases privados después de retirar recursos.

### 7. Variables y secretos

El deploy acepta una lista acotada `{name, value}`:

- nombres con regex de variable de entorno;
- sin duplicados;
- valores string sin NUL, CR ni LF en esta fase;
- límites por valor, cantidad y tamaño total;
- nunca se aceptan paths de env enviados por browser.

El proceso web escribe un bundle privado bajo un volumen compartido
`apps_state/pending/<operation_id>.env`, directorios 0700 y archivo 0600, antes
de encolar. DB/Redis guardan sólo nombres y fingerprint, nunca valores. El
worker mueve el bundle al release por canal stdin/tar, confirma 0600 y elimina
el pending local en `finally`.

Si enqueue falla, el bundle se elimina. Si el worker cae, reconciliación elimina
pending de operaciones terminales/interrumpidas. Respuestas, exceptions,
auditoría, logs y manifests se prueban con canarios negativos. La UI no guarda
valores en React Query, localStorage ni sessionStorage y limpia el formulario al
aceptarse el job.

### 8. Ingress y dominio

El repositorio no puede declarar la red externa del proxy. El override confiable
de SPanel conecta únicamente `ingress_service` a la red configurada por
plataforma. El resto de servicios permanece sólo en redes del proyecto. El
destino publicado es el nombre real del único contenedor Compose de ese servicio,
resuelto por el worker después de `up`; nunca un nombre enviado por el browser.

El request selecciona un servicio existente y un puerto `1..65535`; no puede
seleccionar container, network ni URL. El worker prueba conectividad al
puerto después de health.

Ampliar de forma compatible `SiteDomainPublisher.publish` para aceptar
`port: int = 80`; Hosting continúa usando 80 sin cambios de conducta. Domains
renderiza `http://<container>:<port>`. Al ser superficie pública `systutor.*`,
se actualiza su README y sus tests de contrato.

Apps registra un `site provider` mediante `domain_ownership`, igual que Hosting,
sin importar modelos de Domains. El provider expone el nombre de contenedor ya
resuelto y almacenado en el manifest del deployment activo. El dominio:

- se normaliza y reclama para el tenant mediante contratos existentes;
- se publica sólo después de health + non-root + probe de puerto;
- se despublica después de un stop/down exitoso;
- se republica después de start/restart exitoso;
- conserva la ruta anterior hasta que el nuevo deployment esté sano;
- ante fallo de publicación, el deployment no se activa y se aplica rollback.

### 9. API

Base: `/api/v1/plugins/apps`.

```http
GET  /github/connection                    -> metadata, nunca token
POST /github/oauth/start                   -> authorization_url
GET  /github/oauth/callback?code&state     -> redirect frontend
POST /github/disconnect                    -> 204
POST /applications                         -> 202 application + inspect operation
GET  /applications                         -> 200 lista DB tenant-scoped
GET  /applications/{application_id}        -> 200 detalle DB
POST /applications/{application_id}/source/reconnect
POST /applications/{application_id}/inspect
POST /applications/{application_id}/deployments
GET  /applications/{application_id}/deployments
POST /applications/{application_id}/actions/{refresh|logs|start|stop|restart|down}
DELETE /applications/{application_id}      -> 202 delete operation
GET  /operations/{operation_id}            -> 200 snapshot DB tenant-scoped
```

Los POST/DELETE async responden con:

```json
{
  "operation_id": "uuid",
  "application_id": "uuid",
  "kind": "deploy",
  "status": "queued",
  "poll_url": "/api/v1/plugins/apps/operations/uuid"
}
```

No hay endpoint que acepte project name, container ID, release path, Docker
argv, host SSH o manifest. Los errores públicos usan códigos estables y no
incluyen stdout/stderr externo.

Ningún endpoint devuelve access token, refresh token, ciphertext, nonce, OAuth
code, verifier, client secret ni encryption key. La metadata de conexión se
limita a login/avatar, scopes, estado y timestamps.

`logs` crea un job que guarda como resultado sólo las últimas N líneas ya
redactadas y con límite de bytes. Los GET nunca abren un stream Docker.

### 10. RBAC, tenancy y auditoría

Permisos:

- `apps.github.connect`: conectar/desconectar la cuenta GitHub del usuario
  actual; nunca administrar la conexión de otro usuario;
- `apps.applications.read`: listar aplicación, deployments, operaciones e
  informes propios;
- `apps.applications.manage`: refresh, logs, start, stop y restart propios;
- `apps.applications.create`: crear/cambiar origen e inspeccionar con la conexión
  personal dentro del tenant;
- `apps.deployments.create`: desplegar/redesplegar, platform-only;
- `apps.applications.delete`: down/delete/purge, platform-only.

El manifest entrega `github.connect`, `read`, `manage` y `applications.create`
al tenant admin; deploy/delete se declaran `platform_permissions`. Backend exige
además `TenantContext.is_platform`/`core.platform.admin` para estas últimas:
ocultar un botón no sustituye autorización.

La restricción es intencional: non-root protege el runtime, pero un Docker build
sigue ejecutando código no confiable. Builds para tenants no-plataforma exigen
otra A.SPEC con sandbox, cuotas y política de red.

OAuth es por usuario aunque deploy sea inicialmente platform-only: un tenant
admin conecta su cuenta, registra e inspecciona su fuente; un operador de
plataforma autoriza el deploy de esa aplicación sin sustituir la identidad de
fuente ni recibir el token. La conexión OAuth no concede por sí sola permiso de
build/deploy.

Cada enqueue y terminación registra audit sin secretos:

- `apps.application.created`;
- `apps.github.connected|disconnected|refresh_failed`;
- `apps.operation.queued`;
- `apps.inspect.completed|failed`;
- `apps.deploy.completed|failed`;
- `apps.lifecycle.completed|failed`;
- `apps.application.deleted|delete_failed`.

La terminación conserva actor original, tenant, operation/deployment IDs,
commit, resultado y código; no findings completos, variables, paths ni logs.

### 11. Frontend

Registrar ruta `/app/apps`, navegación **Apps** y permiso read. Inspirarse en
los patrones de Hosting —cards, diálogos, React Query, toasts y gates RBAC— sin
copiar componentes WordPress.

Flujo:

1. Cabecera **GitHub**: muestra desconectado o avatar/login conectado. Botón
   **Conectar GitHub** abre la authorization URL en la misma pestaña; botón
   **Desconectar** exige confirmación y nunca muestra el token.
2. **Nueva aplicación**: exige conexión válida y solicita nombre, `owner/repo`,
   rama y Compose opcional. No ofrece campo PAT/token.
3. La creación devuelve 202; se muestra fase `queued/cloning/auditing` y se hace
   polling cada 2 s hasta estado terminal.
4. **Auditoría**: findings agrupados por severidad con archivo/línea,
   explicación y remediación. Ambigüedad permite seleccionar Compose y lanzar
   una inspección nueva.
5. **Configurar deploy**: variables privadas, servicio, puerto y dominio.
6. **Desplegar**: cierre inmediato del confirm, `toast.promise` ligado al polling
   y vista de fases; no se mantiene el request abierto.
7. **Detalle**: overview, deployments, auditoría, operaciones y acciones de
   lifecycle. Estado visible procede de DB/último refresh.
8. **Logs**: solicitar job, poll y mostrar resultado redactado; no streaming en
   esta fase.

Tras volver del callback, la UI elimina `github=connected|error` de la URL con
`replaceState`, refresca metadata y muestra toast. Nunca lee code/state/token.

Deploy/delete sólo se muestran con doble gate de permiso Apps y
`core.platform.admin`; crear requiere conexión y `apps.applications.create`. Un
polling terminal se detiene y refetch de aplicación ocurre una sola vez.

### 12. Empaquetado y operación

- `Dockerfile` instala `gh` y empaqueta el plugin; no necesita una segunda copia
  del motor bajo `/app/scripts`.
- `docker-compose.yml` añade volumen privado `apps_state` compartido entre app y
  worker y un servicio worker Apps con la misma imagen/config DB/Redis.
- App y worker montan read-only la clave de cifrado; app monta el client secret
  para callback y worker para refresh. Ninguno monta tokens de usuario: éstos
  existen únicamente cifrados en DB.
- `SPANEL_APPS_GITHUB_CLIENT_ID` es público. `.env.docker.example` documenta
  paths externos para client secret y encryption key; sus valores reales no se
  escriben en `.env.docker`, imagen ni repositorio.
- La dependencia criptográfica autenticada queda declarada y pinneada en el
  entorno Python; no se implementa criptografía casera.
- El worker usa `--processes 1 --threads 1 --queues apps`.
- Añadir variables no secretas y placeholders a `.env.docker.example`, nunca
  valores reales.
- Health del panel no se declara sano para mutaciones Apps cuando el dispatcher
  no está disponible; las demás funciones del panel pueden seguir operativas.

## OUT OF SCOPE

- Git genérico, GitLab, Bitbucket, webhooks o auto-deploy por push.
- GitHub App fine-grained, selección de repositorios durante instalación o
  múltiples cuentas GitHub por usuario; esta fase usa un OAuth App y una cuenta.
- PAT pegado manualmente, `gh auth login` y device flow.
- Dar build/deploy a tenant admins no-plataforma.
- Registry privado y credenciales de imágenes.
- Docker Rootless daemon, Kubernetes, Swarm o un segundo daemon.
- Reparar automáticamente Dockerfiles/Compose.
- Terminal web, shell/exec arbitrario o logs en streaming.
- Cancelar un build/deploy en mitad de una mutación.
- Escalado de servicios, métricas, cuotas, límites CPU/RAM y cron.
- Backups de volúmenes de Apps.
- Variables multilinea, archivos secretos o edición/revelado de secretos ya
  desplegados.
- Múltiples dominios o múltiples endpoints públicos por aplicación.
- Rollback elegido manualmente desde UI; sólo rollback automático al deployment
  activo anterior durante fallo de redespliegue.

## CONTRACT

Precondición de enqueue: actor autorizado, aplicación propia, idempotency key
válida, sin operación activa y dispatcher disponible; inspect/deploy exigen
además conexión OAuth de fuente válida. Lifecycle sobre un deployment existente
no depende de que GitHub siga conectado. Ningún request de operación runtime
hace I/O GitHub/Docker/SSH; callback y revocación OAuth son las excepciones
explícitas y sólo intercambian/validan/revocan credenciales con timeouts
acotados, sin Docker ni SSH.

Precondición de deploy: revisión re-auditada sin errores, env privado válido,
ingress existente, dominio perteneciente al tenant, transporte disponible y
namespace runtime libre o completamente propio.

Postcondición de éxito: operation/deployment terminales persistidos; todos los
servicios running/healthy, imágenes y procesos efectivos non-root, recursos con
labels propios, manifest sin secretos y dominio apuntando al contenedor/puerto
sano.

Postcondición de fallo: nunca se activa un deployment no verificado; antes de
`up` no cambia el runtime; después de comenzar `up` se restaura el deployment
anterior o la aplicación queda explícitamente `degraded`, nunca falsamente
`running`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Ningún request de operación runtime ejecuta gh, Docker, SSH, sleep de health ni subprocess; sólo endpoints OAuth hacen HTTP acotado a GitHub.
    proof: tests de rutas con spies en cero llamadas externas; POST runtime devuelve 202 y GET consulta DB; OAuth mockea exclusivamente token exchange, GET /user y revocación.
  - id: I2
    statement: CLI y worker usan una sola implementación de auditoría/deploy; no existen dos copias de las reglas de A.SPEC 0051.
    proof: imports directos al runtime compartido, tests de paridad CLI/API y grep de códigos de findings definido en un solo módulo.
  - id: I3
    statement: Todo mensaje de cola contiene únicamente operation_id y es seguro ante entrega duplicada.
    proof: fake broker inspecciona payload exacto; dos ejecuciones concurrentes producen un solo claim y una sola llamada mutante.
  - id: I4
    statement: Una aplicación tiene como máximo una operación activa y esta fase ejecuta como máximo un build/deploy global.
    proof: compare-and-set/constraint DB, tests concurrentes y worker configurado 1x1.
  - id: I5
    statement: Toda ejecución real exige non-root estático, de imagen, configurado y efectivo, sin fallback root.
    proof: suite A.SPEC 0051 más tests del worker para fallo en cada una de las tres capas antes de activar deployment.
  - id: I6
    statement: Findings, ownership y rechazo de escapes de A.SPEC 0051 son idénticos por CLI y módulo.
    proof: fixtures compartidos comparan resultados estructurados y CLI JSON; suite completa anterior permanece verde.
  - id: I7
    statement: Ningún secreto en plaintext aparece en DB, Redis, argv, manifest, audit, logs, errores, respuestas ni caché frontend; DB sólo contiene ciphertext autenticado.
    proof: canarios únicos recorren OAuth, refresh y operaciones exitosa/fallida/reconciliada; asserts negativos y prueba de tamper AES-GCM.
  - id: I8
    statement: Un tenant nunca ve ni opera aplicaciones, operaciones o deployments ajenos.
    proof: tests list/detail/poll/actions cross-tenant devuelven 404 y broker/runner quedan sin llamadas.
  - id: I9
    statement: Crear/inspeccionar requieren conexión personal y permiso Apps; desplegar/eliminar exigen además core.platform.admin en backend.
    proof: matriz RBAC cubre usuario sin create, tenant admin creador y tenant admin sin plataforma intentando deploy/delete = 403 y cero mutación Docker.
  - id: I10
    statement: El dominio se publica sólo hacia el contenedor server-resolved de un servicio sano y un puerto validado, conectado por override de plataforma.
    proof: tests de override, contrato publisher con puerto y E2E que resuelve HTTPS; repo/browser no pueden elegir network ni container.
  - id: I11
    statement: Sólo recursos con manifest y labels del runtime_project pueden iniciar, parar, reemplazarse o eliminarse.
    proof: fixtures con proyecto homónimo ajeno bloquean todas las acciones con cero mutación.
  - id: I12
    statement: Un deploy fallido nunca reemplaza active_deployment_id; un fallo post-up restaura el anterior o marca degraded.
    proof: tests por fase antes/después de up y rollback exitoso/fallido verifican DB, manifest, containers y dominio.
  - id: I13
    statement: Idempotency-Key igual no crea dos operaciones; reutilizarla con otro payload no confunde intenciones.
    proof: tests secuenciales/concurrentes retornan mismo ID para mismo hash y 409 para hash diferente.
  - id: I14
    statement: No se sostiene una transacción DB durante I/O externo y todo job actualiza heartbeat/fase de forma persistente.
    proof: session spies verifican commit/close antes de runner; tests de reconciliación queued y stale running.
  - id: I15
    statement: La UI nunca espera el build en la mutación HTTP y detiene polling al alcanzar estado terminal.
    proof: frontend build y tests/hooks con 202, fases, terminal success/failure y un solo refetch final.
  - id: I16
    statement: Una conexión GitHub pertenece a exactamente un usuario SPanel y OAuth resiste CSRF, replay y sustitución de identidad.
    proof: tests state one-time/TTL, PKCE S256, callback sin bearer, GET /user obligatorio, conexión ajena inaccesible y reconnect explícito de fuente.
```

## VERIFICATION

GENERATOR ejecuta, en este orden:

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

# Regresión completa del motor probado y compatibilidad CLI
.venv/bin/python -m pytest \
  scripts/tests/test_app_deploy.py \
  scripts/tests/test_app_deploy_live.py -q

# Plugin Apps: runtime compartido, DB, cola, API, RBAC, secretos y dominios
.venv/bin/python -m pytest \
  vendor/systutor-core/plugins/apps/backend/tests -q

# Contratos públicos tocados y runtime de plugins
.venv/bin/python -m pytest \
  vendor/systutor-core/plugins/domains/backend/tests/test_contract.py \
  vendor/systutor-core/plugins/domains/backend/tests/test_primary_publisher.py \
  vendor/systutor-core/plugins/domains/backend/tests/test_proxy_files.py \
  vendor/systutor-core/tests/test_plugin_registry.py \
  vendor/systutor-core/tests/test_plugin_permission_runtime_sync.py -q

.venv/bin/python -m ruff check \
  scripts/app_deploy.py \
  scripts/tests/test_app_deploy.py \
  scripts/tests/test_app_deploy_live.py \
  vendor/systutor-core/plugins/apps \
  vendor/systutor-core/plugins/domains/backend/publisher.py \
  vendor/systutor-core/plugins/domains/backend/proxy/operations.py \
  vendor/systutor-core/plugins/domains/backend/proxy/core.py \
  vendor/systutor-core/src/systutor/contracts/site_domain_publisher.py

cd apps/web && npm run build

cd /data/data/com.termux/files/home/Proyectos/mailadmin
docker compose config >/dev/null
git diff --check
```

`SPANEL_LIVE_TESTS=0` debe producir skip explícito en tests live. Después de la
suite offline, el agente principal ejecuta E2E con
`luc444s/software-de-gestion-para-editores`, rama
`feature/workflow-diseno`:

1. creación devuelve 202 rápidamente y el request web no clona;
2. worker pasa `queued -> running -> succeeded` para inspección;
3. deploy pasa por fases persistidas y deja frontend/backend/database sanos;
4. los tres procesos efectivos son non-root;
5. dominio resuelve al frontend por su puerto no privilegiado;
6. refresh/logs/stop/start/restart operan como jobs y respetan polling;
7. un segundo deploy de fixture controlado valida cambio de deployment activo;
8. un fixture fallido valida rollback al deployment anterior;
9. al terminar se conserva la aplicación objetivo running, salvo autorización
   separada para retirarla.

## ROLLBACK

1. Deshabilitar el plugin `apps` para impedir nuevos enqueue.
2. Esperar que no existan operaciones running; no matar un build/up sin
   inspección manual.
3. Para cada aplicación, usar su manifest y operación `down` antes de revertir
   código. No limpiar por prefijo global.
4. Revertir frontend/plugin/worker y luego el refactor CLI.
5. Las tablas y volúmenes pueden conservarse como evidencia; downgrade de
   migración no elimina recursos Docker ni datos automáticamente.
6. El contrato publisher mantiene default `port=80`, por lo que Hosting sigue
   funcionando durante rollback parcial.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0052-async-first-apps-module.md
    - scripts/app_deploy.py
    - scripts/tests/test_app_deploy.py
    - scripts/tests/test_app_deploy_live.py
    - vendor/systutor-core/plugins/apps/**
    - vendor/systutor-core/plugins/domains/backend/publisher.py
    - vendor/systutor-core/plugins/domains/backend/proxy/core.py
    - vendor/systutor-core/plugins/domains/backend/proxy/operations.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_contract.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_primary_publisher.py
    - vendor/systutor-core/plugins/domains/backend/tests/test_proxy_files.py
    - vendor/systutor-core/src/systutor/contracts/site_domain_publisher.py
    - vendor/systutor-core/README.md
    - vendor/systutor-core/pyproject.toml
    - Dockerfile
    - docker-compose.yml
    - .env.docker.example
  prohibited:
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/plugins/tenant/**
    - apps/web/src/features/auth/**
    - .env
    - .env.docker
    - secrets reales o tokens versionados
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - nuevo plugin, tablas y frontend Apps
    - worker Dramatiq y Redis queue apps
    - motor compartido de auditoría/deploy
    - Dockerfile, compose y volumen privado apps_state
    - contrato de publicación de dominio con puerto
  indirect:
    - daemon Docker y host SSH existentes
    - red proxy y archivos dinámicos de Traefik
    - PostgreSQL, Redis y audit log
    - GitHub OAuth App y tokens cifrados de usuarios
  must_not_affect:
    - contenedores y volúmenes Hosting/WordPress/static
    - cuentas y certificados Mail
    - Filemanager y sus FileTargets
    - permisos o visibilidad cross-tenant existentes
    - ruta de Hosting, que conserva puerto default 80
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0027
    - A.SPEC 0037
    - A.SPEC 0045
    - A.SPEC 0051
  must_compose_with:
    - plugin runtime y migraciones por plugin
    - TenantContext y RBAC sin is_superadmin
    - TaskDispatcher/Dramatiq/Redis existentes
    - GitHub OAuth web flow con state one-time y PKCE S256
    - domain namespace, ownership y publisher contracts
    - proxy network administrada por Domains
  systemic_invariants:
    - kernel sin lógica de negocio Apps
    - plugins se comunican mediante contratos, no imports mutuos
    - toda mutación significativa deja audit
    - secretos plaintext nunca se persisten en superficies observables
  composition_checks:
    - suites Apps + runner + Domains + plugin runtime
    - frontend build
    - docker compose config
    - E2E GitHub/SSH/proxy con aplicación real
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: async desde el primer request y un solo motor de seguridad
  runtime_web_requests_external_io: forbidden
  oauth_endpoints_external_io: github_token_exchange_user_and_revocation_only
  queue_payload: operation_id_only
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  business_logic_location: vendor/systutor-core/plugins/apps
  kernel_changes: contracts_only
  direct_plugin_to_plugin_imports: forbidden
  github_auth: per_user_oauth_only
  oauth_token_storage: authenticated_encryption
```

## Traceability

- Requirement: terminado el runner primitivo, crear el módulo Apps tomando
  inspiración de módulos existentes sin duplicar el motor.
- Decision: “debe de ser async first”.
- Decision: OAuth GitHub por usuario con botón “Conectar GitHub”.
- OAuth reference: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- gh token injection reference: https://cli.github.com/manual/gh_help_environment
- Source primitive: A.SPEC 0051, commit `c1f7539`.
- Validation target: `luc444s/software-de-gestion-para-editores`, rama
  `feature/workflow-diseno`.
- owner: luc444s
- approver: luc444s

## Definition of Done

- [ ] CLI y worker consumen un motor compartido y la suite 0051 sigue verde.
- [ ] Plugin Apps descubre, migra, sincroniza permisos y aparece en navegación.
- [ ] Cada usuario conecta/desconecta GitHub por OAuth state+PKCE y los tokens
      permanecen cifrados, renovables y nunca visibles.
- [ ] Todo POST runtime devuelve 202 y cero I/O externo ocurre en web requests.
- [ ] Worker durable procesa IDs, serializa builds y reconcilia jobs huérfanos.
- [ ] Tenant/RBAC, idempotencia y exclusión concurrente están probados.
- [ ] Findings completos se muestran y cualquier error bloquea deploy.
- [ ] Deploy, imagen y procesos efectivos son non-root sin toggle ni fallback.
- [ ] Secrets pasan sólo por storage 0600 y canarios no aparecen en superficies.
- [ ] Ingress único publica dominio al servicio/puerto sano mediante contrato.
- [ ] Logs, refresh y lifecycle son jobs asíncronos visibles por polling.
- [ ] Redespliegue activa sólo release verificado y rollback está probado.
- [ ] Ruff, pytest, frontend build, compose config y diff-check pasan.
- [ ] E2E real conserva frontend/backend/database sanos y accesibles por dominio.
