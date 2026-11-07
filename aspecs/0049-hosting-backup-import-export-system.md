# A.SPEC 0049 — Importar/Exportar respaldos dentro del modal de Hosting

> `risk: high` — Exportar lee archivos/DB y crea un bearer link temporal;
> Importar reemplaza web root + DB. El motor destructivo NO se reimplementa:
> esta feature orquesta los scripts de A.SPEC 0047/0048, ya validados E2E.

## WHY

A.SPEC 0048 cerró el round-trip en laboratorio:

- `site_backup.py` produce un único ZIP verificable;
- `site_restore.py` restaura ese mismo ZIP con safety backup + rollback;
- `site_backup_link.py` fijó la semántica de enlace opaco, temporal y one-time.

Falta exponer esas dos capacidades en el sistema. Cada sitio/contenedor
registrado ya tiene un modal en Hosting; allí deben aparecer **Importar** y
**Exportar**, cada uno con su diálogo específico.

## SCOPE

### 1. Backend: orquestar los scripts, no duplicarlos

El plugin empaqueta y ejecuta como subprocess, sin `shell=True`:

- `/app/scripts/site_backup.py` para crear el ZIP de exportación;
- `/app/scripts/site_restore.py --yes` para importar/restaurar;
- `site_backup_link.py` se empaqueta como artefacto probado, pero el proceso web
  **no abre un puerto nuevo**: reproduce su contrato one-time bajo el HTTPS ya
  existente mediante ticket Redis + `FileResponse`.

`Dockerfile` copia los tres scripts a `/app/scripts`. En checkout/dev el
resolver usa `<repo>/scripts`; `SPANEL_HOSTING_BACKUP_SCRIPTS_DIR` puede
sobrescribirlo.

Precondición operacional explícita: esta integración requiere
`SPANEL_DOCKER_TRANSPORT=local`, porque los scripts hablan con el Docker CLI del
host montado. Si el transporte efectivo es SSH, ambos POST responden 503
`hosting_backup_transport_unsupported` **antes de guardar/subir/mutar**.

No se copian funciones de backup/restore al plugin. El subprocess recibe
únicamente `site.container_name`, resuelto desde `get_owned_site`; el browser
nunca envía container, DB, path ni argumentos Docker.

### 2. Almacenamiento privado y exclusión

Configuración del plugin:

- `SPANEL_HOSTING_BACKUPS_DIR` (default
  `/var/lib/spanel/hosting-backups` dentro de app);
- `SPANEL_HOSTING_BACKUP_SCRIPTS_DIR` (autodetect repo o `/app/scripts`);
- `SPANEL_HOSTING_BACKUP_LINK_TTL` (default 300, clamp 30..900 s);
- `SPANEL_HOSTING_BACKUP_MAX_UPLOAD_BYTES` (default 5 GiB, mínimo 1 MiB);

`docker-compose.yml` monta un volumen nombrado `hosting_backups` en el default.
Directorios 0700 y ZIPs 0600. Layout interno no público:

```text
<root>/exports/<backup_id>/*.zip
<root>/imports/<upload_id>.zip
<root>/safety/...
<root>/locks/<site_id>.lock
<root>/failed/<reference>/...
```

Un `flock(LOCK_EX|LOCK_NB)` por `site_id` serializa Importar y Exportar entre
workers; contención → 409 `hosting_backup_busy`. No se exige `managed=true`:
cualquier fila registrada, propia y `running` puede respaldarse/restaurarse.

En startup y antes de cada operación se purgan únicamente exports/imports
temporales más viejos que el TTL + margen. Safety backups NO se exponen ni se
borran automáticamente en esta etapa. No existe endpoint de listado.

### 3. API Exportar — sólo link temporal

```http
POST /api/v1/plugins/hosting/sites/{site_id}/backup/export
Authorization: Bearer ...
Permission: hosting.sites.manage

200 {
  "url": "/api/v1/plugins/hosting/sites/{site_id}/backup/download/{ticket}",
  "expires_in": 300,
  "filename": "wp-...zip"
}
Cache-Control: no-store
```

Flujo:

1. `get_owned_site` resuelve tenant + container.
2. Libera la transacción DB antes del subprocess largo.
3. Ejecuta `site_backup.py --container <trusted> --out <private random dir>`.
4. Exige exit 0 y exactamente un ZIP regular; chmod 0600.
5. Emite token `secrets.token_urlsafe(32)` en Redis con TTL configurado. El
   payload contiene `user_id`, `tenant_id`, `site_id`, `backup_id` y filename,
   nunca un path.
6. Audita `hosting.site.backup_exported`, sin token/path/credenciales.

La emisión requiere `RedisCacheBackend`; fallback Memory devuelve 503
`hosting_backup_ticket_store_unavailable`, porque no garantiza one-time entre
workers.

```http
GET /api/v1/plugins/hosting/sites/{site_id}/backup/download/{ticket}
```

- No requiere Authorization: el token es bearer por posesión.
- Redis `GETDEL` consume atómicamente antes de abrir el archivo.
- Revalida que emisor siga activo, conserve `hosting.sites.manage`, tenant/site
  coincidan y el archivo resuelva bajo `<root>/exports/<backup_id>`.
- Invalidez, expiry, segundo GET o mismatch → 401 `Descarga expirada`.
- Responde streaming/`FileResponse`, attachment ZIP, `Content-Length`,
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`.
- Al terminar/interrumpirse la respuesta elimina ese export directory mediante
  background cleanup. Audita `hosting.site.backup_downloaded` como consumo por
  ticket, asociando al emisor sin afirmar que éste fue quien abrió el link.

No hay descarga permanente ni reutilizable.

### 4. API Importar — restaurar el mismo ZIP

```http
POST /api/v1/plugins/hosting/sites/{site_id}/backup/import
Content-Type: multipart/form-data
Authorization: Bearer ...
Permission: hosting.sites.manage

file=<zip>
confirmation=<container_name exacto>

200 {"site_id": "...", "container_name": "...", "restored": true}
Cache-Control: no-store
```

- Rechaza filename no `.zip`, confirmación distinta y upload mayor al límite.
- Copia por chunks a un temporal privado, nunca `await file.read()` completo.
- Libera DB antes de ejecutar `site_restore.py --zip <private> --container
  <trusted> --yes --safety-out <root>/safety`.
- El script vuelve a verificar manifest/hash/container/kind, crea y pre-valida
  safety backup, aplica restore y rollback si falla.
- Mapeo de exits:
  - 0 → 200 + auditoría `hosting.site.backup_imported`;
  - 2 → 422 `hosting_backup_invalid`;
  - 1 → 409 `hosting_backup_restore_failed` (incluye rollback exitoso o fallo
    pre-mutación; mensaje público genérico);
  - 3 → 500 `hosting_backup_rollback_failed`, conserva incoming + safety bajo
    `<root>/failed/<opaque_reference>` y devuelve solo la referencia opaca;
  - lanzamiento/IO inesperado → 503 `hosting_backup_runtime_unavailable`.
- stdout/stderr completos nunca llegan al cliente ni auditoría. Logs internos
  truncan/redactan; no contienen token ni credenciales.
- El incoming se elimina tras success o error recuperable; si rollback falla se
  conserva para intervención.
- Toda falla posterior a autorización intenta auditoría
  `hosting.site.backup_import_failed` con código/result, sin filename/path.

### 5. Frontend: dos botones, dos diálogos

En el dialog principal de cada `HostingSite`:

- mostrar **Importar** y **Exportar** sólo con `hosting.sites.manage`;
- funcionan también para registrados `managed=false`;
- disabled si `status !== "running"` o su mutation está pendiente.

Crear `components/BackupDialogs.tsx` para no llevar `HostingPage.tsx` sobre el
límite. Ambos son diálogos sobre el modal del sitio (`z-[1100]`).

#### Exportar

- Abre diálogo “Exportar respaldo de `<container>`”.
- `Alert`: incluye archivos y, en WP, DB; cualquiera con el link puede bajar el
  ZIP; expira y funciona una sola vez; previews/bots pueden consumirlo.
- Botón **Generar enlace temporal** llama al POST y permanece en el diálogo.
- Mientras corre: disabled + “Generando respaldo…”.
- Al éxito muestra URL absoluta una sola vez en `<code>`, expiración, botones
  **Copiar enlace** y **Abrir/descargar**. Abrir puede consumirlo.
- Cerrar limpia link del estado; regenerar ejecuta un backup nuevo.
- Error muestra el mensaje real normalizado por `apiRequest`.

#### Importar

- Abre diálogo “Importar respaldo en `<container>`”.
- `Alert` destructivo: reemplaza archivos y para WP la DB; el sistema crea
  safety backup y rollback, pero la operación no debe interrumpirse.
- Input `accept=".zip,application/zip"` y texto de confirmación exacto con el
  container.
- Submit deshabilitado hasta archivo `.zip` + confirmación exacta.
- Al submit cierra el diálogo inmediatamente (patrón A.SPEC 0043) y ejecuta
  `toast.promise`: loading “Restaurando respaldo…”, success “Respaldo
  restaurado”, error backend real.
- En success invalida `hostingKeys.sites`; jamás cachea el `File` ni respuesta.

### 6. Commits secuenciales obligatorios

GENERATOR realiza dos commits, sin incluir cambios preexistentes ajenos:

1. `feat(hosting): integrar backup import/export backend (A.SPEC 0049)`
   - A.SPEC, Dockerfile/compose, backend y tests backend;
   - debe existir y pasar verification backend **antes** del siguiente.
2. `feat(hosting ui): añadir importar y exportar backups (A.SPEC 0049)`
   - sólo frontend;
   - se crea después de build frontend verde.

`README.md` y `.env.docker.example` tienen cambios previos del usuario: NO se
stagean ni se modifican.

## OUT OF SCOPE

- Historial/listado de backups, descarga directa permanente, purge manual,
  retención configurable o restaurar otro container.
- Jobs, Dramatiq, progreso/polling, cron, S3/object storage.
- Modificar los scripts 0047/0048 o duplicar su motor en el plugin.
- Nuevo permiso: se reutiliza `hosting.sites.manage`.
- Migraciones/modelos de DB.
- Soporte del panel para `SPANEL_DOCKER_TRANSPORT=ssh`.
- HTTPS propio del script: el link del sistema usa el mismo origin HTTPS del
  panel.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El browser nunca selecciona container, DB, web root, script ni path del servidor.
    proof: tests API envían fields extra/nombres hostiles; subprocess argv contiene solo site.container_name y paths random server-owned.
  - id: I2
    statement: Un ticket produce como máximo una descarga entre todos los workers.
    proof: Redis GETDEL; test concurrente obtiene exactamente un 200 y N-1 401.
  - id: I3
    statement: Exportar e Importar sólo operan sobre un site_id propio con hosting.sites.manage.
    proof: tests cross-tenant/permiso revocado = 404/403 y runner sin llamadas.
  - id: I4
    statement: Importar no muta si transporte no-local, confirmación falla, upload excede límite o site lock está ocupado.
    proof: cada guard se prueba con runner spy en cero llamadas.
  - id: I5
    statement: La implementación del sistema ejecuta los scripts E2E validados y no replica su lógica Docker/ZIP/SQL.
    proof: backups.py sólo arma argv, storage, locks y mapea exits; grep sin docker cp/mysqldump/mariadb SQL.
  - id: I6
    statement: Token, credenciales, paths privados y stdout/stderr no salen por API, audit ni frontend.
    proof: fixtures con secretos; respuestas/audits no los contienen; export response sólo URL/TTL/filename.
  - id: I7
    statement: Un export descargado se elimina y un import recuperable limpia incoming; rollback_failed conserva evidencia con referencia opaca.
    proof: tests background cleanup, exits 0/1/2/3 y paths bajo root temporal.
  - id: I8
    statement: El modal ofrece exactamente Importar y Exportar para cualquier sitio registrado running con manage.
    proof: build TypeScript + assertions/component extraction; no guard managed ni guard kind.
  - id: I9
    statement: Safety backups no son descargables/listables por ninguna ruta de esta etapa.
    proof: tabla de rutas y tests 404 para intentos de ids/paths safety.
```

## VERIFICATION

GENERATOR debe ejecutar, en este orden:

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

# prerequisite scripts siguen verdes
.venv/bin/python -m pytest \
  scripts/tests/test_site_backup.py \
  scripts/tests/test_site_backup_link.py \
  scripts/tests/test_site_restore.py -q

# backend
.venv/bin/python -m pytest \
  vendor/systutor-core/plugins/hosting/backend/tests/test_backups.py \
  vendor/systutor-core/plugins/hosting/backend/tests/test_backup_routes.py -q
.venv/bin/python -m ruff check \
  vendor/systutor-core/plugins/hosting/backend/backups.py \
  vendor/systutor-core/plugins/hosting/backend/backup_tickets.py \
  vendor/systutor-core/plugins/hosting/backend/settings.py \
  vendor/systutor-core/plugins/hosting/backend/schemas.py \
  vendor/systutor-core/plugins/hosting/backend/router.py \
  vendor/systutor-core/plugins/hosting/backend/register.py \
  vendor/systutor-core/plugins/hosting/backend/tests/test_backups.py \
  vendor/systutor-core/plugins/hosting/backend/tests/test_backup_routes.py

# frontend
cd apps/web && npm run build

# final
cd /data/data/com.termux/files/home/Proyectos/mailadmin
git diff --check
```

Después de verification, comprobar `git log -3 --oneline`: commit backend antes
que frontend y `.env.docker.example`/`README.md` continúan fuera de ambos.

## ROLLBACK

1. Revertir primero commit frontend.
2. Revertir commit backend.
3. El volumen `hosting_backups` puede conservar safety backups; no borrarlo de
   forma automática. Los scripts 0047/0048 siguen utilizables por operador.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0049-hosting-backup-import-export-system.md
    - Dockerfile
    - docker-compose.yml
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/backups.py
    - vendor/systutor-core/plugins/hosting/backend/backup_tickets.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/register.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_backups.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_backup_routes.py
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/components/BackupDialogs.tsx
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
  prohibited:
    - scripts/**
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/hosting/migrations/**
    - vendor/systutor-core/plugins/hosting/plugin.json
    - vendor/systutor-core/plugins/hosting/permissions/**
    - README.md
    - .env*
    - entrypoint.sh
```

## Traceability

- Requirement: botones Importar/Exportar dentro del modal Hosting; cada uno
  abre su flujo. Exportar sólo crea link temporal, Importar restaura el mismo
  ZIP mediante scripts 0048.
- owner: luc444s
- prerequisite: commit `8aa1393` (A.SPEC 0048 E2E).

## Definition of Done

- [ ] Backend ejecuta scripts, one-time ticket y guards tenant/RBAC/storage.
- [ ] Import exits/cleanup/safety/rollback_failed mapeados y auditados.
- [ ] Modal contiene Importar + Exportar y ambos diálogos completos.
- [ ] Verification backend + frontend verde.
- [ ] Dos commits secuenciales: backend primero, frontend después.
- [ ] Cambios previos README/.env permanecen sin stagear.
