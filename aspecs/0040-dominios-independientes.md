# A.SPEC 0040 — Crear dominios independientes y vincularlos después

> `risk: normal` — no hay migración de esquema ni cambios al escritor de Traefik
> existente. La ruta nueva de reserva no escribe YAML; el vínculo reutiliza
> `ProxyOperations.add()` sin cambiar prioridades, resolver ni certificados.
> `approver:` humano obligatorio antes de desplegar.

## WHY

El alta de dominios del plugin `domains` obliga a elegir un sitio existente:

```text
POST /api/v1/plugins/domains
{ "site_id": "uuid", "domain": "ejemplo.com", "www": false }
```

`DomainsService.add()` resuelve el sitio, obtiene su contenedor y publica el YAML
de Traefik. Consecuencias actuales:

- Un tenant no puede reservar un dominio antes de tener un sitio.
- Un dominio propiedad del tenant en `tenant_domains`, pero sin ruta publicada,
  no aparece en `GET /domains`.
- El inventario de dominios depende del proxy: si Traefik no responde, la lista
  falla, aunque la propiedad del dominio ya esté en la base.
- La UI deshabilita “Agregar dominio extra” cuando no hay sitios y exige el
  selector de sitio.

La propiedad explícita ya existe en `tenant_domains(domain PK, tenant_id)` y los
contratos `domain_namespace` / `domain_ownership` ya permiten reclamar y
consultar dominios sin importar modelos entre plugins. `mail` ya compone
`{tenant.domain} ∪ tenant_domains`. `hosting` ya acepta dominios del tenant al
crear un sitio. Falta únicamente separar **reservar** de **publicar**.

## WHAT

Un dominio puede crearse sin `site_id` ni contenedor. La creación sin sitio:

- valida y normaliza el dominio;
- reclama la propiedad en `tenant_domains`;
- **no** escribe ningún `spanel-*.yml`;
- **no** sondea ni solicita certificados;
- **no** ejecuta Docker ni reinicia contenedores;
- aparece en `GET /domains` como reservado: `site_id=null`, `container=null`,
  `published=false`.

Después, el dominio reservado se puede vincular a un sitio:

- `POST /domains/{domain}/link` con `{site_id, www}`;
- el backend resuelve el sitio contra Hosting, nunca acepta un contenedor del
  cliente;
- publica la ruta con el mismo `ProxyOperations.add()` que usa el flujo actual;
- si falla el proxy, la propiedad reservada se conserva y la API devuelve error.

El dominio reservado también queda disponible automáticamente para correo
(`mail`) y para crear un sitio (`hosting`), porque ambos ya consumen
`tenant_domains`.

Verdad independiente y falsable: **reservar un dominio no crea ni modifica
ningún archivo de Traefik; vincularlo produce exactamente el mismo YAML,
prioridad y resolver que el alta actual.**

## SCOPE

Entra:

- `CreateDomainRequest.site_id` opcional.
- Reserva de dominio sin sitio en `DomainsService.add()`.
- Nuevo `DomainsService.link()` y ruta `POST /domains/{domain}/link`.
- Lectura de dominios propiedad del tenant no publicados en `list_domains()`.
- Lista tolerante a proxy caído para dominios propiedad del tenant.
- `DELETE` de un dominio reservado libera la propiedad mediante un provider
  opcional nuevo del contrato `domain_namespace`.
- `published` en `DomainItem` y aviso de proxy en `DomainsResponse`.
- UI de Dominios: alta sin sitio, estado “Sin publicar”, acción “Vincular” y
  diálogo de vínculo.
- Tests unitarios, de API, de regresión de Hosting/Mail y live opcional.
- Documentación y esta A.SPEC.

No entra:

- Nueva tabla o migración de esquema.
- Rutas hacia contenedores arbitrarios, Docker externo o URLs de servicios
  externos. La API deja el punto de extensión, pero no acepta destinos que no
  sean un sitio propio resuelto por Hosting.
- Cambiar `tenants.domain`, la semántica de correo o el dominio primario de un
  sitio.
- Editar `HostingSite.domain`, labels del primario, `provisioner.py`, el
  renderer YAML, prioridades, resolver o emisión de certificados.
- Borrar o reescribir YAML heredados que no estén gestionados por Spanel.
- DNS público, MX/SPF/DKIM/DMARC, certificados wildcard o panel DNS.
- Desplegar en producción.

## CONTRACT

### API

| Método | Ruta | Permiso | Resultado |
|---|---|---|---|
| GET | `/api/v1/plugins/domains` | `domains.domains.read` | Primarios, extras y reservados del tenant |
| POST | `/api/v1/plugins/domains` | `domains.domains.manage` | `201`; reserva o publica según `site_id` |
| POST | `/api/v1/plugins/domains/{domain}/link` | `domains.domains.manage` | `200`; vincula un reservado a un sitio |
| DELETE | `/api/v1/plugins/domains/{domain}` | `domains.domains.manage` | `204`; libera reserva o quita ruta extra |
| GET | `/api/v1/plugins/domains/{domain}/certificate` | `domains.domains.read` | Estado; reservado devuelve `unpublished` |

`POST /domains` acepta:

```json
{ "domain": "ejemplo.com", "www": false }
```

o, para compatibilidad total:

```json
{ "site_id": "uuid", "domain": "ejemplo.com", "www": false }
```

Reglas de `POST` sin `site_id`:

1. Normaliza con el perfil de proxy actual. Un dominio inválido → `422
   domains.invalid_domain`.
2. Rechaza un dominio primario de cualquier sitio → `409
   domains.primary_owned`.
3. Rechaza un dominio de otro tenant → `409 domains.already_owned`.
4. Si el proxy responde y el dominio ya es un extra de otro contenedor →
   `409 domains.already_claimed`. Si el proxy no responde, la reserva continúa:
   no escribe rutas y el chequeo se repite al vincular.
5. Reclama cada candidato libre en `tenant_domains`. Si ya lo posee el mismo
   tenant, la operación es idempotente.
6. Con `www=true`, reserva también `www.<dominio>`.
7. Audita `domains.domain.added` con `details.mode="reserved"`,
   `site_id=null`, `container=null`.
8. Devuelve items con `published=false`.

Reglas de `POST /domains/{domain}/link`:

1. Normaliza el dominio y exige que pertenezca al tenant actual; si no,
   `404 domains.domain_not_found`.
2. Resuelve `site_id` con `resolve_site()`; sitio inexistente o de otro tenant
   → `404 domains.site_not_found`.
3. Rechaza primarios → `409 domains.primary_owned`.
4. Rechaza extras publicados por otro contenedor → `409
   domains.already_claimed`.
5. Reclama candidatos libres (`www` incluido) en la misma transacción.
6. Exige proxy disponible y publica con `ProxyOperations.add(container,
   domain, www=..., wait=0, set_wordpress_url=False)`.
7. Audita `domains.domain.linked` con `site_id` y `container`.
8. Devuelve items con `published=true`. Si falla la publicación, no se elimina
   la propiedad reservada.

Reglas de `DELETE /domains/{domain}`:

- Extra publicado por un contenedor del tenant → quita la ruta como hoy.
- Primario de un sitio → `409 domains.primary_owned`.
- Propiedad reservada no publicada → libera la fila de `tenant_domains` con el
  provider `release` del contrato. Si es el dominio de correo del tenant, el
  provider responde `409 tenant_domain_in_use`.
- Dominio desconocido → `404`.
- La respuesta sigue siendo `204`.

### Modelo de lectura

`DomainItem` conserva los campos actuales y agrega:

```json
{
  "domain": "ejemplo.com",
  "site_id": null,
  "container": null,
  "owner": "domains",
  "editable": true,
  "conflict": false,
  "published": false
}
```

- Primario de sitio: `owner="hosting"`, `published=true`, `editable=false`.
- Extra publicado: `owner="domains"`, `published=true`, `editable=can_manage`.
- Reservado: `owner="domains"`, `published=false`, `site_id=null`,
  `container=null`, `editable=can_manage`.

`list_domains()` une primarios, extras y filas de `tenant_domains` del tenant
que no estén ya publicadas. Excluye `tenants.domain` de la lista de reservados
para no duplicar el dominio de correo. Si el proxy falla, devuelve los dominios
propiedad del tenant y `proxy_available=false` + `proxy_error`; no convierte la
lista en un `503`.

`certificate` de un dominio reservado devuelve:

```json
{ "domain": "ejemplo.com", "state": "unpublished", "verified": false }
```

sin tocar la red.

### UI

Diálogo de alta:

```text
┌─ Agregar dominio ──────────────────────────────┐
│                                                │
│  Dominio                                       │
│  [ ejemplo.com                              ]  │
│                                                │
│  Destino                                       │
│  [ Solo reservar (sin publicar)          ▼ ]   │
│                                                │
│  Reserva el dominio para este tenant. No se    │
│  publica en Traefik ni se emite certificado    │
│  hasta que lo vincules a un sitio.             │
│                                                │
│  [ ] Agregar también www.ejemplo.com           │
│                                                │
│                     [ Cancelar ] [ Agregar ]   │
└────────────────────────────────────────────────┘
```

Si se elige un sitio en **Destino**:

```text
┌─ Agregar dominio ──────────────────────────────┐
│                                                │
│  Dominio                                       │
│  [ ejemplo.com                              ]  │
│                                                │
│  Destino                                       │
│  [ gestiongases (spanel-...-wp)          ▼ ]   │
│                                                │
│  El dominio se publicará en gestiongases.      │
│  Se creará la ruta en Traefik y se solicitará  │
│  el certificado.                               │
│                                                │
│  [ ] Agregar también www.ejemplo.com           │
│                                                │
│                     [ Cancelar ] [ Agregar ]   │
└────────────────────────────────────────────────┘
```

Selector de destino:

```text
Solo reservar (sin publicar)
gestiongases (spanel-gestiongases-wp)
otro-sitio (spanel-otro-wp)
```

Tabla:

| Dominio | Sitio | Estado | Dueño | Certificado | Acciones |
|---|---|---|---|---|---|
| ejemplo.com | — | Sin publicar | Dominio | Sin publicar | Vincular · Quitar |
| gestiongases.com | spanel-gestiongases-wp | Publicado | Extra | Válido | Quitar |

Diálogo de vínculo:

```text
┌─ Vincular dominio ─────────────────────────────┐
│                                                │
│  ejemplo.com                                   │
│                                                │
│  Sitio                                         │
│  [ gestiongases (spanel-...-wp)          ▼ ]   │
│                                                │
│  El dominio se publicará en gestiongases.      │
│                                                │
│  [ ] Agregar también www.ejemplo.com           │
│                                                │
│                     [ Cancelar ] [ Vincular ]  │
└────────────────────────────────────────────────┘
```

- El botón de alta ya no se deshabilita cuando `sites.length === 0`.
- El texto “dominio extra” desaparece del alta.
- Un reservado muestra “Sin publicar” y ofrece **Vincular** solo si hay sitios
  y el usuario tiene `domains.domains.manage`.
- El certificado de un reservado no se consulta; muestra “Sin publicar”.
- El resto de la tabla, conflicto, buzones y permisos se conserva.

### Contrato `domain_namespace`

Se agrega un provider opcional sin romper registros existentes:

```python
DomainReleaseProvider = Callable[[Session, str, str], None]

def register_domain_providers(
    app, name, *, list_domains, owner, claim=None, release=None
) -> None: ...

def release_domain(app, db, tenant_id, domain) -> bool: ...
```

- `release` es opcional: providers actuales siguen funcionando.
- `tenant` lo registra y delega en `remove_managed_domain()` con la app para
  respetar primarios y dominios en uso.
- `domains` lo usa solo para reservas no publicadas.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Reservar un dominio sin sitio no escribe ningún spanel-*.yml, no sondea certificados y no ejecuta Docker.
    proof: test de servicio con FakeOps que falla si add/remove son llamados; test API que compara el directorio dinámico antes/después; live que verifica ausencia de archivo nuevo.
  - id: I2
    statement: Vincular un reservado produce el mismo YAML, prioridad HTTP 200/HTTPS 100, resolver y marcador que el alta actual.
    proof: test con FakeOps y live que compara el YAML generado por link con el de POST con site_id.
  - id: I3
    statement: Los primarios de Hosting, los extras existentes y sus certificados no cambian al reservar, listar o liberar.
    proof: tests existentes de domains/proxy verdes; live con hash de los YAML existentes y HTTPS del primario antes/después.
  - id: I4
    statement: Un dominio tiene un solo tenant dueño; reservar y vincular respetan la PK global y responden 409 ante otro tenant.
    proof: tests de dos tenants en servicio y API.
  - id: I5
    statement: Un dominio reservado queda disponible para correo por tenant_domains, sin modificar código de mail.
    proof: test de regresión que lista cuentas y acepta el dominio reservado en create_account.
  - id: I6
    statement: Hosting puede crear un sitio con un dominio reservado por el mismo tenant y no puede con uno de otro tenant.
    proof: tests de hosting con y sin provider de dominio.
  - id: I7
    statement: DELETE de un reservado libera la propiedad; DELETE de un extra publicado solo quita la ruta como hoy.
    proof: tests de servicio y API de ambos casos.
  - id: I8
    statement: Con el proxy caído, reservar y listar siguen funcionando; vincular responde 503 y conserva la reserva.
    proof: test con operaciones que lanzan ProxyError.
  - id: I9
    statement: La UI conserva el flujo con sitio y permite reservar sin sitios; no exige site_id ni contenedor.
    proof: tsc/build y revisión del diálogo; prueba manual en Docker local.
  - id: I10
    statement: No hay migración ni cambio de esquema; revertir el commit no deja datos inválidos.
    proof: git diff sin migrations; filas reservadas siguen siendo tenant_domains válidas.
```

## VERIFICATION

### V0 — Gates estáticos y unitarios

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/domains/backend/tests \
  vendor/systutor-core/plugins/tenant/backend/tests \
  vendor/systutor-core/plugins/hosting/backend/tests \
  vendor/systutor-core/plugins/mail/backend/tests \
  scripts/tests/test_domains_api.py \
  scripts/tests/test_proxy_domain.py -q

cd apps/web
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

### V1 — Reserva sin sitio

- `POST /domains` con `{domain}` responde `201`.
- El item tiene `site_id=null`, `container=null`, `published=false`.
- `GET /domains` incluye el dominio reservado.
- No se crea ningún archivo `spanel-*.yml`.
- `DELETE` del reservado responde `204` y la fila desaparece.
- Repetir la reserva del mismo tenant es idempotente; otro tenant recibe `409`.

### V2 — Vínculo posterior

- `POST /domains/{domain}/link` con un sitio del tenant responde `200`.
- El item pasa a `published=true` y `container` es el del sitio.
- El YAML contiene el marcador, el Host y el resolver actuales.
- Un `site_id` ajeno responde `404`; un primario responde `409`.
- Si el proxy falla, responde `503` y la fila sigue reservada.

### V3 — Compatibilidad

- `POST /domains` con `{site_id, domain, www}` conserva el comportamiento y la
  respuesta actual.
- Primarios siguen en solo lectura y `DELETE` de primario sigue en `409`.
- Extras existentes se siguen listando y quitando igual.
- Mail acepta el dominio reservado; Hosting crea sitio con él.
- `test_list_does_not_invent_unpublished_owned_domains` se actualiza: ahora el
  dominio propio no publicado **sí** se lista como reservado.

### V4 — Docker local (obligatorio antes de desplegar)

> Este entorno de desarrollo puede no tener Docker. La ejecución real se hace
> en un host local con Docker y laboratorio Traefik; no en producción.

```bash
docker compose build app
docker compose up -d postgres redis
./scripts/traefik-lab.sh up

# App con transporte local o Docker del laboratorio, según el host
SPANEL_LIVE_TESTS=1 \
  .venv/bin/python -m pytest scripts/tests/test_domains_independent_live.py -q
```

La prueba live debe demostrar:

1. Reservar `lab-reserved.spanel` y comprobar `201`, fila en `tenant_domains`
   y **ausencia** de `spanel-*.yml` nuevo.
2. Vincularlo al sitio de laboratorio y comprobar router `@file` habilitado,
   certificado de la CA interna y HTTPS 200.
3. Comparar hash de todos los `spanel-*.yml` existentes antes/después del
   vínculo: solo cambia el archivo del sitio vinculado.
4. Desvincular (`DELETE`) y comprobar que la ruta desaparece y la reserva
   permanece.
5. Volver a reservar/liberar y comprobar `tenant_domains` sin la fila.
6. `docker inspect` de Traefik y del sitio: `StartedAt` sin cambios.

## ROLLBACK

- No hay migración de esquema: revertir el commit elimina la ruta de vínculo y
  la UI nueva.
- Las filas reservadas creadas por la versión nueva siguen siendo filas válidas
  de `tenant_domains`; no bloquean rutas ni certificados. Se pueden liberar con
  el endpoint de tenant o dejarlas como propiedad del tenant.
- Los YAML creados por un vínculo se pueden eliminar con el mismo `DELETE`
  (quita la ruta) o restaurando el backup de `SPANEL_PROXY_BACKUPS_DIR`.
- No se toca producción ni se requiere downtime.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0040-dominios-independientes.md
    - vendor/systutor-core/src/systutor/contracts/domain_namespace.py
    - vendor/systutor-core/src/systutor/contracts/README.md
    - vendor/systutor-core/plugins/tenant/backend/register.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/tenant/backend/tests/**
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/tests/**
    - vendor/systutor-core/plugins/domains/frontend/api.ts
    - vendor/systutor-core/plugins/domains/frontend/pages/DomainsPage.tsx
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - vendor/systutor-core/plugins/mail/backend/tests/**
    - scripts/tests/test_domains_api.py
    - scripts/tests/test_domains_independent_live.py
    - README.md
    - docs/tenant-domains.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/src/systutor/core/**
    - vendor/systutor-core/plugins/domains/backend/proxy/**
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/**
    - vendor/systutor-core/plugins/tenant/migrations/**
    - vendor/systutor-core/plugins/domains/migrations/**
    - docker-compose.yml
    - scripts/spanel_cert_agent/**
    - scripts/traefik-lab/**
    - scripts/mail_cert.py
    - systutor-installer/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - POST /api/v1/plugins/domains (site_id opcional)
    - POST /api/v1/plugins/domains/{domain}/link (nuevo)
    - GET /api/v1/plugins/domains (reservados y aviso de proxy)
    - DELETE /api/v1/plugins/domains/{domain} (liberar reserva)
    - UI /app/domains
    - contrato domain_namespace (release opcional)
  indirect:
    - disponibilidad de dominios para mail
    - creación de sitios con dominio reservado
    - auditoría de dominios
    - navegación y permisos de Dominios
  must_not_affect:
    - rutas y certificados de sitios existentes
    - YAML gestionados existentes y archivos ajenos
    - dominio primario de tenant y cuentas de correo existentes
    - contenedores, Traefik, StartedAt y volúmenes
    - aislamiento por tenant, autenticación y RBAC
    - contratos públicos existentes de hosting y mail
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013
    - 0027
    - 0030
    - 0037
  must_compose_with:
    - plugin domains (proxy y certificados)
    - plugin tenant (tenant_domains y dominio de correo)
    - plugin hosting (resolución de sitio y dominio primario)
    - plugin mail (conjunto de dominios del tenant)
  systemic_invariants:
    - un dueño por Host
    - un dueño por dominio
    - reservar no publica
    - vincular publica con el mismo escritor
    - aislamiento por tenant
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest vendor/systutor-core/plugins/domains/backend/tests vendor/systutor-core/plugins/tenant/backend/tests vendor/systutor-core/plugins/hosting/backend/tests vendor/systutor-core/plugins/mail/backend/tests -q
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest scripts/tests/test_domains_api.py scripts/tests/test_proxy_domain.py -q
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit && node node_modules/vite/bin/vite.js build
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: reservar es propiedad; publicar es proxy. El servicio de dominios es el único que decide, el router HTTP queda delgado.
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/src/systutor/contracts/domain_namespace.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
```

## Traceability

- Requirement: crear dominios independientes sin `site_id` ni contenedor y
  vincularlos después a sitios, Docker, correo o servicios externos.
- owner: luc444s
- approver: luc444s
- Commit:
- Deployment: local Docker + laboratorio Traefik; producción queda fuera de
  esta ejecución.

## Definition of Done

- [ ] Objetivo satisfecho: alta sin sitio y vínculo posterior.
- [ ] Alcance respetado; sin tabla ni migración nueva.
- [ ] Contrato de API y UI cumplido.
- [ ] Verdad falsable: reserva no escribe Traefik; vínculo usa el mismo YAML.
- [ ] Invariantes I1–I10 preservados y probados.
- [ ] V0–V3 verdes; V4 ejecutado en Docker local o reportado como GAP explícito.
- [ ] Rollback honesto y sin downtime.
- [ ] Composición con Hosting, Mail, Tenant y Domains verificada.
- [ ] Sin cambios fuera de `change_surface`.
- [ ] Archivos bajo 600 líneas.
- [ ] Trazabilidad completa.
