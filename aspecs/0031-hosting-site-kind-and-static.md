# A.SPEC 0031 — Hosting site kind and static provisioning

> `risk: high` — crea contenedores, volúmenes y rutas de Traefik, y decide qué
> tipo de sitio se aprovisiona. Una ruta mal publicada puede servir el sitio
> equivocado o dejar un dominio ocupado. Exige `approver:` humano.
>
> Fase 2 de `docs/proposals/0030-crear-sitio-tipos-y-dominios.md`. Consume el
> namespace de la A.SPEC 0030. La UI “Crear sitio” queda para la A.SPEC 0032.

## WHY

`POST /api/v1/plugins/hosting/sites` solo sabe crear WordPress:

- `CreateSiteRequest` exige `admin_email`, un dato que el panel no debe pedir.
- `hosting_sites` no distingue WordPress de un sitio estático.
- No existe provisioning de nginx + volumen + contenido.
- Un dominio de sitio no se valida contra el `site_suffix` del tenant.
- No hay forma de publicar el dominio primario por el file provider del
  laboratorio; WordPress usa labels y el laboratorio no tiene Docker provider.

El prototipo `scripts/spanel_static/` ya demostró el camino estático en el
laboratorio, pero no vive en la imagen y no está detrás del plugin.

## WHAT

`POST /api/v1/plugins/hosting/sites` pasa a aceptar **tipo, nombre y dominio** y
aprovisiona el sitio correcto como una sola transición:

```json
{ "kind": "static", "name": "paginadelucas", "domain": "paginadelucas.spanel" }
```

1. `kind` es `wordpress` (default) o `static`.
2. `admin_email` desaparece del request. WordPress usa el email del usuario
   autenticado; fallback `admin@<dominio>`.
3. El dominio debe pertenecer al `site_suffix` del tenant (A.SPEC 0030) y estar
   libre en `hosting_sites` y en los extras del proxy.
4. `hosting_sites` gana `kind` con default `wordpress` y backfill.
5. WordPress conserva su provisioning actual, sus labels de Traefik y su
   contraseña generada.
6. Estático aprovisiona: volumen propio, `nginx:alpine`, contenido inicial
   HTML/CSS/JS, labels de propiedad, raíz `/usr/share/nginx/html`, sin DB, sin
   usuario admin y sin contraseña.
7. Si el plugin `domains` está habilitado, el dominio primario del estático se
   publica por su file provider mediante un contrato; si no, se usan labels de
   Traefik como en WordPress.
8. `GET /hosting/sites` devuelve `kind`; `admin_password` es `null` para
   estático.
9. Crear un estático es observable: contenedor running, volumen con contenido,
   ruta publicada y HTTP 200.
10. `DELETE /hosting/sites/{id}` de un estático elimina su ruta, contenedor y
    volumen, y libera el dominio.

## SCOPE

- Modelo, migración, schemas, servicio y router de `hosting`.
- `static.py`: nombres, labels, contenido inicial y `StaticProvisioner`.
- Contrato `site_domain_publisher` y su implementación/registro en `domains`.
- `kind` en `HostingSiteSummary` y en `GET /sites`.
- Validación de dominio contra el sufijo del tenant.
- Guards backend WordPress-only (`409 hosting_site_wrong_kind`).
- Tests de schemas, migración, nombres estáticos, reglas de creación y
  publisher.

## OUT OF SCOPE

- La UI “Crear sitio”, badges y ocultamiento de acciones (A.SPEC 0032).
- Editar el contenido estático por API o UI; el filemanager es la vía.
- `www` automático en el formulario.
- Cambiar el dominio de un sitio ya creado.
- Migrar sitios WordPress existentes a estático.
- DNS, Cloudflare o certificados públicos.
- Cambiar el transporte del proxy (A.SPEC 0029) o su file provider.

## CONTRACT

Precondiciones:

- Plugin `hosting` habilitado y con la migración `001_create_hosting_sites`.
- A.SPEC 0030 aplicada: `tenant_site_settings.site_suffix` puede existir.
- El usuario tiene `hosting.sites.create`.
- La red Docker de `SPANEL_HOSTING_NETWORK` existe.
- Si el plugin `domains` está habilitado, su proxy es escribible; si no, se
  usan labels.

Postcondiciones:

1. `hosting_sites.kind` existe, es `VARCHAR(16) NOT NULL DEFAULT 'wordpress'`;
   las filas existentes quedan en `wordpress`.
2. `CreateSiteRequest` tiene exactamente `kind`, `name`, `domain`; rechaza
   `admin_email` con `422` por `extra="forbid"`.
3. `kind` inválido es `422` de validación.
4. El dominio se normaliza igual que antes; si el tenant no tiene sufijo →
   `409 hosting_site_suffix_missing`; si está fuera →
   `409 hosting_site_domain_outside_suffix`.
5. Un dominio ya presente en `hosting_sites` o reclamado por el proxy →
   `409 hosting_site_conflict`. No se crea ningún recurso.
6. WordPress: se crea como hoy, con el email del usuario autenticado. Si el
   email está vacío, `admin@<dominio>`. `admin_password` es un string generado.
7. Estático: se crea `spanel-<slug>-web` y el volumen homónimo, con labels
   `spanel.site=<slug>`, `spanel.managed=true`, `spanel.tenant=<id>`,
   `spanel.kind=static`, `com.spanel.static=true` y
   `com.spanel.static.domain=<dominio>`.
8. El estático sirve una página inicial con el marcador
   `data-spanel-static="1"`, `styles.css` y `app.js`; el health check espera
   HTTP 200 con el marcador.
9. Publicación de ruta:
   - con publisher registrado (plugin `domains`): `ProxyOperations.add(
     container, domain, wait=0, set_wordpress_url=False)` crea el YAML y no se
     agregan labels de Traefik;
   - sin publisher: se agregan los labels de Traefik, igual que WordPress.
10. Si algo falla después de crear recursos: se elimina solo lo creado (ruta,
    contenedor, volumen) y no queda fila en `hosting_sites`.
11. `GET /sites` devuelve `kind` en cada resumen; `admin_password` es `null`
    para estático.
12. `DELETE` de un estático elimina su ruta publicada (si existe), contenedor y
    volumen, y borra la fila. No toca recursos ajenos.
13. Las rutas WordPress-only (`config`, `database`, `phpmyadmin`, `users`,
    `secrets/salts`, reset de password) sobre un estático devuelven
    `409 hosting_site_wrong_kind` sin tocar Docker.
14. `start`, `stop`, `restart`, `logs` y filemanager siguen sirviendo a ambos
    tipos.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Crear WordPress sigue funcionando sin pedir admin_email y con la misma contraseña de una sola vez.
    proof: test de schema + test de servicio con provisioner falso; respuesta admin_password string.
  - id: I2
    statement: Un dominio fuera del sufijo no crea contenedor, volumen, ruta ni fila.
    proof: test con suffix provider y provisioners espía; asserts de cero llamadas.
  - id: I3
    statement: Un estático no crea DB, usuario admin ni contraseña.
    proof: respuesta admin_password=None; asserts de que no se ejecuta mariadb ni wp-cli.
  - id: I4
    statement: El contenedor y el volumen estático llevan los labels de propiedad del tenant y del sitio.
    proof: test puro de labels + test de argv de creación.
  - id: I5
    statement: El dominio primario del estático se publica una sola vez, por publisher o por labels, nunca por ambos.
    proof: test con publisher espía (cero labels) y sin publisher (labels presentes).
  - id: I6
    statement: Un fallo de provisioning o de publish limpia los recursos creados y no deja la fila.
    proof: test con provisioner que falla después de crear; asserts de cleanup y ausencia de row.
  - id: I7
    statement: Los sitios WordPress existentes siguen siendo wordpress y conservan sus endpoints.
    proof: migración con backfill; tests de hosting existentes verdes; kind=wordpress por default.
  - id: I8
    statement: Un estático no puede ejecutar rutas WordPress-only.
    proof: tests de guard 409 hosting_site_wrong_kind para config, database, phpmyadmin, users, salts y reset.
  - id: I9
    statement: Borrar un estático solo elimina recursos con sus labels y su ruta.
    proof: test de cleanup con recursos ajenos; los ajenos no se borran y se informa 409.
  - id: I10
    statement: No se filtran secretos ni se agregan dependencias de red nuevas.
    proof: revisión de argv/logs; tests de errores sin passwords; network igual a la configurada.
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

### V1 — Schemas y migración

- `{kind:"static",name:"x",domain:"x.spanel"}` válido.
- `{name:"x",domain:"x.spanel"}` → `kind="wordpress"`.
- `{kind:"otro"}` → `422`.
- Incluir `admin_email` → `422` (extra forbid).
- `test_hosting_kind_migration.py`: upgrade → columna `kind` con default y
  backfill `wordpress` → downgrade → upgrade otra vez.

### V2 — Estático puro

- `StaticNames(slug).container == "spanel-<slug>-web"`.
- `volume` homónimo; label `spanel.kind=static`.
- `build_static_run_args()` incluye `--network`, volumen en
  `/usr/share/nginx/html`, labels y la imagen configurada.
- El contenido contiene el marcador `data-spanel-static="1"`.

### V3 — Reglas de creación

- Sin sufijo → 409 `hosting_site_suffix_missing`, sin provisioning.
- `otro.com` con sufijo `spanel` → 409
  `hosting_site_domain_outside_suffix`, sin provisioning.
- Dominio ya en `hosting_sites` → 409 `hosting_site_conflict`.
- Dominio reclamado por extra → 409 `hosting_site_conflict`.
- WordPress recibe `admin_email` del usuario; si está vacío, `admin@domain`.
- Estático devuelve `admin_password=None`.

### V4 — Publisher

- Con publisher registrado, el estático no lleva labels de Traefik y el
  publisher recibe `(container, domain)`.
- Sin publisher, el estático lleva labels `traefik.http.routers.*`.
- Si el publisher falla, se limpian contenedor/volumen y no se inserta row.
- `unpublish` se llama al borrar un estático con ruta publicada.

### V5 — Guards y lifecycle

- `GET /sites` incluye `kind`.
- `start/stop/restart/logs` pasan para un estático.
- `config`, `database`, `phpmyadmin`, `users`, `salts`, `reset` → 409
  `hosting_site_wrong_kind`.
- `DELETE` de un estático llama cleanup y unpublish.

### V6 — Composición

- `pytest` de hosting, domains, tenant y mail verde.
- `tsc --noEmit` verde.
- La migración nueva no toca kernel, mail ni la migración 001 de hosting.

## ROLLBACK

- El `002` de hosting define `downgrade()` y se prueba up/down/up en SQLite. El
  downgrade elimina solo la columna `kind`; no toca filas ni recursos Docker.
- Revertir el commit vuelve a crear solo WordPress y a exigir `admin_email`.
- Los contenedores estáticos creados por esta A.SPEC no se borran solos al
  revertir: se eliminan con `DELETE /sites/{id}` o con el CLI. Los WordPress
  existentes no se tocan.
- Las rutas publicadas por el publisher se pueden quitar con
  `POST delete` antes de revertir, o quedan como YAML gestionado hasta la
  limpieza manual.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/src/systutor/contracts/site_domain_publisher.py  # nuevo
    - vendor/systutor-core/plugins/domains/backend/publisher.py             # nuevo
    - vendor/systutor-core/plugins/domains/backend/register.py
    - vendor/systutor-core/plugins/domains/backend/proxy/operations.py      # backup solo local
    - vendor/systutor-core/plugins/domains/backend/tests/test_primary_publisher.py
    - vendor/systutor-core/plugins/domains/backend/tests/conftest.py          # aisla el perfil del .env
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/hosting/backend/migrations/002_add_hosting_site_kind.py  # nuevo
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/static.py               # nuevo
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/register.py
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - vendor/systutor-core/plugins/hosting/README.md
    - aspecs/0031-hosting-site-kind-and-static.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/plugins/domains/backend/proxy/__init__.py
    - vendor/systutor-core/plugins/domains/backend/proxy/config.py
    - vendor/systutor-core/plugins/domains/backend/proxy/core.py
    - vendor/systutor-core/plugins/domains/backend/proxy/detect.py
    - vendor/systutor-core/plugins/domains/backend/proxy/doctor.py
    - vendor/systutor-core/plugins/domains/backend/proxy/files.py
    - vendor/systutor-core/plugins/domains/backend/proxy/labdns.py
    - vendor/systutor-core/plugins/domains/backend/proxy/probe.py
    - vendor/systutor-core/plugins/domains/backend/proxy/transport.py
    - vendor/systutor-core/plugins/domains/backend/proxy/wordpress.py
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/frontend/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/filemanager/**
    - docker-compose.yml
    - scripts/**
    - /opt/stacks/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - creacion de sitios en el plugin hosting
    - contenedores y volumenes estaticos
    - publicacion del dominio primario en el proxy
  indirect:
    - listado y borrado de sitios
    - UI de hosting (consumira kind en 0032)
    - laboratorio Traefik por file provider
  must_not_affect:
    - sitios WordPress existentes y sus contenedores
    - base de datos, usuarios y secretos de WordPress
    - rutas y YAML que no sean el dominio primario creado
    - mail, tenant y kernel
    - migraciones 001 de hosting, kernel y mail
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013
    - 0014
    - 0027
    - 0030
  must_compose_with:
    - plugin domains (publisher del file provider)
    - plugin tenant (site_suffix)
    - plugin filemanager (acceso a /usr/share/nginx/html)
  systemic_invariants:
    - un dueno por Host
    - aislamiento por tenant
    - deshabilitar un plugin no cambia el ruteo
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest plugins/hosting/backend/tests plugins/domains/backend/tests plugins/tenant/backend/tests plugins/mail/backend/tests
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la creacion por tipo se decide en el servicio; el provisioning estatico vive en static.py y el publisher en domains
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/static.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/publisher.py
```

## Traceability

- Requirement: crear un sitio estático o WordPress desde la API de Hosting con
  tipo y dominio del tenant, sin pedir email y con la ruta publicada.
- owner: luc444s
- approver: luc444s
- Commit: `6d2b01f`
- Deployment: laboratorio primero; producción con la imagen nueva y el plugin
  `domains` habilitado para publicar por file provider.

## Definition of Done

- [x] Objetivo: API crea ambos tipos y el estático responde por HTTP.
- [x] Alcance respetado: sin UI, sin mail, sin proxy ajeno.
- [x] Contrato: kind, migración, provisioning, publisher y guards.
- [x] Invariantes I1–I10 probados.
- [x] V0–V6 ejecutados.
- [x] Rollback de migración ejecutado (up/down/up).
- [x] Sin archivos fuera de `change_surface`.
- [x] Nada >600 líneas; ideal <400.
- [x] Trazabilidad completa.
