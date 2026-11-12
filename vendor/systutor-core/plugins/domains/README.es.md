# Dominios

[English](README.md) | Español

Plugin de administración de dominios web.

## Propiedad

- El **dominio primario** de un sitio lo escribe Hosting (label de Traefik y
  `HostingSite.domain`). Este plugin lo muestra en solo lectura.
- Los **dominios extra** los escribe este plugin en `spanel-<slug>.yml` dentro
  del directorio dinámico de Traefik, con el marcador `# managed-by: spanel`.
- El **dominio de correo** pertenece a Tenant/Mail. Acá solo se leen cuentas
  para mostrar el conteo.

El contrato `systutor.contracts.domain_ownership` es el único puente con
Hosting: publica primarios y consulta si un dominio ya es un extra. Ningún
plugin importa modelos del otro.

## Reservar y vincular (A.SPEC 0040)

Un dominio se puede crear **sin sitio**: se normaliza y reclama la propiedad en
`tenant_domains` sin escribir ningún `spanel-*.yml`, sin sondear certificados y
sin ejecutar Docker. El item se lista con `published=false` y el certificado
responde `unpublished`. Después se publica con
`POST /domains/{domain}/link` contra un sitio del tenant; el vínculo usa el
mismo `ProxyOperations.add()` que el alta con sitio (mismo YAML, prioridad y
resolver) y conserva la reserva si el proxy falla.

- `DELETE` de un **extra publicado** quita la ruta como siempre.
- `DELETE` de un **reservado** libera la fila con el provider `release` del
  contrato `domain_namespace` (registration opcional en
  `register_domain_providers`); si el dominio es el correo del tenant el
  provider responde `409 tenant_domain_in_use`.
- `GET /domains` es **tolerante a un proxy caído**: devuelve los dominios con
  propiedad registrada y `proxy_available=false` + `proxy_error`.

## API

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/api/v1/plugins/domains` | `domains.domains.read` |
| POST | `/api/v1/plugins/domains` | `domains.domains.manage` |
| POST | `/api/v1/plugins/domains/{domain}/link` | `domains.domains.manage` |
| DELETE | `/api/v1/plugins/domains/{domain}` | `domains.domains.manage` |
| GET | `/api/v1/plugins/domains/{domain}/certificate` | `domains.domains.read` |

## Configuración

Reutiliza `SPANEL_PROXY_*` del CLI (`scripts/proxy_domain.py`). Las variables
propias son `SPANEL_DOMAINS_PROFILE` (default `public`) y
`SPANEL_PROXY_PROBE_HOST` / `SPANEL_PROXY_PROBE_TIMEOUT` para el sondeo.

### Transporte (A.SPEC 0029)

`SPANEL_PROXY_TRANSPORT` vale `auto` (default), `local`, `ssh` o `docker`.

- `auto` prueba en orden: SSH configurado (`SPANEL_PROXY_SSH_HOST`), Docker
  con un Traefik escribible, y por último `SPANEL_PROXY_CONFIG_DIR` local.
  Si nada aplica responde `503 domains.proxy_unavailable` detallando cada
  comprobación.
- `docker` detecta el contenedor Traefik, lee su config estática dentro del
  contenedor y escribe los `spanel-*.yml` con `docker exec` en el bind mount
  del file provider. No hace falta montar el directorio ni configurar SSH.
- Un modo explícito que falla no cae a otro en silencio.

Overrides del transporte Docker:

| Variable | Efecto |
|---|---|
| `SPANEL_PROXY_TRAEFIK_CONTAINER` | nombre exacto del contenedor Traefik |
| `SPANEL_PROXY_CONFIG_DIR` | directorio **dentro de Traefik** (override) |
| `SPANEL_PROXY_PROBE_HOST` | host de sondeo cuando no hay red compartida |
| `SPANEL_PROXY_CERT_RESOLVER` | gana sobre el resolver detectado |

`doctor` informa `transporte: docker|local|ssh (motivo)`. Sin un transporte
viable, las operaciones responden `503 domains.proxy_unavailable`.

## CLI

`python3 scripts/proxy_domain.py` conserva los comandos `doctor`, `list`,
`add`, `status` y `remove`. La implementación vive en
`backend/proxy/`; el script es un shim.

## DNS autoritativo (A.SPEC 0067)

El plugin opera PowerDNS Authoritative mediante un único `PowerDnsClient`
compartido. El CLI y FastAPI usan el mismo cliente, validación, versionado y
soporte de zone files; ninguno ejecuta comandos externos ni implementa otra
ruta HTTP.

Configuración del host:

```text
SPANEL_PDNS_API_URL
SPANEL_PDNS_API_KEY_FILE
SPANEL_PDNS_SERVER_ID=localhost
SPANEL_PDNS_TIMEOUT_SECONDS=5
```

El archivo de clave debe ser regular y solo legible por su dueño. No existe
fallback de clave en texto plano; la URL y la clave nunca se devuelven por la
API, no se guardan en la base, no se registran y no aparecen en el JSON del CLI.

PostgreSQL es dueño de la propiedad tenant-zona (`domains_dns_zones`) y del
contrato público de nameservers (`domains_dns_settings`). PowerDNS es dueño de
SOA, NS y cada RRset. Una zona pertenece a un solo tenant. `ns1` y `ns2` pueden
compartir una dirección y la API siempre informa que **no es redundancia
física**.

### CLI

```text
python3 scripts/spanel_dns.py doctor
python3 scripts/spanel_dns.py nameservers show|plan|apply|verify
python3 scripts/spanel_dns.py zones list|create|show|delete
python3 scripts/spanel_dns.py rrsets list|plan|apply|delete
python3 scripts/spanel_dns.py zone import|export
```

Las mutaciones son planes salvo que se pase `--apply`. El borrado destructivo
exige `--confirm-zone` o `--confirm-name` cuando stdin no es interactivo.
Códigos de salida: `0` éxito/sin drift, `2` validación, `3` drift/plan sin
aplicar, `4` conflicto, `5` PowerDNS no disponible, `6` propiedad/datos.
`--json` es estable y ordenado.

`nameservers verify --zone ... --resolver ... --port ...` realiza consultas
reales SOA/NS por UDP y TCP.

### API

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/api/v1/plugins/domains/dns/doctor` | `domains.dns.read` |
| GET | `/api/v1/plugins/domains/dns/nameservers` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/nameservers/plan` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/nameservers/apply` | `domains.dns.nameservers` + `core.platform.admin` |
| POST | `/api/v1/plugins/domains/dns/nameservers/verify` | `domains.dns.read` |
| GET/POST | `/api/v1/plugins/domains/dns/zones` | `domains.dns.read` / `domains.dns.zones` |
| GET/DELETE | `/api/v1/plugins/domains/dns/zones/{zone_name}` | `domains.dns.read` / `domains.dns.zones` |
| GET | `/api/v1/plugins/domains/dns/resolve?domain=...` | `domains.dns.read` |
| GET | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/plan` | `domains.dns.records` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/apply` | `domains.dns.records` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/delete` | `domains.dns.records` |
| POST/GET | `/api/v1/plugins/domains/dns/zones/{zone_name}/import` / `export` | `domains.dns.records` / `domains.dns.read` |

Los administradores de tenant reciben lectura, records y zonas por defecto.
Nunca reciben configuración de nameservers. Toda escritura exige
`expected_version`; una zona desactualizada o cambiada externamente devuelve
`409`. Los valores TXT nunca se copian a auditoría: solo owner, tipo, cantidad
y hash.
`nameservers plan` devuelve una entrada por zona aprobada con su huella actual
en `version` (también en `zone_versions`). `nameservers apply` exige
`--zone ZONA --zone-version ZONA=VERSION` para cada zona existente aprobada y
rechaza versiones faltantes o desactualizadas con conflicto antes de modificar
PowerDNS. `--no-zones` aprueba explícitamente un conjunto vacío, que es la vía
segura de bootstrap para persistir la configuración pública inicial; omitir
las opciones de zona nunca significa “todas las zonas”.

### Interfaz DNS de Dominios (A.SPEC 0068)

La página Dominios lee una sola vez la lista de zonas del tenant y muestra
`Gestionado`, `Externo`, `Sin zona` o `Error` para cada dominio. Una fila propia
abre su zona DNS con doble clic o `Enter`; deliberadamente no existe un botón
DNS por fila. El diálogo permite buscar y filtrar RRsets, planificar y aplicar
con la versión del plan, protege SOA/NS del apex, importa/exporta y exige el
nombre exacto para eliminar la zona. Los conflictos se muestran y nunca se
reintentan automáticamente.

El diálogo global **Servidores de nombres** muestra y verifica el estado por
UDP/TCP. Configurar exige `domains.dns.nameservers` y `core.platform.admin`,
planifica las zonas propias listadas explícitamente y siempre envía un conjunto
aprobado explícito. El conjunto vacío queda reservado al bootstrap inicial.
La delegación del registrador, DNSSEC y Cloudflare quedan fuera de la interfaz.
