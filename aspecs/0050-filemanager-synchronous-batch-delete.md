# A.SPEC 0050 — Borrar selecciones múltiples en un único lote síncrono

> `risk: normal` — elimina archivos y directorios, pero sólo dentro de un
> `FileTarget` ya autorizado. No introduce jobs, concurrencia ni nuevas formas
> de resolver contenedores; reduce N operaciones destructivas existentes a una.

## WHY

El Gestor de archivos borra hoy una selección con un `for ... of` que espera un
`DELETE /entries` por elemento. Cada petición crea un proceso Docker distinto:

- WordPress: 20 archivos tardan una mediana de 2.425 s;
- estático/helper: 20 archivos tardan una mediana de 8.047 s.

El prototipo E2E de laboratorio demostró que un único `delete_many` síncrono
reduce esas medianas a 0.116 s y 0.410 s. Con 500 archivos mantuvo 0.148 s y
0.408 s respectivamente. Traversal, raíz y preflight-all también pasaron.

## WHAT

Cuando el usuario confirma el borrado de **más de un elemento seleccionado**, el
frontend envía todas las rutas en una petición batch. El backend autorizado
ejecuta un solo proceso Docker/PHP síncrono que prevalida todas las rutas y luego
las elimina. Una selección de exactamente un elemento conserva el DELETE actual.

## SCOPE

### Contrato HTTP

```http
POST /api/v1/plugins/filemanager/targets/{provider}/{resource_id}/entries/delete-batch
Authorization: Bearer ...
Permission: filemanager.files.manage
Content-Type: application/json

{"paths":["folder/a.txt","folder/b.txt"]}

200 {"deleted":2}
```

- `paths` admite entre 1 y 1000 strings; el browser usa batch sólo con `>1`.
- La resolución del target sigue siendo server-side por provider/resource_id.
- La sesión DB termina antes de iniciar Docker, igual que las rutas existentes.
- Se ejecuta exactamente un `operation(..., "delete_many", paths=...)`.
- Se registra una auditoría `filemanager.delete_batch` con `count`, sin guardar
  la lista completa de nombres en audit.

### Programa fijo dentro del contenedor

`container.php` añade `delete_many`:

1. exige array con 1..1000 rutas;
2. exige strings y aplica `nonroot()` + `target_path()` a **todas** antes de la
   primera mutación;
3. rechaza duplicados y solapamientos padre/hijo dentro del mismo lote;
4. por cada path mantiene las guardas existentes contra absoluto, `..`, `\\`,
   NUL y symlinks;
5. sólo tras preflight completo llama `delete_tree()` secuencialmente;
6. responde `{"deleted": N}`.

No se promete atomicidad de filesystem: un error de I/O durante la fase de
borrado puede dejar un lote parcialmente aplicado. Sí se garantiza cero
mutación por input inválido detectado en preflight.

### Frontend

- una selección: conserva el `DELETE /entries?path=...` actual;
- varias selecciones: un solo POST `/entries/delete-batch` con rutas relativas;
- diálogo cierra al confirmar, la promesa permanece asíncrona para React pero el
  servidor procesa el lote de forma síncrona;
- al finalizar limpia selección y refresca una sola vez;
- un error batch produce un solo toast con el mensaje backend.

## OUT OF SCOPE

- Jobs, Redis, workers, colas, polling, progreso por archivo o cancelación.
- Borrado paralelo o múltiples procesos Docker para un lote.
- Batch upload, copiar/pegar, mover, comprimir o extraer.
- Cambiar el borrado de selección única.
- Transacciones/rollback de filesystem.
- Nuevos permisos, migraciones o configuración.

## CONTRACT

Precondición: target propio resuelto y permiso `filemanager.files.manage`.

Postcondición de éxito: las N rutas prevalidadas fueron procesadas por un único
proceso y la respuesta informa N. Si cualquier ruta falla en preflight, ninguna
ruta del lote se elimina.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Un lote crea exactamente una operación Docker, nunca una por ruta.
    proof: test de ruta espía una sola llamada operation con op=delete_many y todos los paths; frontend no contiene loop de requests para targets múltiples.
  - id: I2
    statement: Ningún input inválido puede producir una eliminación parcial durante preflight.
    proof: test live mezcla primero una ruta válida y después traversal; la operación falla y el archivo válido permanece.
  - id: I3
    statement: Raíz, traversal, absolutos, backslash, NUL y symlinks siguen inaccesibles.
    proof: target_path/nonroot se reutilizan por cada path; tests de programa/live cubren raíz y traversal, y suite existente de confinamiento sigue verde.
  - id: I4
    statement: Duplicados y relaciones padre/hijo se rechazan antes de borrar.
    proof: tests delete_many verifican error y conservación del árbol para ambos casos.
  - id: I5
    statement: Un tenant no opera targets ajenos y read-only no obtiene escritura.
    proof: tests API batch cross-tenant 404 y falta de manage 403 con runner en cero llamadas.
  - id: I6
    statement: La selección única conserva exactamente el endpoint DELETE existente.
    proof: frontend build y prueba/fuente verifican rama targets.length===1; test de ruta DELETE existente sigue verde.
  - id: I7
    statement: WordPress usa docker exec y estático un único helper docker run, sin cambiar runtimes.
    proof: tests de driver existentes + E2E en ambos runtimes.
  - id: I8
    statement: No se sostiene una conexión DB durante I/O del contenedor.
    proof: authorize devuelve Access tras cerrar sesión; test concurrente existente sigue verde.
```

## VERIFICATION

GENERATOR debe ejecutar exactamente:

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

.venv/bin/python -m pytest \
  vendor/systutor-core/plugins/filemanager/backend/tests/test_driver.py \
  vendor/systutor-core/plugins/filemanager/backend/tests/test_routes.py -q

.venv/bin/python -m pytest scripts/tests/test_filemanager_live.py -q

.venv/bin/python -m ruff check \
  vendor/systutor-core/plugins/filemanager/backend/driver.py \
  vendor/systutor-core/plugins/filemanager/backend/router.py \
  vendor/systutor-core/plugins/filemanager/backend/tests/test_driver.py \
  vendor/systutor-core/plugins/filemanager/backend/tests/test_routes.py \
  scripts/tests/test_filemanager_live.py

cd apps/web && npm run build

cd /data/data/com.termux/files/home/Proyectos/mailadmin
git diff --check
```

El live test sin `SPANEL_LIVE_TESTS=1` debe reportar skip, no falsificar PASS.
Después de GENERATOR, el agente principal repite E2E con contenedores desechables
WordPress y estático, valida 20/100/500 y confirma cleanup.

## ROLLBACK

Revertir el commit de A.SPEC 0050 restaura el loop de DELETE individuales. No
hay migración ni estado persistente nuevo. Un lote ya ejecutado no es reversible,
igual que el borrado individual previo.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0050-filemanager-synchronous-batch-delete.md
    - vendor/systutor-core/plugins/filemanager/backend/container.php
    - vendor/systutor-core/plugins/filemanager/backend/router.py
    - vendor/systutor-core/plugins/filemanager/backend/tests/test_driver.py
    - vendor/systutor-core/plugins/filemanager/backend/tests/test_routes.py
    - vendor/systutor-core/plugins/filemanager/frontend/FileExplorer.tsx
    - scripts/tests/test_filemanager_live.py
  prohibited:
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/filemanager/plugin.json
    - vendor/systutor-core/plugins/filemanager/permissions/**
    - vendor/systutor-core/plugins/filemanager/migrations/**
    - vendor/systutor-core/plugins/hosting/**
    - Dockerfile
    - docker-compose.yml
    - README.md
    - .env*
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - filemanager delete API
    - fixed PHP container program
    - multi-selection delete UI
  indirect:
    - WordPress php runtime
    - static helper runtime
    - audit log
  must_not_affect:
    - single-entry delete
    - listing/read/write/upload/download
    - zip/extract
    - tenant and RBAC isolation
    - hosting lifecycle and site data resolution
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0023
    - A.SPEC 0024
  must_compose_with:
    - filemanager authorized FileTarget resolution
  systemic_invariants:
    - paths remain relative to the server-resolved root
    - container selection never comes from the browser
  composition_checks:
    - backend route tests
    - frontend TypeScript/Vite build
    - disposable lab E2E for php and volume runtimes
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: batch delete remains one operation across UI, API and fixed container program
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/filemanager/backend/container.php
    - vendor/systutor-core/plugins/filemanager/frontend/FileExplorer.tsx
```

## Traceability

- Requirement: al elegir múltiples archivos para borrar, enviar un único lote
  síncrono sin cola ni proceso Docker por archivo.
- owner: luc444s
- approver: luc444s
- benchmark: lab `464d153f`, 2026-11-07, 20/100/500 en php y helper.
- Commit: pendiente de GENERATOR.
- Deployment: rebuild de `spanel-app` después de integración.

## Definition of Done

- [ ] Multi-selección envía una petición y crea un proceso Docker.
- [ ] Preflight completo, límite, duplicados y solapamientos probados.
- [ ] Single-delete y operaciones no relacionadas permanecen verdes.
- [ ] API tenant/RBAC y auditoría batch probadas.
- [ ] Build frontend verde.
- [ ] E2E WordPress + estático supera baseline y limpia recursos lab.
- [ ] Sin colas, jobs, nuevos permisos, migraciones ni cambios ajenos.
