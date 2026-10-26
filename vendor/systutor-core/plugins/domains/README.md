# Dominios

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

## API

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/api/v1/plugins/domains` | `domains.domains.read` |
| POST | `/api/v1/plugins/domains` | `domains.domains.manage` |
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
