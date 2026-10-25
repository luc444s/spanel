# A.SPEC 0027 — Plugin de dominios: dominios extra del proxy con UI

> `risk: high` — la entrega escribe configuración que consume el Traefik real y
> decide a qué Host responde cada dominio. Una escritura incorrecta puede crear
> dos routers para el mismo Host, servir un certificado ajeno o dejar un sitio
> sin ruta. Exige `approver:` humano.
>
> Numeración: **0027**, reservada por 0025 y 0026 para el plugin `proxy`. La
> A.SPEC 0028 ya está ocupada por TLS de correo. El contrato genérico
> multi-integración queda para una futura 0029.

## WHY

Hoy el dominio de un sitio vive en cuatro lugares que se ignoran entre sí:

| Mecanismo | Quién lo escribe | Dónde |
|---|---|---|
| Labels `traefik.http.routers.*.rule=Host(...)` | Hosting, al crear el sitio | `plugins/hosting/backend/provisioner.py:175-179` |
| Archivo `spanel-<slug>.yml` | CLI de proxy | `scripts/spanel_proxy/*` → `scripts/traefik-lab/dynamic` o `/opt/stacks/traefik/config` |
| `HostingSite.domain` (unique) | Hosting | `plugins/hosting/backend/models.py:32` |
| `Tenant.domain` (unique) | Tenant / Mail | `kernel/tenants/models.py:26` |

Consecuencias concretas, verificadas en el repo:

- El renderer de archivos fija `priority: 200` (HTTP) y `100` (HTTPS)
  (`scripts/spanel_proxy/core.py:138,145`). Un router de archivo **le gana** al
  router por label del mismo Host. Si alguien agrega el dominio primario por
  CLI, quedan dos dueños, gana el archivo y el certificado puede ser otro. No
  es hipotético: `operations.add` solo comprueba que el dominio no esté en otro
  archivo (`operations.py:364-368`), nunca mira Hosting.
- El CLI vive en `scripts/`, que **no viaja en la imagen** (`Dockerfile:32` solo
  copia `plugins/`). La única forma de usarlo en producción es entrar al host.
- No hay UI: no se puede ver qué dominio apunta a qué sitio, ni el estado del
  certificado, ni cuántos buzones tiene.
- El código actual ya rozó el límite de tamaño: `operations.py` 694 líneas y
  `core.py` 465, sobre el máximo de 600 de `vendor/systutor-core/AGENTS.md`.

El objetivo es que exista **un dueño por ruta**, visible en una página, y que
agregar o quitar un dominio extra deje de requerir SSH.

## WHAT

Crear el plugin independiente `domains` con UI. El plugin administra **solo los
dominios extra** (los del YAML de Traefik) y muestra los primarios en modo
lectura. Una página lista todos los dominios del tenant con sitio, dueño,
certificado y cantidad de buzones. Agregar y quitar un extra es una operación
de API autorizada y auditada, ejecutada por la misma implementación que hoy usa
el CLI.

La verdad independiente y falsable es: **para cada Host existe exactamente un
mecanismo que lo escribe, el backend rechaza cualquier intento de cruzar ese
límite, y la UI lo muestra sin ambigüedad.**

## SCOPE

Entra:

- Plugin nuevo `vendor/systutor-core/plugins/domains/` con manifiesto, backend,
  frontend, permisos, eventos, pruebas propias y README.
- Mudanza de `scripts/spanel_proxy/` al plugin, dividida por responsabilidad,
  conservando comportamiento y pruebas.
- `scripts/proxy_domain.py` queda como entrada CLI delgada sobre el plugin.
- Contrato de kernel `domain_ownership` para que Hosting y Domains se consulten
  sin importarse.
- Guard de propiedad en las dos direcciones: Domains rechaza primarios; Hosting
  rechaza crear o adoptar un sitio con un dominio que ya es extra.
- API de listar, agregar y quitar extras, más estado de certificado por dominio.
- Página con lista, alta, baja y estado; primario en solo lectura.
- Conteo de buzones **en el frontend**, reutilizando el endpoint de `mail`.
- Pruebas unitarias, de API y live sobre el laboratorio.

No entra:

- Crear sitios web, cambiar el primario o el contenedor de un sitio.
- Escribir labels de Docker ni tocar contenedores.
- DNS público, delegación, MX, SPF/DKIM/DMARC ni certificados wildcard.
- Crear, borrar o cambiar contraseñas de buzones. Mail sigue siendo el dueño;
  acá solo se leen cuentas ya existentes.
- Buzones en dominios extra (hoy `mail` solo acepta `Tenant.domain`).
- Rediseñar Traefik, el laboratorio, `scripts/traefik-lab.sh` o el Traefik de
  producción más allá de escribir los `spanel-*.yml` gestionados.
- El contrato genérico multi-integración (0029) y el panel de DNS.

## T0 — LÍNEA BASE MEDIDA (2026-10-25, HEAD `5146220`)

| Dato | Valor medido |
|---|---|
| Escritura de primario | Labels en el `docker run` de Hosting, solo si `SPANEL_HOSTING_TRAEFIK` no es `false` (`provisioner.py:175-179`, `settings.py:58,100`) |
| Escritura de extras | Un archivo por contenedor con **todos** sus extras (`render_site_yaml`, `core.py:120`) |
| Prioridad | HTTP 200, HTTPS 100 (`core.py:138,145`) |
| Guard actual | Solo contra otro archivo gestionado (`operations.py:364-368`); ignora Hosting |
| Marcador de propiedad | `# managed-by: spanel`; se rechaza YAML ajeno (`core.py:157`) |
| Escritura segura | Temporal + `os.replace` local; `umask 022` + `mv` por SSH (`operations.py:159-174`) |
| Transporte | Local o SSH (`build_runner`, `core.py:347`) |
| Backups | `SPANEL_PROXY_BACKUPS_DIR/<timestamp>/<archivo>` antes de modificar |
| Tamaños | `operations.py` 694, `core.py` 465, `config.py` 140, CLI 136 |
| Pruebas existentes | `scripts/tests/test_proxy_domain.py`, 15 casos, sin Docker |
| App en producción | Contenedor con `docker.sock` y red `proxy`, **sin** mount de `/opt/stacks/traefik/config` (`docker-compose.yml`) |
| Consecuencia de despliegue | Escribir el YAML requiere SSH al host o un mount explícito; el certificado se sondea contra un host/puerto alcanzable desde la app |
| Mail | `mail` toma `Tenant.domain` y rechaza otro dominio (`mail/backend/service.py:84-94,182-188`) |

## CONTRACT

### 1. Modelo de propiedad

| Dominio | Dueño | Superficie | Escritura |
|---|---|---|---|
| Primario de un sitio | `hosting` | label del contenedor + `HostingSite.domain` | solo Hosting, al crear/adoptar |
| Extra de un sitio | `domains` | `spanel-<slug>.yml` con marcador | solo Domains |
| Dominio de buzón | `mail` | `Tenant.domain` | solo Tenant/Mail |
| Certificado | Traefik | resolver del perfil | nadie desde la app |

Reglas duras:

- **R1.** Un Host tiene un solo dueño. El espacio de nombres es **global**: un
  tenant no puede reclamar un dominio ya reclamado por otro.
- **R2.** Un extra no puede ser igual a ningún `HostingSite.domain`, ni de su
  tenant ni de otro.
- **R3.** Hosting no puede crear ni adoptar un sitio con un dominio que ya
  figura como extra, aunque el plugin `domains` esté deshabilitado en ese
  momento (ver R6).
- **R4.** Un dominio de buzón no se puede agregar como extra sin decidir antes
  qué pasa con el correo. En esta entrega el backend lo permite como ruta web,
  pero la UI lo advierte: el buzón seguirá viviendo en `Tenant.domain`.
- **R5.** Quitar un extra no toca el primario, el contenedor ni el sitio.
- **R6.** Si el plugin `domains` está deshabilitado, Hosting no tiene un checker
  registrado y no puede detectar extras: puede crear una colisión. El plugin,
  al habilitarse, la reporta como conflicto en la lista. Esta es la única
  ventana residual y queda declarada.

### 2. Contrato de kernel `domain_ownership`

Nuevo módulo `vendor/systutor-core/src/systutor/contracts/domain_ownership.py`,
mismo patrón que `file_targets.py` (registro en `app.state`, rechazo de
duplicados con otra implementación). No importa modelos de plugins.

```python
@dataclass(frozen=True)
class OwnedSite:
    site_id: str
    tenant_id: str
    container: str
    primary_domain: str | None

SiteProvider = Callable[[Session, str], Sequence[OwnedSite]]
ExtraClaimChecker = Callable[[Session, str], bool]

def register_site_provider(app, name, provider) -> None: ...
def list_sites(app, db, tenant_id) -> list[OwnedSite]: ...
def resolve_site(app, db, tenant_id, site_id) -> OwnedSite | None: ...
def is_primary_domain(app, db, domain) -> bool: ...  # global, para R2
def register_extra_claims(app, name, checker) -> None: ...
def is_extra_claimed(app, db, domain) -> bool: ...   # global, para R3
```

- Hosting registra `site_provider`: lee `hosting_sites` del tenant, devuelve
  `container` y `primary_domain`; nunca el resto de las columnas.
- Domains registra `extra_claims`: lee los `spanel-*.yml` gestionados y dice si
  el dominio está en alguno, global.
- `systutor/contracts/README.md` documenta el contrato (cambio de API pública).
- Hosting llama `is_extra_claimed` en `create_site` (antes de
  `provisioner.create`, `service.py:217-224`) y en adopción
  (`scripts/hosting_adopt.py`), y responde `409 hosting_site_conflict` con un
  mensaje que nombra el plugin de dominios.
- Domains llama `list_sites`/`resolve_site` para armar la lista y validar
  `site_id`, e `is_primary_domain` para R2. Ninguna de las dos consultas
  devuelve datos de otros tenants al cliente.

### 3. Plugin y mudanza del CLI

`vendor/systutor-core/plugins/domains/`:

```
plugin.json                 id=domains, permisos domains.domains.read / domains.domains.manage
backend/
  register.py               settings + router + registro de extra_claims
  settings.py               DomainsSettings desde SPANEL_PROXY_* + SPANEL_DOMAINS_*
  router.py                 API (delgada: valida, delega, audita)
  schemas.py                request/response con extra="forbid"
  service.py                casos de uso: listar, agregar, quitar, certificado
  proxy/
    config.py               Settings y perfiles
    core.py                 dominio, slug, reglas, YAML y escritura atómica
    transport.py            LocalRunner/SshRunner
    probe.py                probe_certificate
    operations.py           add/remove/list/status y utilidades
    doctor.py               comprobaciones de entorno
    labdns.py               DNS interno (solo perfil internal)
    wordpress.py            home/siteurl (solo CLI)
  tests/                    unitarias de contrato y servicio
frontend/
  register.ts               ruta /app/domains + navegación
  api.ts
  pages/DomainsPage.tsx
permissions/domains.json
README.md
```

- La lógica se mueve, no se reescribe: comportamiento, mensajes de error y
  marcador se conservan. `operations.py` se parte por operación para respetar
  400/600 líneas.
- `scripts/spanel_proxy/` se elimina. `scripts/proxy_domain.py` inserta
  `vendor/systutor-core` en `sys.path`, importa el paquete del plugin y conserva
  comandos, flags, salida y códigos de salida. Sigue siendo la entrada sin
  contenedor.
- Las superficies de WordPress (`--set-wordpress-url`) y DNS interno del
  laboratorio se conservan en el CLI. La API **no** expone flags: no se puede
  pedir por HTTP que se cambie la URL de WordPress. En perfil `internal`, el
  alta y la baja siguen actualizando el DNS del laboratorio como efecto interno,
  sin parámetro de entrada.
- `scripts/tests/test_proxy_domain.py` actualiza imports y sigue pasando sin
  Docker.

### 4. Configuración

| Variable | Default | Uso |
|---|---|---|
| `SPANEL_DOMAINS_PROFILE` | `public` en la app; `internal` en el CLI | perfil de validación y puertos |
| `SPANEL_PROXY_CONFIG_DIR` | `/opt/stacks/traefik/config` (público) | directorio de los `spanel-*.yml` |
| `SPANEL_PROXY_SSH_HOST` / `_USER` / `_PORT` / `_PASSWORD` | vacío | escribir por SSH; cae a `SPANEL_DOCKER_SSH_*` |
| `SPANEL_PROXY_PROBE_HOST` | `ssh_host` o `127.0.0.1` | dónde sondear el certificado (en la app, `traefik` o la IP del host) |
| `SPANEL_PROXY_HTTPS_PORT` | `443` público / `9443` interno | puerto del sondeo |
| `SPANEL_PROXY_CA_FILE` | vacío en público | CA para validar el certificado interno |
| `SPANEL_PROXY_PROBE_TIMEOUT` | `5` | segundos por sondeo |
| `SPANEL_PROXY_BACKUPS_DIR` | `/opt/stacks/traefik/backups` | respaldo antes de escribir |

Reglas:

- Sin `SPANEL_PROXY_SSH_HOST` ni un `SPANEL_PROXY_CONFIG_DIR` escribible, la API
  responde error de configuración (`503 domains_proxy_unavailable`), no intenta
  adivinar rutas.
- Si el certificado no valida y no hay CA configurada, el estado es
  `pending_cert` con el motivo. **Nunca** `verify=False` para reportar `active`.
- La app no monta el directorio del host: la vía por defecto en producción es
  SSH. Montarlo es una decisión de despliegue, no del plugin.

### 5. API

Base `/api/v1/plugins/domains`. Todo endpoint revalida usuario, tenant y
permiso; el `site_id` se resuelve contra Hosting, nunca se acepta contenedor.

| Método y ruta | Permiso | Resultado |
|---|---|---|
| `GET /domains` | `domains.domains.read` | `{items, sites}`: primarios y extras del tenant |
| `POST /domains` | `domains.domains.manage` | `201` con los dominios creados (incluye `www` si se pidió) |
| `DELETE /domains/{domain}` | `domains.domains.manage` | `204`; borra el dominio y su variante `www.` |
| `GET /domains/{domain}/certificate` | `domains.domains.read` | estado del certificado de un dominio conocido |

Item de lista:

```json
{
  "domain": "lucas.spanel",
  "site_id": "uuid",
  "container": "spanel-lucas-wp",
  "owner": "hosting",
  "editable": false,
  "conflict": false
}
```

- `owner`: `hosting` | `domains`.
- `editable`: `true` solo para extras y con `domains.domains.manage`.
- `conflict`: `true` si el mismo Host aparece en label y en YAML; se muestra la
  fila una vez, con dueño `hosting`. El alta se rechaza, pero la baja del extra
  sí se permite: es la forma de resolver el conflicto sin tocar el primario.
- `sites`: sitios del tenant con `site_id`, `container` y `primary_domain`, para
  el alta. Un sitio sin primario aparece igual y puede recibir extras.

`POST` acepta `{site_id, domain, www}` con `extra="forbid"` y responde:

| Código | `code` | Motivo |
|---|---|---|
| `409` | `domains.primary_owned` | el dominio es primario de un sitio |
| `409` | `domains.already_claimed` | ya está en otro sitio o tenant |
| `404` | `domains.site_not_found` | `site_id` inexistente o de otro tenant |
| `422` | `domains.invalid_domain` | normalización o perfil |
| `503` | `domains.proxy_unavailable` | sin SSH ni directorio escribible |

`DELETE` sobre un primario sin extra responde `409 domains.primary_owned` con el
mensaje "gestionado por Hosting"; nunca toca la label ni la fila de Hosting.
Sobre un dominio desconocido, `404`. Si el primario además está publicado como
extra (conflicto), el `DELETE` quita solo la reclamación del YAML.

Escritura: se resuelve el sitio, se lee el archivo existente, se valida el
marcador, se agrega o quita el dominio, se respalda y se escribe atómico. Si
falla el transporte, el archivo anterior queda intacto y la API devuelve `503`.

### 6. Certificados

- El sondeo reutiliza `probe_certificate` y devuelve `state` (`active`,
  `pending_cert`, `pending_dns`), `issuer`, `subject`, `not_after`, `sans`,
  `verified` y `error`.
- Solo se sondean dominios que ya aparecen en la lista (primario o extra del
  tenant). Un dominio arbitrario responde `404`: no es un prober abierto.
- La lista no sondea en bloque. La UI pide el estado por fila, con `staleTime`
  de 60 s y botón **Verificar** para forzar.
- Un tiempo de espera no se convierte en "válido": se informa el error.

### 7. Buzones

- Se leen desde el frontend con el endpoint existente de `mail`
  (`GET /api/v1/plugins/mail/accounts`), con el token del usuario. No hay
  llamada servidor-a-servidor ni import de `plugins.mail`.
- Si el usuario no tiene `mail.accounts.read`, la columna **Buzones** no se
  muestra.
- El conteo por dominio se calcula sobre las cuentas recibidas. Para un extra
  distinto de `Tenant.domain` se muestra `—` con la leyenda "el correo solo
  existe en el dominio del tenant". No se inventa un cero.

### 8. UI

Ruta `/app/domains`, título **Dominios**, permiso `domains.domains.read`.

Tabla: **Dominio | Sitio | Dueño | Certificado | Buzones | Acciones**.

- **Dueño**: `Hosting` (badge neutro, candado) o `Plugin` (badge de edición).
- **Certificado**: badge `Válido` / `Pendiente` / `Sin datos`, con vencimiento
  en el tooltip. Se carga por fila.
- **Acciones**: solo en extras y con permiso: **Quitar** con confirmación que
  nombra el dominio y el sitio. Primarios muestran "Gestionado por Hosting" y
  no ofrecen acciones.
- **Agregar dominio**: diálogo con selector de sitio (sitios del tenant),
  campo de dominio, casilla "agregar también www" y vista previa del router.
  Errores del backend se muestran tal cual, sin perder lo escrito.
- **Conflicto**: fila con badge rojo "Dos dueños" y la acción **Quitar extra**
  (solo con `domains.domains.manage`), que elimina la reclamación del YAML sin tocar el
  primario.
- Estados de carga, vacío ("todavía no hay dominios") y error de proxy
  (`503`) con el detalle de configuración.
- Estilos y componentes siguen `MailAccountsPage` y `HostingPage` (tabla,
  diálogos, toasts). Sin librerías nuevas.

### 9. Auditoría y eventos

- Cada alta y baja registra `record_audit` del kernel con `module="domains"`,
  `action="domains.domain.added"` / `.removed`, `entity_type="domain"`,
  `entity_id=<dominio>`, `details={"site_id", "container", "www"}`.
- Eventos del manifiesto: `domains.domain.added`, `domains.domain.removed`.
- Nunca se audita el YAML completo, credenciales SSH ni la salida de `docker`.
- El CLI conserva su log de texto (`SPANEL_PROXY_LOG`); la API usa auditoría del
  kernel, no escribe el log del CLI.

### 10. Entregables y orden

**E0 — Dueño de la ruta (bloqueante).**

1. Contrato `domain_ownership` + README + pruebas de registro/duplicados.
2. Hosting registra `site_provider` y consulta `is_extra_claimed` en creación y
   adopción; pruebas de ambos rechazos.
3. Mudanza del código a `plugins/domains/backend/proxy/` con la división
   declarada; CLI como shim; `scripts/spanel_proxy/` eliminado; pruebas
   existentes verdes con imports nuevos.
4. Guard de Domains contra primarios y duplicados globales, con `doctor`
   reportando conflictos.

E0 se cierra antes de exponer endpoints de escritura o UI. Un E0 incompleto no
se compensa con la UI.

**E1 — Backend.** Settings, schemas, router, service, permisos, eventos,
auditoría, certificados, pruebas de API.

**E2 — Frontend.** Página, diálogos, conteo de buzones, estados y navegación.

**E3 — Live.** Prueba sobre el laboratorio con un sitio de laboratorio:
agregar `lab-<algo>.spanel`, ver el router en Traefik, certificado `active`,
HTTPS 200, quitar y verificar que el primario y los demás archivos no cambiaron.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Cada Host tiene un solo dueño; el backend rechaza cualquier cruce.
    proof: Pruebas de primario por API, extra por Hosting y colisión label+YAML; R2 se evalúa global, no por tenant.
  - id: I2
    statement: Ningún pedido puede escribir fuera de los spanel-*.yml gestionados.
    proof: site_id se resuelve contra Hosting; validate_managed_path + marcador antes de escribir; probar rutas y archivos ajenos.
  - id: I3
    statement: Un tenant no ve ni modifica dominios ni sitios de otro.
    proof: Pruebas con dos tenants y tokens reales; IDs ajenos responden 404.
  - id: I4
    statement: Un fallo de escritura o de red no deja el YAML a medias ni borra el anterior.
    proof: Temporal + rename; prueba con runner que falla en el mv/write; se compara el archivo antes y después.
  - id: I5
    statement: Quitar un extra no altera el primario, el contenedor ni otros dominios del mismo archivo.
    proof: Archivo con tres dominios; se quita uno y se comparan los restantes, el marcador y el router del primario.
  - id: I6
    statement: Deshabilitar el plugin no cambia el ruteo existente.
    proof: Deshabilitar y comprobar que los YAML siguen igual y Traefik mantiene los routers; la UI y la API desaparecen.
  - id: I7
    statement: La app no reinicia, recrea ni detiene Traefik ni los contenedores de sitios.
    proof: StartedAt de Traefik y de los sitios sin cambios; solo escritura de archivos; probar que no se invoca docker run/restart.
  - id: I8
    statement: No se filtran credenciales SSH, YAML completo ni datos de otros tenants en respuestas, logs o auditoría.
    proof: Revisión de logs/audit y pruebas de error; el detalle auditado se limita a site_id, contenedor y dominio.
  - id: I9
    statement: Un certificado solo se reporta activo si la cadena valida contra la CA configurada.
    proof: Prueba con CA correcta, CA ausente y certificado de otra CA; nunca verify=False para active.
```

## VERIFICATION

### V0 — Gates estáticos

```bash
cd vendor/systutor-core && ruff check . && python3 -m pyright
cd apps/web && npm run build
python3 -m pytest vendor/systutor-core/plugins/domains/backend/tests -q
python3 scripts/tests/test_proxy_domain.py
```

Ningún archivo nuevo supera 400 líneas (600 es el máximo duro).

### V1 — Unitarias del proxy mudado

- Normalización interna/pública, slug, render/parse, marcador, roundtrip.
- `add` idempotente, `remove` que reescribe y luego borra, `dry-run`.
- Rechazo de dominio ya presente en otro archivo.
- Escritura atómica y backup; fallo simulado no deja temporal.
- Los 15 casos actuales siguen pasando con los imports nuevos.

### V2 — API con SQLite y TestClient

`scripts/tests/test_domains_api.py` (sin Docker) y, con laboratorio,
`scripts/tests/test_domains_api_live.py`:

- Dos tenants, tokens reales, sitios reales en la tabla (sin Docker).
- `GET /domains` muestra primarios como `hosting` y extras como `domains`.
- `POST` con primario → `409 domains.primary_owned`.
- `POST` con dominio de otro tenant → `409 domains.already_claimed`.
- `POST` con `site_id` ajeno → `404`.
- `DELETE` de primario → `409`; de extra → `204` y el archivo pierde ese Host.
- Sin `domains.domains.manage` → `403`; plugin deshabilitado → `404`.
- Auditoría con tenant y actor correctos; sin secretos.

### V3 — Matriz de propiedad

| Acción | Resultado esperado |
|---|---|
| Domains agrega primario | `409`, sin escritura |
| Domains agrega dominio de otro tenant | `409`, sin escritura |
| Hosting crea sitio con extra existente | `409`, sin provisionar contenedor |
| Adopción con extra existente | `409`, sin insertar fila |
| Dos archivos con el mismo Host | la lista marca conflicto y el alta se rechaza |
| Plugin deshabilitado y luego habilitado | el conflicto se detecta, no se corrige solo |

### V4 — Certificado

- Dominio con certificado del laboratorio → `active`, `verified=true`, SAN
  correcto y `not_after`.
- Dominio sin ruta → `pending_cert` o `pending_dns`, con error legible.
- Dominio ajeno o inexistente → `404`.
- Sin `SPANEL_PROXY_CA_FILE` en perfil interno → no reporta `active`.

### V5 — Live en el laboratorio

Sobre `spanel-traefik-lab` y un sitio `spanel-lab-*`:

1. Registrar el sitio de laboratorio en Hosting (o adoptarlo) con su primario.
2. `POST` de `lab-extra.spanel` por la API.
3. Confirmar `spanel-<slug>.yml` con el marcador y solo ese extra.
4. Consultar la API de Traefik en `127.0.0.1:9081/api/http/routers`: el router
   `spanel-<slug>@file` está `enabled` y el primario sigue en su router.
5. `GET /domains/lab-extra.spanel/certificate` → `active` con la CA interna.
6. `curl --cacert scripts/traefik-lab/ca/root_ca.crt https://lab-extra.spanel:9443`
   responde 200.
7. `DELETE` y verificar router ausente, archivo sin el extra y primario intacto.
8. `docker inspect` de Traefik y del sitio: `StartedAt` sin cambios.

La prueba vive en `scripts/tests/test_domains_api_live.py` y se salta si el
laboratorio no está arriba; un laboratorio ausente es GAP, no PASS. Solo toca
recursos `spanel-lab-*` / `com.spanel.lab=true`.

### V6 — Regresión

- `pytest vendor/systutor-core/plugins/hosting/backend/tests -q`.
- `scripts/tests/test_hosting_api_live.py` (sin cambios de comportamiento salvo
  los nuevos rechazos).
- `scripts/tests/test_mail_cert.py` y `test_mail_cert_live.py` (mail no se toca).
- Prueba manual: un sitio existente conserva su certificado y su página.

### V7 — Rollback ejecutado

- Agregar un extra, quitar uno, y restaurar un backup; comparar archivo byte a
  byte con el original.
- Deshabilitar el plugin y comprobar que la API responde 404 y el ruteo sigue.
- Rehabilitar y comprobar que la lista reconstruye el estado desde los YAML.

## ROLLBACK

- Deshabilitar `domains` retira su UI, navegación y rutas; los `spanel-*.yml`
  quedan escritos y el ruteo no cambia.
- Para revertir del todo, quitar los extras con el CLI
  (`python3 scripts/proxy_domain.py remove --domain ...`) antes o después de
  deshabilitar, y restaurar los backups de `SPANEL_PROXY_BACKUPS_DIR` si hace
  falta.
- El guard de Hosting (`is_extra_claimed`) es una consulta opcional: sin el
  plugin registrado, Hosting vuelve al comportamiento actual. No hay migración
  de base ni tabla nueva, así que no hay downgrade.
- Revertir el commit de la mudanza restaura `scripts/spanel_proxy/`; las pruebas
  viejas vuelven a importar desde ahí.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/domains/**            # plugin nuevo
    - vendor/systutor-core/src/systutor/contracts/domain_ownership.py
    - vendor/systutor-core/src/systutor/contracts/README.md
    - vendor/systutor-core/README.md
    - vendor/systutor-core/plugins/hosting/backend/register.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - scripts/hosting_adopt.py
    - scripts/proxy_domain.py                            # shim
    - scripts/spanel_proxy/**                            # eliminado tras la mudanza
    - scripts/tests/test_proxy_domain.py
    - scripts/tests/test_domains_api.py                  # nuevo, sin Docker
    - scripts/tests/test_domains_api_live.py             # nuevo, opt-in
    - aspecs/0027-domains-plugin.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**        # sin cambios de kernel
    - vendor/systutor-core/plugins/mail/**               # mail no se toca
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py  # labels del primario intactos
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - scripts/traefik-lab.sh
    - docker-compose.yml                                 # sin mounts nuevos en esta entrega
    - /opt/stacks/traefik/** fuera de los spanel-*.yml y backups
    - vendor/systutor-core/plugins/hosting/frontend/**   # sin botones nuevos en Hosting
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - plugins/domains (nuevo)
    - contracts/domain_ownership
    - hosting create/adopt guard
    - scripts/proxy_domain.py
    - configuracion de Traefik de los sitios con extras
  indirect:
    - UI de administracion (nueva ruta y navegacion)
    - auditoria y eventos del kernel
    - build del frontend
  must_not_affect:
    - ruteo y certificados de los sitios existentes
    - plugin mail y sus cuentas
    - kernel auth y permisos existentes
    - Traefik: contenedor, procesos, StartedAt y archivos no gestionados
    - otros tenants y sus sitios
    - scripts/traefik-lab.sh y el laboratorio
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0012  # hosting core
    - 0014  # hosting provisioning
    - 0025  # CLI y laboratorio
    - 0026  # CA interna
  must_compose_with:
    - plugin hosting (site provider y claim guard)
    - plugin mail (solo lectura de cuentas desde el frontend)
  systemic_invariants:
    - un dueño por Host
    - deshabilitar un plugin no cambia el ruteo
    - aislamiento por tenant
  composition_checks:
    - pytest vendor/systutor-core/plugins/{hosting,mail,domains}/backend/tests -q
    - scripts/tests/test_hosting_api_live.py
    - scripts/tests/test_mail_cert.py
    - scripts/tests/test_domains_api_live.py
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: una responsabilidad coherente por modulo; la API no sabe de YAML y el proxy no sabe de HTTP
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - plugins/domains/backend/proxy/core.py
    - plugins/domains/backend/proxy/files.py
    - plugins/domains/backend/proxy/probe.py
    - plugins/domains/backend/service.py
    - src/systutor/contracts/domain_ownership.py
```

## ESTADO DE IMPLEMENTACIÓN (2026-10-25)

Implementado en esta rama:

- **E0.** Contrato `domain_ownership` con registro y consultas globales.
  Hosting publica sus sitios y rechaza crear un sitio con un dominio extra;
  `scripts/hosting_adopt.py` hace la misma comprobación. `scripts/spanel_proxy/`
  se eliminó y la lógica vive en `plugins/domains/backend/proxy/` (operations
  535 líneas, doctor/wordpress/labdns separados). El CLI `scripts/proxy_domain.py`
  es un shim con los mismos comandos y códigos de salida.
- **E1.** API `GET/POST /api/v1/plugins/domains`, `DELETE /{domain}` y
  `GET /{domain}/certificate`; permisos `domains.domains.read` y `domains.domains.manage`;
  auditoría del kernel; eventos declarados y publicados de forma best-effort;
  sondeo de certificado sin `verify=False`.
- **E2.** Página `/app/domains` con lista, dueño, conflicto, certificado por
  fila, buzones leídos de `mail` y diálogos de alta y baja.
- **E3.** `scripts/tests/test_domains_api_live.py` escrito para el laboratorio.

Puertas ejecutadas en este entorno (Termux, sin Docker ni laboratorio):

```text
python3 -m pytest plugins/domains/backend/tests plugins/hosting/backend/tests \
  plugins/mail/backend/tests scripts/tests/test_domains_api.py \
  scripts/tests/test_proxy_domain.py scripts/tests/test_mail_cert.py -q
                                    -> 218 passed
apps/web: tsc --noEmit              -> sin errores
apps/web: vite build                -> ok
```

No ejecutado acá: `ruff`, `pyright` (no instalados en Termux) y la suite live
`test_domains_api_live.py` (requiere `SPANEL_LIVE_TESTS=1` y el laboratorio).
El commit queda pendiente; Traceability se completa al commitear.

## Traceability

- Requirement: dominios extra del proxy administrados desde la UI, con un solo
  dueño por Host y sin tocar el primario de Hosting.
- owner: luc444s
- approver:
- Commit:
- Deployment: laboratorio Docker (`spanel-*`, `com.spanel.lab=true`) y luego
  el host de producción con el plugin habilitado.

## Definition of Done

- [ ] Objetivo cumplido: lista, alta y baja de extras desde la UI.
- [ ] Alcance respetado: sin sitios, DNS, buzones ni contrato genérico.
- [ ] Contrato cumplido: API y errores de la tabla.
- [ ] Verdad falsable: matriz de propiedad probada en las dos direcciones.
- [ ] Invariantes preservados (I1–I9).
- [ ] Verificación V0–V7 ejecutada; live con laboratorio real.
- [ ] Rollback probado, incluido deshabilitar el plugin.
- [ ] Checks de composición con Hosting y Mail.
- [ ] Sin cambios no relacionados; `scripts/spanel_proxy/` eliminado sin
      duplicar lógica.
- [ ] Restricciones estructurales: ningún archivo >600, ideal <400.
- [ ] Trazabilidad completa con commit literal.

## FUERA DE ALCANCE

- Panel de DNS, delegación, registros MX/TXT y certificados wildcard.
- Editar el dominio primario, recrear sitios o mover un sitio de contenedor.
- Buzones en dominios extra o cualquier cambio en `mail`.
- Historial de cambios de ruteo más allá de la auditoría del kernel.
- Migrar los labels del primario al plugin (eso sería el contrato de 0029).
- Montar `/opt/stacks/traefik` en el contenedor de la app.
