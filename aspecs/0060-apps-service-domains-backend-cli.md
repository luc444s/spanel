# A.SPEC 0060 — Backend y CLI de servicios y dominios para Apps

> `risk: high` — amplía lifecycle por servicio, permite revelar secretos
> administrados y vincula dominios públicos a servicios Compose. Debe reutilizar
> Dominios; queda prohibido crear un segundo ingress, catálogo o API de rutas.

## WHY

Apps ya despliega proyectos Compose y persiste el inventario dinámico de sus
servicios, pero sus acciones lifecycle son globales y su dominio histórico se
modela como un único `domain + ingress_service + ingress_port` por aplicación.
El operador necesita administrar cada servicio y asignarle dominios ya
registrados sin escribir nombres de contenedor efímeros ni duplicar las
responsabilidades del plugin Dominios.

También necesita consultar los secretos que el propio tenant entregó a Apps,
pero una respuesta normal de detalle no puede incluirlos ni cachearlos.

## REGLA DE REUTILIZACIÓN

```text
Reutilizar > extender > crear
```

Esta spec considera existentes y autoritativas las superficies de Dominios para:

```text
GET    /api/v1/plugins/domains/available
GET    /api/v1/plugins/domains/routes
POST   /api/v1/plugins/domains/routes
GET    /api/v1/plugins/domains/routes/{route_id}
PATCH  /api/v1/plugins/domains/routes/{route_id}
DELETE /api/v1/plugins/domains/routes/{route_id}
POST   /api/v1/plugins/domains/routes/{route_id}/probe
```

La implementación debe localizar e importar sus clientes, schemas, tipos,
validadores y operaciones existentes. Si alguno no está disponible en el
checkout efectivo, se debe detener la implementación y reportar la discrepancia;
no se autoriza recrearlo en Apps ni crear una variante con otro nombre.

### Decisión de ampliación aprobada (2026-11-09)

El preflight detectó que este checkout no contenía la superficie de rutas de
Dominios declarada arriba: sólo existían dominios primarios/extra publicados en
archivos gestionados por `ProxyOperations`. El usuario autorizó explícitamente
**extender Dominios** de forma mínima para materializar esa superficie, en lugar
de simularla en Apps.

La extensión es la siguiente y es la única autorizada:

- `GET /api/v1/plugins/domains/available`: dominios registrados y visibles para
  el tenant, incluyendo publicados y reservados.
- `GET|POST /api/v1/plugins/domains/routes` y
  `GET|PATCH|DELETE /api/v1/plugins/domains/routes/{route_id}`: CRUD de rutas
  con `domain`, `path`, `internal_path`, `strip_path`, `port`, `entrypoints`,
  `https`, `middlewares` y `site_id` estable. `site_id` identifica el sitio
  resuelto por el contrato `domain_ownership`; nunca un nombre de contenedor,
  Docker ID, proyecto, red ni argv.
- `POST /api/v1/plugins/domains/routes/{route_id}/probe`: sondeo de certificado
  y ruta usando el sondeo TLS existente de Dominios.
- Persistencia mínima en Dominios para IDs estables, ownership por tenant y
  edición/eliminación. La publicación sigue usando los YAML gestionados, el
  renderer y `ProxyOperations` existentes; no se crea un segundo publisher,
  renderer, inventario ni sistema TLS.

Apps registra sus servicios como sitios resolubles por `domain_ownership`
(`site_id = <application_id>:<service>`) para que Dominios resuelva el
contenedor activo. Las rutas se reconcilian contra el sitio estable al leerlas,
probarlas y operarlas; el nombre efímero del contenedor nunca se guarda en Apps.

No crear:

- un segundo modelo o almacenamiento de rutas fuera de `plugins/domains`;
- otro publisher de Traefik;
- endpoints `/apps/domains`;
- endpoints duplicados `/domains/routes` en Apps;
- otro inventario de dominios;
- otro renderer YAML;
- otro sistema de certificados;
- un contrato público nuevo si el contrato actual ya resuelve el caso
  (`domain_ownership` resuelve el sitio estable; no se agrega otro registry).

## SCOPE

### 1. Inventario operativo por servicio

Extender el detalle existente de la aplicación y del deployment activo. Cada
entrada de `service_details` puede añadir únicamente información obtenida del
runtime real y necesaria para el modal:

```json
{
  "name": "backend",
  "image": "spanel-app-backend:latest",
  "status": "running",
  "health": "healthy",
  "ports": [8000],
  "restart_count": 0,
  "environment_names": ["DATABASE_URL", "JWT_SECRET_KEY"],
  "created_at": "2026-11-09T23:55:00Z"
}
```

Reglas:

- `name` proviene del servicio Compose persistido, no del cliente;
- `ports` son puertos internos, nunca bindings secretos del host;
- no devolver IP interna ni Docker ID salvo una necesidad demostrada;
- no devolver valores de entorno;
- servicios y contenedores son dinámicos: no asumir frontend/backend/database;
- manifiestos antiguos siguen serializando valores vacíos compatibles.

### 2. Lifecycle por servicio sin endpoints paralelos

Reutilizar el endpoint actual:

```text
POST /api/v1/plugins/apps/applications/{application_id}/actions/{action}
```

El body acepta un selector opcional:

```json
{"service": "backend"}
```

Semántica:

```text
body vacío       acción sobre toda la aplicación
service presente acción sólo sobre ese servicio Compose
```

Acciones que admiten `service`:

- `refresh`;
- `logs`;
- `start`;
- `stop`;
- `restart`.

`down` y delete siempre afectan a la aplicación completa y deben rechazar un
selector de servicio.

El backend valida el nombre contra el deployment activo antes de encolar. El
worker vuelve a validarlo contra el manifiesto y ejecuta Compose con el servicio
como argumento final. Nunca acepta container ID, container name, project,
network, ruta de release ni argv Docker proporcionados por el cliente.

Logs conserva la redacción actual y sólo devuelve salida del servicio elegido.
Refresh devuelve estado de ese servicio cuando hay selector y conserva el
resultado global actual cuando no lo hay.

### 3. Operaciones e idempotencia

Las acciones por servicio siguen el flujo existente:

```text
HTTP 202 → operation_id → worker → polling → estado terminal
```

La operación persiste de forma no secreta:

```text
subject_type = app_service
subject_id   = <application_id>:<service_name>
```

No se crean tablas ni colas nuevas si los campos existentes pueden representar
el subject. Una misma idempotency key no puede cambiar de body o servicio.

### 4. Secretos administrados

El listado normal muestra nombres y metadata, nunca valores. Se autoriza una
única superficie nueva porque revelar dentro del detalle cacheable sería
inseguro:

```text
POST /api/v1/plugins/apps/applications/{application_id}/secrets/{name}/reveal
```

Body:

```json
{"reason": "Diagnóstico solicitado por el tenant"}
```

Respuesta:

```json
{"name": "JWT_SECRET_KEY", "value": "..."}
```

Reglas obligatorias:

- sólo valores administrados y cifrados por Apps para esa aplicación;
- el nombre debe estar declarado para al menos un servicio del deployment;
- no leer indiscriminadamente `.Config.Env` de Docker;
- permiso dedicado `apps.secrets.read`;
- mismo tenant o 404 opaco;
- `reason` obligatorio y acotado;
- `Cache-Control: no-store`;
- valor ausente de logs, trazas, errores, operaciones, eventos y auditoría;
- respuesta síncrona: no guardar el valor en un resultado de job;
- auditoría `apps.secret.revealed` con actor, aplicación, nombre, servicios y
  motivo, pero nunca con el valor.

El endpoint no edita ni rota secretos. Editar variables y redesplegar queda fuera
de alcance.

### 5. Dominios pertenecen a Dominios

Apps deja de ofrecer ingress nuevo durante create/deploy. Los campos históricos
pueden mantenerse para lectura compatible, pero:

- el formulario y CLI no los envían en nuevas aplicaciones;
- el worker no publica una segunda ruta automática;
- no se borran columnas ni se crea migración sólo para ocultarlas;
- Dominios conserva ownership, conflicto, TLS, path routing, middlewares,
  entrypoints, probe y eliminación.

Una ruta apunta a la identidad estable de Apps (`application_id + service`) y la
superficie existente de Dominios resuelve el contenedor activo. La UI y el CLI
no guardan ni envían el nombre efímero del contenedor.

Redeploy debe conservar la ruta y hacer que Dominios resuelva el contenedor
nuevo mediante su mecanismo existente. Delete de aplicación libera sus rutas
usando el mecanismo existente; `down` no reasigna el dominio y puede dejarlo en
estado no disponible hasta un nuevo start/deploy.

## CLI

Extender `scripts/spanel_apps.py`, que continúa siendo API-first. El CLI:

- sólo habla HTTP autenticado;
- no importa el runtime del plugin;
- no abre PostgreSQL, Redis, Docker o SSH;
- lee token por stdin o variables existentes, nunca por argv;
- usa la API de Apps para aplicación/servicios;
- usa directamente la API existente de Dominios para dominios/rutas/probe.

### Comandos exactos

#### Enumerar servicios

```bash
python3 scripts/spanel_apps.py services APPLICATION
```

Salida humana:

```text
name=frontend status=running health=healthy ports=80
name=backend status=running health=healthy ports=8000
name=database status=running health=healthy ports=5432
```

#### Enumerar dominios/rutas del servicio

```bash
python3 scripts/spanel_apps.py domain-list APPLICATION SERVICE
```

Debe distinguir dominios registrados disponibles y rutas ya vinculadas. La
fuente es Dominios, no una lista construida por Apps.

#### Asignar dominio registrado

```bash
python3 scripts/spanel_apps.py domain-assign APPLICATION SERVICE \
  --domain api.example.com \
  --path / \
  --internal-path / \
  --port 3000 \
  --entrypoint web \
  --entrypoint websecure \
  --https \
  --middleware rate-limit@file
```

Opciones exactas:

```text
APPLICATION             id de aplicación registrada
SERVICE                 nombre de servicio del deployment activo
--domain DOMAIN         obligatorio; debe estar registrado para el tenant
--path PATH             default /
--internal-path PATH    default /
--strip-path            default false
--port PORT             obligatorio salvo un único puerto detectable
--entrypoint NAME       repetible; omitir usa defaults de Dominios
--https / --no-https    HTTPS por defecto
--middleware NAME       repetible
```

El comando obtiene primero aplicación, servicio y dominio; después llama al
`POST /domains/routes` existente con su schema existente. No traduce a
contenedor ni ejecuta `proxy_domain.py add`.

#### Quitar asignación

```bash
python3 scripts/spanel_apps.py domain-remove APPLICATION SERVICE ROUTE_ID
```

Valida primero que la ruta pertenece a ese tenant, aplicación y servicio; luego
usa el `DELETE /domains/routes/{route_id}` existente.

#### Probar asignación

```bash
python3 scripts/spanel_apps.py domain-probe APPLICATION SERVICE ROUTE_ID
```

Usa el `POST /domains/routes/{route_id}/probe` existente. No hace `curl -k` ni
inventa una comprobación alternativa.

#### Lifecycle por servicio

Los comandos existentes aceptan selector opcional:

```bash
python3 scripts/spanel_apps.py logs APPLICATION --service backend --wait
python3 scripts/spanel_apps.py restart APPLICATION --service backend --wait
python3 scripts/spanel_apps.py stop APPLICATION --service backend --wait
python3 scripts/spanel_apps.py start APPLICATION --service backend --wait
```

`down --service` debe ser error de parser.

### Salida y secretos

- `--json` conserva JSON de las APIs sin añadir valores secretos;
- errores HTTP nunca imprimen body;
- domain assign nunca imprime token, headers o secretos;
- reveal no se añade al CLI en esta spec para evitar que el valor termine en
  historial, pipes o logs de automatización.

## OUT OF SCOPE

- Nuevos endpoints, modelos, migraciones o renderer en Dominios.
- Terminal, exec, shell o estadísticas Docker.
- Edición/rotación de variables.
- Deploy de un único servicio.
- Crear dominios desde Apps o desde `domain-assign`.
- Elegir un contenedor arbitrario.
- Publicar database/Redis por heurística.
- Cambiar ownership entre tenants.

## INVARIANTS

```yaml
invariants:
  - id: B1
    statement: Apps no crea ni posee una segunda implementación de dominios.
    proof: imports/clientes apuntan a Dominios y no existen rutas/modelos/publisher nuevos.
  - id: B2
    statement: Toda acción por servicio valida un nombre del deployment activo.
    proof: tests con servicio válido, inexistente y perteneciente a otra aplicación.
  - id: B3
    statement: Ningún cliente puede seleccionar contenedor, proyecto, red o argv.
    proof: schemas estrictos y tests extra=forbid.
  - id: B4
    statement: Un secreto sólo se revela de forma no cacheable, autorizada y auditada.
    proof: matriz RBAC/tenant, headers y canario ausente de logs/audit/jobs.
  - id: B5
    statement: El CLI sólo usa las APIs autenticadas existentes.
    proof: tests de transporte y ausencia de imports Docker/DB/SSH/runtime.
  - id: B6
    statement: Redeploy no depende del nombre efímero de un contenedor guardado por Apps.
    proof: ruta funciona después de reemplazar el contenedor activo.
```

## VALIDATION

- Unit tests de schema de acción con y sin `service`.
- Lifecycle genera Compose global o Compose + servicio exactamente.
- Logs de servicio siguen redactando todos los canarios.
- Down/delete rechazan selector de servicio.
- Idempotencia distingue bodies y servicios.
- Inventario dinámico no asume nombres de fixture.
- Reveal: 200 autorizado; 403 sin permiso; 404 cross-tenant/ausente.
- Reveal incluye `no-store` y auditoría sin valor.
- CLI prueba todos los comandos con transporte HTTP fake; no mocks como evidencia
  E2E, sólo como tests unitarios del parser/transporte.
- Regresión completa de Apps y Dominios.
- Ruff, Pyright y `git diff --check`.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - aspecs/0060-apps-service-domains-backend-cli.md
    - vendor/systutor-core/plugins/apps/backend/**
    - vendor/systutor-core/plugins/apps/permissions/**
    - vendor/systutor-core/plugins/domains/backend/**
    - vendor/systutor-core/plugins/domains/migrations/**
    - vendor/systutor-core/plugins/domains/frontend/api.ts
    - scripts/spanel_apps.py
    - scripts/tests/test_spanel_apps.py
  import_only:
    - vendor/systutor-core/src/systutor/contracts/**
  prohibited:
    - vendor/systutor-core/plugins/apps/frontend/**
    - vendor/systutor-core/plugins/hosting/**
    - apps/web/**
    - docker-compose.yml
    - .env*
```

`import_only` significa que esta spec puede consumir la superficie existente,
pero no modificarla ni duplicarla. Una corrección demostrablemente necesaria en
Dominios requiere autorización separada antes de ampliar el change surface.

## COMMITS

```text
docs(aspec): define Apps service backend and domain CLI (A.SPEC 0060)
refactor(apps): delegate new ingress exclusively to Domains
feat(apps): scope existing lifecycle actions to a Compose service
feat(apps): expose dynamic service runtime details
feat(apps): add audited managed-secret reveal
feat(apps cli): assign registered domains to application services
test(apps): cover service lifecycle domain CLI and secret isolation
```

Cada commit funcional incluye sus tests directos; el último agrega la matriz
cruzada y regresiones, no posterga toda la cobertura.

## DEFINITION OF DONE

- [ ] Apps no crea ingress nuevo ni duplica Dominios.
- [ ] Detalle expone servicios dinámicos con health y puertos, sin valores env.
- [ ] Logs/start/stop/restart/refresh pueden limitarse a un servicio.
- [ ] Down/delete siguen siendo globales.
- [ ] Reveal sólo devuelve secretos administrados, con RBAC, auditoría y no-store.
- [ ] CLI enumera servicios y asigna/quita/prueba rutas mediante APIs existentes.
- [ ] CLI no acepta container name, Docker ID, project, network ni token por argv.
- [ ] Tests backend/CLI, Ruff y Pyright verdes.
