# A.SPEC 0052B — Backend Apps async-first, OAuth y worker

> `risk: high` — añade OAuth por usuario, tokens cifrados, jobs durables y
> builds/deploys Docker no confiables. Todo runtime I/O se ejecuta en un worker
> serializado y conserva el motor de 0052A/0051.

## WHY

Con el motor único extraído, SPanel necesita una superficie tenant-aware que no
bloquee requests durante clone/build/health. También necesita acceder a repos
privados sin token global: cada usuario conecta GitHub y la aplicación conserva
la identidad que autorizó su fuente.

## WHAT

Crear el backend completo del plugin `apps`: manifest, permisos, migraciones,
OAuth GitHub por usuario, operaciones persistentes, worker Dramatiq, inspect,
deploy, lifecycle, secrets privados, ingress y contrato de dominio con puerto.

Esta entrega no añade la página React; su API queda completa y probada para
0052C. Como el contrato de manifests exige un `frontend_entrypoint` real, añade
únicamente `frontend/register.ts` como registro vacío (`routes`, `navigation` y
`widgets` sin entradas). No expone UI ni importa componentes; 0052C reemplaza
ese placeholder por el registro y página reales.

## SCOPE

### 1. Persistencia

Crear tablas propias del plugin:

#### `apps_github_connections`

- una fila por `user_id`;
- GitHub user id, login, avatar, scopes y estado no secretos;
- access/refresh token cifrados por separado;
- expiraciones, nonce/tag y versión de clave;
- timestamps de conexión y último uso.

#### `apps_applications`

- UUID, tenant, name/slug tenant-scoped;
- `repository` `owner/repo`, branch y compose opcional;
- `source_connection_user_id` explícito;
- runtime project server-generated y globalmente único;
- domain, ingress service/port y status;
- active deployment/operation;
- soft-delete y timestamps.

#### `apps_deployments`

Intentos inmutables con application/operation, repo/branch/commit/tree hash,
Compose, ingress, estado, release, manifest sin secretos y error público.
`active_deployment_id` cambia sólo después de health, non-root y publicación.

#### `apps_operations`

- kinds `inspect`, `deploy`, `refresh`, `logs`, `start`, `stop`, `restart`,
  `down`, `delete`;
- status `queued`, `running`, `succeeded`, `failed`, `interrupted`;
- phase, actor, tenant, correlation/request id;
- idempotency key + request hash;
- message id, heartbeat, attempt, report/result/error JSON.

DB es la fuente de verdad; memoria del web worker no lo es.

### 2. OAuth GitHub por usuario

Implementar botón-ready OAuth App web flow:

1. `POST /github/oauth/start`, sesión + `apps.github.connect`;
2. state aleatorio y PKCE S256 en Redis real, TTL <= 10 min, one-time, ligado a
   user ID y redirect interno;
3. authorization URL exacta de GitHub;
4. callback público consume state atómicamente, sin confiar en cookie/bearer;
5. token exchange JSON con client secret, verifier y timeout;
6. `GET /user` obligatorio para fijar identidad real;
7. cifrado antes de commit y redirect limpio `/app/apps?github=connected`.

Scopes: `repo offline_access`. Callback HTTPS exacto en producción, sin wildcard.
Memory cache responde 503 antes de iniciar OAuth.

Access/refresh token se cifran con AES-256-GCM y clave dedicada, nunca con la
JWT key. AAD liga provider, user y key version. Client secret y encryption key
son secret files montados read-only; `cryptography` es dependencia declarada.

Refresh ocurre bajo row lock y rota ambos tokens atómicamente. Revocación,
`bad_refresh_token`, scopes insuficientes o tamper marcan reconnect requerido.
Disconnect intenta revocación remota, siempre elimina ciphertext local y no
detiene deployments existentes.

Una aplicación usa siempre `source_connection_user_id`. Otro operador puede
aprobar deploy, pero no sustituir ni ver el token. Si el dueño queda inactivo,
sale del tenant o desconecta GitHub, inspect/deploy fallan hasta `source/reconnect`
explícito por otro usuario autorizado. Lifecycle ya desplegado no requiere OAuth.

El worker descifra just-in-time y pasa `GH_TOKEN` sólo al child `gh`, con
`GH_PROMPT_DISABLED=1`; nunca va en Redis, DB plaintext, argv, manifest o logs.

Referencias normativas:

- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
- https://cli.github.com/manual/gh_help_environment

### 3. Async-first durable

Añadir worker Dramatiq dedicado `apps`, un proceso y un hilo. Cada mensaje
contiene sólo `operation_id`.

Un request runtime:

1. autoriza tenant/RBAC;
2. valida sin I/O externo;
3. persiste operation `queued` y adquiere `active_operation_id` atómicamente;
4. commit;
5. enqueue;
6. devuelve 202.

Si dispatcher falla: operation terminal `apps_queue_unavailable`, lock liberado,
bundle pending eliminado y HTTP 503. Nunca fallback inline/thread.

Actor hace claim `queued -> running`; duplicado sale sin repetir mutación.
`max_retries=0`. Worker boot reconcilia bajo lock:

- queued sin message id se reencola una vez;
- running con heartbeat vencido pasa a interrupted;
- no adopta, baja ni elimina recursos durante recovery.

Fases/heartbeat usan transacciones cortas; DB se cierra durante GitHub, Docker,
SSH y health. Una sola operación por app y lock global para build/deploy.

Todo POST async exige `Idempotency-Key`: misma key/hash devuelve mismo job;
misma key con body distinto => 409. Contención => 409 con operation activa.

### 4. Inspección y deploy

Tenant con `apps.applications.create` conecta GitHub, registra e inspecciona.
Inspect clona temporal, descubre Compose, ejecuta
`audit_project(non_root=True)`, persiste revisión/findings y limpia checkout.
Findings de seguridad terminan job `succeeded` con reporte bloqueante; un fallo
técnico termina `failed`.

Deploy exige `apps.deployments.create` + `core.platform.admin`, vuelve a clonar y
auditar la revisión efectiva y bloquea antes de Docker ante cualquier error.
Non-root no tiene toggle:

- USER final estático;
- override `cap_drop: ALL` + `no-new-privileges`;
- `.Config.User` de imagen;
- usuario configurado y procesos efectivos;
- ownership labels/manifest.

Servicios `image:` son válidos si pasan las mismas verificaciones. Registry
privado queda fuera.

### 5. Releases, lifecycle y secretos

Layout server-owned:

```text
<remote_root>/<runtime_project>/
  releases/<deployment_id>/source/
  private/<deployment_id>/app.env
  manifests/<deployment_id>.json
  current.json
```

Release inmutable y Compose project estable. Redespliegue construye antes de
sustituir. Fallo post-up restaura el deployment activo anterior y lo verifica;
si rollback falla, application queda `degraded`, nunca falsamente running.

Variables llegan como lista nombre/valor acotada, sin duplicados/NUL/CR/LF. Web
escribe `apps_state/pending/<operation>.env` 0600 bajo dirs 0700; DB/Redis sólo
guardan nombres/fingerprint. Worker transfiere por stdin/tar, confirma 0600 y
elimina pending en finally/recovery. Canarios no aparecen en superficies.

Refresh, logs, start, stop, restart, down y delete también son jobs. Logs guardan
sólo N líneas redactadas y bytes acotados. Down conserva volúmenes; purge/delete
requiere plataforma, confirmación exacta, manifest y labels propios.

### 6. Ingress y Domains

El repo no declara la red externa proxy. Override confiable conecta sólo el
servicio elegido. Browser elige service existente + port 1..65535; nunca
container/network/URL. Worker resuelve el único nombre real de container,
verifica health/non-root y prueba puerto.

Extender compatible `SiteDomainPublisher.publish(container, domain, port=80)`.
Hosting conserva 80. Domains renderiza `http://<container>:<port>`.

Apps registra site provider por contrato, sin imports directos a Domains.
Publica después de verificación, despublica tras stop/down, republica tras
start/restart y conserva ruta anterior hasta que redespliegue nuevo esté sano.
Fallo de publicación impide activar deployment y dispara rollback.

### 7. API

Base `/api/v1/plugins/apps`:

```http
GET  /github/connection
POST /github/oauth/start
GET  /github/oauth/callback?code&state
POST /github/disconnect
POST /applications
GET  /applications
GET  /applications/{id}
POST /applications/{id}/source/reconnect
POST /applications/{id}/inspect
POST /applications/{id}/deployments
GET  /applications/{id}/deployments
POST /applications/{id}/actions/{refresh|logs|start|stop|restart|down}
DELETE /applications/{id}
GET  /operations/{operation_id}
```

POST runtime devuelve 202 con operation/application/kind/status/poll_url. GET
lee DB y nunca Docker/SSH/GitHub. OAuth exchange/revocation son únicas excepciones
HTTP externas, con timeout y sin Docker/SSH.

Ningún endpoint acepta project/container/release/argv/host/manifest ni devuelve
token, ciphertext, nonce, code, verifier, client secret o encryption key.

### 8. RBAC y audit

Permisos:

- `apps.github.connect` — conexión propia;
- `apps.applications.read`;
- `apps.applications.manage` — refresh/logs/start/stop/restart;
- `apps.applications.create` — registrar/reconectar/inspeccionar;
- `apps.deployments.create` — platform-only;
- `apps.applications.delete` — platform-only.

Tenant admin recibe connect/read/manage/create. Deploy/delete exigen además
`core.platform.admin` en backend.

Auditar connect/disconnect/refresh failure, application create, operation queue,
inspect/deploy/lifecycle completion/failure y delete. Guardar IDs, commit,
actor/source owner y códigos, nunca findings completos, token, env, paths o logs.

### 9. Packaging

- Dockerfile instala `gh`, dependencia crypto y plugin.
- `frontend/register.ts` satisface el manifest con registro vacío y sin UI.
- Compose añade `apps_state` y worker `--processes 1 --threads 1 --queues apps`.
- App+worker montan encryption key; app monta client secret para exchange y
  worker para refresh.
- Client ID es público; `.env.docker.example` documenta paths, no secretos.
- Dispatcher no disponible degrada sólo mutaciones Apps.

## OUT OF SCOPE

- Frontend React, rutas y navegación; corresponde a 0052C. El único archivo
  frontend permitido aquí es el entrypoint vacío exigido por el manifest.
- GitHub App fine-grained, multi-cuenta, PAT, device flow o `gh auth login`.
- Git genérico/GitLab/Bitbucket, webhooks y auto-deploy.
- Build/deploy por tenant no-plataforma.
- Registry privado, terminal/exec, logs streaming, cancelación.
- Métricas, cuotas, escalado, backups y rollback manual.
- Múltiples dominios/endpoints públicos.

## INVARIANTS

```yaml
invariants:
  - id: B1
    statement: Requests runtime sólo autorizan, persisten, encolan y devuelven; cero gh/Docker/SSH.
    proof: route spies; POST 202 y GET DB-only; OAuth mock limitado a exchange/user/revoke.
  - id: B2
    statement: Queue payload contiene sólo operation_id y duplicados no repiten mutación.
    proof: fake broker + claim concurrente con una llamada runner.
  - id: B3
    statement: Una app tiene una operación activa y existe como máximo un build/deploy global.
    proof: CAS/constraint, concurrencia y worker 1x1.
  - id: B4
    statement: OAuth personal resiste CSRF, replay y sustitución de identidad.
    proof: state one-time/TTL, PKCE, callback sin bearer, GET /user y conexión ajena 404.
  - id: B5
    statement: Tokens/env nunca aparecen plaintext fuera de memoria efímera necesaria.
    proof: canarios OAuth/refresh/deploy, DB sólo ciphertext AES-GCM y tamper fails closed.
  - id: B6
    statement: Worker usa exactamente la source_connection_user_id y nunca fallback global.
    proof: usuario desconectado/inactivo/ajeno falla antes de gh; child env contiene token correcto solamente.
  - id: B7
    statement: Todo deploy exige las tres capas non-root y procesos efectivos UID != 0.
    proof: tests por capa y suite 0051 compartida.
  - id: B8
    statement: Tenant/RBAC aíslan conexiones, apps, jobs y deployments.
    proof: matriz cross-user/cross-tenant 404/403 con broker/runner cero.
  - id: B9
    statement: Sólo manifests y labels propios permiten lifecycle/delete.
    proof: proyecto homónimo ajeno bloquea toda mutación.
  - id: B10
    statement: Deployment fallido no reemplaza active; post-up rollback restaura o marca degraded.
    proof: fallos inyectados por fase y asserts DB/runtime/domain.
  - id: B11
    statement: Dominio apunta sólo a container server-resolved, sano y puerto validado.
    proof: contrato port, override/probe y browser incapaz de elegir container/network.
  - id: B12
    statement: Idempotency-Key igual/hash igual devuelve job original; hash distinto 409.
    proof: tests secuenciales y concurrentes.
  - id: B13
    statement: DB no permanece abierta durante I/O externo y jobs huérfanos terminan/reconcilian explícitamente.
    proof: session spies y tests queued/stale running.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

.venv/bin/python -m pytest scripts/tests/test_app_deploy.py -q
.venv/bin/python -m pytest vendor/systutor-core/plugins/apps/backend/tests -q
.venv/bin/python -m pytest \
  vendor/systutor-core/plugins/domains/backend/tests/test_contract.py \
  vendor/systutor-core/plugins/domains/backend/tests/test_primary_publisher.py \
  vendor/systutor-core/plugins/domains/backend/tests/test_proxy_files.py \
  vendor/systutor-core/tests/test_plugin_registry.py \
  vendor/systutor-core/tests/test_plugin_permission_runtime_sync.py -q

.venv/bin/python -m ruff check \
  scripts/app_deploy.py \
  vendor/systutor-core/plugins/apps \
  vendor/systutor-core/plugins/domains/backend/publisher.py \
  vendor/systutor-core/plugins/domains/backend/proxy/core.py \
  vendor/systutor-core/plugins/domains/backend/proxy/operations.py \
  vendor/systutor-core/src/systutor/contracts/site_domain_publisher.py

docker compose config >/dev/null
git diff --check
```

Tests usan fake GitHub/Redis/runner; no contactan GitHub real ni Docker salvo
live gate explícito.

## ROLLBACK

Deshabilitar plugin, esperar jobs terminales, bajar Apps sólo por manifest y
revertir commit 0052B. No borrar tablas/volúmenes/tokens cifrados automáticamente.
0052A continúa como CLI operativa. Publisher default 80 mantiene Hosting.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0052-async-first-apps-module.md
    - aspecs/0052b-async-apps-backend-oauth-worker.md
    - vendor/systutor-core/plugins/apps/plugin.json
    - vendor/systutor-core/plugins/apps/README.md
    - vendor/systutor-core/plugins/apps/permissions/**
    - vendor/systutor-core/plugins/apps/migrations/**
    - vendor/systutor-core/plugins/apps/backend/**
    - vendor/systutor-core/plugins/apps/frontend/register.ts
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
    - vendor/systutor-core/plugins/apps/frontend/pages/**
    - vendor/systutor-core/plugins/apps/frontend/components/**
    - vendor/systutor-core/plugins/apps/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/plugins/tenant/**
    - apps/web/src/features/auth/**
    - .env
    - .env.docker
```

## Commit obligatorio

```text
feat(apps): add async oauth deployment backend (A.SPEC 0052B)
```

Debe partir del commit 0052A verde e incluir sólo backend/infra/contratos de
0052B. API y tests quedan verdes sin depender de frontend 0052C.

## Definition of Done

- [ ] OAuth state+PKCE, cifrado, refresh, reconnect y disconnect probados.
- [ ] Plugin/migraciones/permisos cargan y sincronizan.
- [ ] Requests runtime nunca ejecutan I/O externo.
- [ ] Worker durable/idempotente/reconciliable procesa sólo operation IDs.
- [ ] Inspect tenant y deploy platform-only funcionan con identidad fuente.
- [ ] Secrets y tokens superan canarios negativos.
- [ ] Non-root, ownership, releases y rollback probados.
- [ ] Ingress/domain port y lifecycle probados.
- [ ] Suites backend, Ruff, compose config y diff-check verdes.
