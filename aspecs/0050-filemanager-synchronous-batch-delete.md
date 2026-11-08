# A.SPEC 0050 — Borrar selecciones múltiples en un único lote síncrono

> `risk: normal` — elimina archivos y directorios, pero sólo dentro de un
> `FileTarget` ya autorizado. No introduce jobs, concurrencia ni nuevas formas
> de resolver contenedores; reduce N operaciones destructivas existentes a una.

## WHY

Antes de esta A.SPEC, el Gestor de archivos borraba una selección con un
`for ... of` que esperaba un `DELETE /entries` por elemento. Cada petición
creaba un proceso Docker distinto:

- WordPress: 20 archivos tardan una mediana de 2.425 s;
- estático/helper: 20 archivos tardan una mediana de 8.047 s.

El prototipo E2E de laboratorio demostró que un único `delete_many` síncrono
elimina ese costo repetido. Traversal, raíz y preflight-all también pasaron.

### Antes y después

Antes, el frontend enviaba un `DELETE` por elemento y esperaba cada respuesta
antes de continuar. La medición real disponible fue de 20 archivos; 100 y 500
son proyecciones lineales de ese baseline y se identifican como tales.

Después, el frontend envía un POST con todas las rutas y el backend ejecuta un
único proceso Docker. Los tres tamaños posteriores son mediciones E2E reales del
código integrado, con tres rondas y mediana, sobre recursos desechables.

| Runtime | Cantidad | Antes 1-a-1 | Después batch | Mejora aprox. |
|---|---:|---:|---:|---:|
| WordPress/PHP | 20 | 2.425 s (real) | 0.117 s | 21x |
| WordPress/PHP | 100 | ~12.1 s (proyección) | 0.144 s | 84x |
| WordPress/PHP | 500 | ~60.6 s (proyección) | 0.177 s | 343x |
| Estático/helper | 20 | 8.047 s (real) | 0.366 s | 22x |
| Estático/helper | 100 | ~40.2 s (proyección) | 0.494 s | 81x |
| Estático/helper | 500 | ~201 s (proyección) | 0.579 s | 347x |

La operación sigue siendo síncrona: la petición espera el resultado completo.
La mejora proviene de pagar autorización, HTTP y arranque Docker una sola vez,
no de introducir paralelismo, workers ni una cola.

#### Pruebas que originaron y validaron el cambio

**Baseline anterior — lab `ada3b501`:**

- contenedores desechables `spanel-lab-bench-wp-*` y
  `spanel-lab-bench-static-*`, cada uno con volumen propio;
- transporte Docker local desde la app del laboratorio;
- 3 rondas por runtime;
- creación de 20 archivos de 16 KiB y reproducción exacta del frontend:
  loop secuencial, una llamada `delete` y un proceso Docker por archivo;
- medianas observadas: 2.425 s WordPress y 8.047 s estático;
- también confirmó que `extract` ya era una operación única y rápida, por lo
  que no formaba parte del problema.

**Prototipo previo a integración — lab `464d153f`:**

- `delete_many` inyectado sólo en el laboratorio, sin modificar producción;
- lotes de 20, 100 y 500 archivos, 3 rondas por tamaño y runtime;
- verificó rechazo de traversal y raíz y que el preflight conserva el primer
  archivo válido cuando una ruta posterior es inválida;
- confirmó tiempo casi constante y justificó integrar el contrato.

**Código integrado por GENERATOR — lab `8e80fe49`:**

- ejecutó el `container.php` real del working tree, no el prototipo;
- sembró cada lote mediante ZIP/extract para que la preparación no contaminara
  el tiempo medido; el cronómetro cubrió sólo `delete_many`;
- 3 rondas para 20, 100 y 500 archivos en runtime PHP y helper estático;
- verificó respuesta `deleted=N`, carpeta vacía, traversal, raíz y preflight;
- confirmó al terminar la ausencia de ambos contenedores y volúmenes lab.

Las pruebas permanentes añadidas por esta A.SPEC cubren además una sola llamada
al driver, tenant/RBAC, límite 1000, auditoría count-only, duplicados,
solapamientos padre/hijo y conservación del árbol ante preflight inválido.

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
- benchmarks: baseline `ada3b501`, prototipo `464d153f` e integrado
  `8e80fe49`, 2026-11-07, 20/100/500 en PHP y helper.
- Commit: `1feac07`.
- Deployment: rebuild de `spanel-app` después de integración.

## Definition of Done

- [x] Multi-selección envía una petición y crea un proceso Docker.
- [x] Preflight completo, límite, duplicados y solapamientos probados.
- [x] Single-delete y operaciones no relacionadas permanecen verdes.
- [x] API tenant/RBAC y auditoría batch probadas.
- [x] Build frontend verde.
- [x] E2E WordPress + estático supera baseline y limpia recursos lab.
- [x] Sin colas, jobs, nuevos permisos, migraciones ni cambios ajenos.
