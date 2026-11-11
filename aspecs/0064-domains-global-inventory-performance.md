# A.SPEC 0064 — Proyección observada del inventario global de dominios

> `risk: medium` — Traefik/YAML continúa siendo la única autoridad sobre la
> publicación efectiva. PostgreSQL incorpora una proyección materializada,
> reconstruible y de solo lectura para que el inventario global no consulte el
> proxy en cada petición. No cambia contratos HTTP ni scripts existentes.

## WHY

La vista global introducida por A.SPEC 0063 reconstruye el inventario una vez
por tenant. Cada reconstrucción lista y lee nuevamente todos los YAML
administrados. Con Docker, 100 dominios y 2 tenants produjeron 2 escaneos, 200
lecturas YAML y una mediana real de 18.182 segundos.

Una lectura masiva mediante un único TAR tardó 0.253 segundos para 100 YAML y
1.340 segundos para 1,000, incluyendo transferencia y parseo. Ese trabajo no
debe formar parte de cada `GET`: debe ejecutarse explícitamente al sincronizar,
y el listado debe consultar PostgreSQL.

## AUTHORITY

- Traefik/YAML es la fuente de verdad de la publicación efectiva.
- PostgreSQL sigue siendo autoridad de tenants, propiedad de dominios, Hosting,
  Apps y rutas administrativas existentes.
- La nueva tabla es únicamente una observación de Traefik. Nunca publica,
  repara ni elimina YAML.
- Ante una discrepancia de publicación, gana Traefik y el siguiente sync
  reemplaza la proyección.
- No existe watcher, `inotify` ni proceso residente nuevo.

## WHAT

### 1. Proyección materializada

Domains persiste observaciones normalizadas de los documentos
`spanel-*.yml` que no sean documentos `spanel-route-*`:

- dominio;
- tenant y sitio inferidos;
- contenedor, resolver y archivo fuente;
- hash del documento;
- generación y fecha de observación.

También persiste el estado del último sync: generación activa, fecha,
cantidades y último error. La proyección puede borrarse y reconstruirse sin
modificar Traefik.

### 2. Sincronización completa

`scripts/domains_sync.py`:

- es plan/dry-run por defecto y solo escribe con `--apply`;
- obtiene el inventario en una operación externa acotada, no una por YAML;
- infiere sitio y tenant desde los providers registrados y valida propiedad;
- informa altas, cambios, bajas, conflictos, huérfanos y documentos inválidos;
- prepara todo antes de escribir;
- activa altas/cambios/bajas y el estado de sync en una sola transacción;
- ante fallo de transporte, parseo o ambigüedad no altera la última generación;
- es idempotente;
- admite salida legible y reporte JSON sin exponer secretos.

La sincronización es explícita. Puede invocarse al operar o programarse
externamente, pero esta A.SPEC no añade scheduler ni watcher.

### 3. Adopción explícita

`scripts/domains_adopt.py` adopta un dominio ya publicado en Traefik:

- dry-run por defecto y escritura con `--apply`;
- exige dominio y tenant; acepta sitio explícito para resolver ambigüedad;
- verifica que YAML, contenedor, sitio y tenant sean coherentes;
- reclama la propiedad administrativa solo si está libre;
- nunca reescribe el YAML ni cambia Traefik;
- ejecuta/aprovecha la sincronización para dejar la observación actualizada;
- es auditable, idempotente y rechaza propiedad cruzada.

### 4. Lectura global

- Después del primer sync exitoso, `scope=all` obtiene dominios adicionales de
  la proyección en una consulta global y los agrupa por tenant en memoria.
- Antes del primer sync exitoso conserva el fallback actual a Traefik para un
  despliegue compatible y reversible.
- Un estado existente pero antiguo no dispara lecturas implícitas del proxy:
  la frescura depende de ejecutar `domains_sync.py`.
- `scope=tenant`, comprobaciones de conflicto y operaciones de escritura
  conservan Traefik como autoridad y su semántica actual.
- Sitios primarios, rutas de Apps y dominios reservados siguen procediendo de
  sus tablas actuales.
- Resolver rutas no vuelve a consultar todos los sitios por cada fila.

### 5. Transporte masivo

La abstracción de archivos ofrece lectura masiva segura para Local, Docker y
SSH. Docker/SSH deben transportar el conjunto en `O(1)` invocaciones externas,
conservar nombre y límites de cada archivo y validar rutas al extraer. Un
archivo o miembro que escape el directorio administrado se rechaza.

### 6. Benchmark y E2E

`scripts/benchmark_domains_global.py` mide por separado sincronización y
lectura global sobre 100 y 1,000 dominios. La preparación y limpieza quedan
fuera de la medición.

El E2E real prueba: YAML temporal → sync → PostgreSQL → endpoint global, y
demuestra que el `GET` sigue funcionando cuando se bloquea el acceso posterior
al proxy.

## OUTCOME

En `mailadmin-app-1` contra `spanel-traefik-lab`, cinco ejecuciones después de
un calentamiento deben cumplir:

| Escenario | Mediana GET | p95 GET |
|---|---:|---:|
| 100 dominios / 2 tenants | ≤ 0.500 s | ≤ 1.000 s |
| 1,000 dominios / 2 tenants | ≤ 1.000 s | ≤ 2.000 s |

Además:

- después del sync, cada `GET scope=all` hace 0 lecturas YAML y 0 `docker exec`;
- el sync usa una sola instantánea lógica del proxy;
- 2 o 100 tenants no multiplican lecturas físicas;
- la respuesta conserva dominios, tenant, dueño, publicación, conflicto y
  editabilidad observables antes del cambio.

## INVARIANTS

1. `core.platform.admin` mediante `TenantContext.is_platform` sigue siendo la
   única autoridad de lectura transversal.
2. La proyección no es una segunda fuente de verdad ni se usa para escribir
   Traefik.
3. Ningún sync parcial reemplaza la última generación válida.
4. Una baja solo se aplica tras un escaneo completo exitoso.
5. Un dominio de otro tenant nunca puede adoptarse ni reasignarse.
6. Los scripts existentes y sus interfaces permanecen válidos.
7. Una lectura nunca modifica YAML, comprueba certificados ni cambia tenant.
8. No se introducen watcher, scheduler, paginación ni caché en memoria.
9. No se usa `users.is_superadmin` para autorización o alcance.

## CHANGE SURFACE

### `change_surface.allowed`

- `aspecs/0064-domains-global-inventory-performance.md`
- `vendor/systutor-core/plugins/domains/backend/models.py`
- `vendor/systutor-core/plugins/domains/backend/service.py`
- `vendor/systutor-core/plugins/domains/backend/schemas.py`
- `vendor/systutor-core/plugins/domains/backend/proxy/files.py`
- `vendor/systutor-core/plugins/domains/backend/proxy/operations.py`
- `vendor/systutor-core/plugins/domains/backend/proxy/__init__.py`
- `vendor/systutor-core/plugins/domains/backend/sync.py`
- `vendor/systutor-core/plugins/domains/migrations/*.py`
- `vendor/systutor-core/plugins/domains/backend/tests/*.py`
- `scripts/domains_sync.py`
- `scripts/domains_adopt.py`
- `scripts/benchmark_domains_global.py`
- `scripts/README.md`
- `scripts/README.es.md`
- `scripts/tests/test_domains_sync.py`
- `scripts/tests/test_domains_adopt.py`
- `scripts/tests/test_domains_projection_e2e.py`

### `change_surface.prohibited`

- `vendor/systutor-core/src/systutor/**`
- `vendor/systutor-core/plugins/hosting/**`
- `vendor/systutor-core/plugins/apps/**`
- `vendor/systutor-core/plugins/tenant/**`
- `vendor/systutor-core/plugins/domains/frontend/**`
- `apps/**`
- `docker-compose.yml`
- existing scripts other than `scripts/benchmark_domains_global.py`
- HTTP paths, response schemas and permissions

## VERIFICATION

Run exactly:

```bash
python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
python3 -m pytest scripts/tests/test_domains_sync.py scripts/tests/test_domains_adopt.py -q
python3 -m pytest scripts/tests/test_domains_projection_e2e.py -q
python3 -m py_compile scripts/domains_sync.py scripts/domains_adopt.py scripts/benchmark_domains_global.py
(cd vendor/systutor-core && ruff check .)
(cd vendor/systutor-core && python3 -m pyright)
(cd vendor/systutor-core && python3 -m pytest tests -q)
git diff --check
```

With the real lab available, also run exactly:

```bash
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_domains_projection_e2e.py -q
python3 scripts/benchmark_domains_global.py --domains 100 --tenants 2 --runs 5 --warmups 1 --transport docker --traefik-container spanel-traefik-lab
python3 scripts/benchmark_domains_global.py --domains 1000 --tenants 2 --runs 5 --warmups 1 --transport docker --traefik-container spanel-traefik-lab
```

## COMMIT PLAN

Exactly three commits:

1. `feat(domains): add observed inventory projection`
   - A.SPEC, backend model/migration/sync/read path and backend tests.
2. `feat(scripts): add domain sync and adoption tools`
   - sync/adopt CLIs, unit tests, benchmark and script documentation.
3. `test(domains): add projection E2E coverage`
   - real end-to-end test only.

## OUT OF SCOPE

- PostgreSQL como autoridad de publicación.
- Modificar automáticamente YAML desde sync/adopt.
- Watcher, scheduler, daemon o Redis para refrescar la proyección.
- Cambiar interfaces de scripts existentes.
- Paginación o cambios frontend.
- Verificación TLS o reparación del proxy.
