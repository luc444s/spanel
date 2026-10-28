# Gestor de certificados TLS de correo

Implementa la A.SPEC 0035: inventario de certificados de correo, API en el
plugin `domains`, modal **Certificados TLS** en Hosting y emisión bajo demanda
mediante un agente privilegiado.

Certbot sigue siendo el dueño de la emisión y la renovación. Spanel **no**
implementa ACME, no guarda claves privadas y no recrea Docker Mailserver.

## Reparto de responsabilidades

| Componente | Puede | No puede |
|---|---|---|
| Web / API `domains` | leer `state.json`, escribir `requests/`, leer `jobs/` | leer `privkey.pem`, ejecutar Certbot, tocar Docker |
| `spanel-cert-agent` (root) | validar, ejecutar Certbot, instalar el par, generar SNI, recargar | aceptar flags, rutas o entorno desde la solicitud |
| Certbot del host | emitir y renovar | se invoca solo con argv fijo |

`cf-mail` sigue siendo el certificado predeterminado global. En esta fase no se
regenera ni se reinstala desde el panel.

## Rutas

| Ruta | Dueño / modo | Uso |
|---|---|---|
| `/var/lib/spanel/cert-manager/state.json` | `root:spanel-cert` `0640` | Inventario, solo metadatos y rutas |
| `/var/lib/spanel/cert-manager/requests/` | `spanel:spanel-cert` `0770` | La web escribe; el agente lee y borra |
| `/var/lib/spanel/cert-manager/jobs/` | `root:spanel-cert` `0750` | Resultado de cada solicitud |
| `/etc/spanel/cert-agent.toml` | `root:root` `0600` | Allowlist, Certbot y comandos permitidos |
| `/etc/ssl/spanel/mail/<hostname>/fullchain.pem` | `root:root` `0644` | Cadena instalada |
| `/etc/ssl/spanel/mail/<hostname>/privkey.pem` | `root:root` `0600` | Clave instalada |

La web solo escribe `requests/`. El agente publica `state.json` y `jobs/` sin
contenido de claves. Un `state.json` que contenga `PRIVATE KEY` se rechaza
completo y la API responde `503`.

## Configuración del agente

```toml
# /etc/spanel/cert-agent.toml
allowlist = ["mail.systutor.com", "mail.legalattorney.pe"]
default_certificate = "cf-mail"
default_cert_path = "/etc/ssl/cf-mail.fullchain.crt"
default_key_path = "/etc/ssl/cf-mail.key"
install_root = "/etc/ssl/spanel/mail"
state_path = "/var/lib/spanel/cert-manager/state.json"
requests_dir = "/var/lib/spanel/cert-manager/requests"
jobs_dir = "/var/lib/spanel/cert-manager/jobs"
renew_days = 30

[certbot]
executable = "/usr/bin/certbot"
challenge = "webroot"          # auto | webroot | dns-01
webroot_path = "/var/www/html"
# dns_plugin = "cloudflare"    # solo con challenge = "dns-01"
lineage_root = "/etc/letsencrypt/live"

[postfix]
sni_map_path = "/etc/postfix/sni_map"
postmap_command = ["postmap", "/etc/postfix/sni_map"]
reload_command = ["postfix", "reload"]

[dovecot]
local_name_path = "/etc/dovecot/conf.d/99-spanel-local_name.conf"
reload_command = ["dovecot", "reload"]
```

Variables de entorno equivalentes (ganan sobre el TOML):

`SPANEL_CERT_AGENT_CONFIG`, `SPANEL_CERT_ALLOWLIST`, `SPANEL_CERT_STATE_PATH`,
`SPANEL_CERT_REQUESTS_DIR`, `SPANEL_CERT_JOBS_DIR`, `SPANEL_CERT_INSTALL_ROOT`,
`SPANEL_CERT_DEFAULT_NAME`, `SPANEL_CERT_DEFAULT_CERT_PATH`,
`SPANEL_CERT_DEFAULT_KEY_PATH`, `SPANEL_CERT_RENEW_DAYS`,
`SPANEL_CERTBOT_EXECUTABLE`, `SPANEL_CERTBOT_CHALLENGE`,
`SPANEL_CERTBOT_WEBROOT`, `SPANEL_CERTBOT_DNS_PLUGIN`,
`SPANEL_CERTBOT_LINEAGE_ROOT`, `SPANEL_CERTBOT_CA_FILE`,
`SPANEL_POSTFIX_SNI_MAP`, `SPANEL_POSTFIX_POSTMAP_CMD`,
`SPANEL_POSTFIX_RELOAD_CMD`, `SPANEL_DOVECOT_LOCAL_NAME`,
`SPANEL_DOVECOT_RELOAD_CMD`.

Sin `sni_map_path`, `postmap_command`, `local_name_path` o los `reload_command`
el agente **falla cerrado antes de ejecutar Certbot**. No inventa rutas ni
comandos.

El plugin web lee las rutas de estado/solicitudes/jobs de
`SPANEL_CERT_STATE_PATH`, `SPANEL_CERT_REQUESTS_DIR`, `SPANEL_CERT_JOBS_DIR`,
`SPANEL_CERT_DEFAULT_NAME`, `SPANEL_CERT_RENEW_DAYS` y
`SPANEL_CERT_STATE_MAX_AGE`. La antigüedad máxima por defecto es 900 s; pasado
ese umbral el inventario se marca `stale` y la UI muestra “Estado no disponible”.

## Instalación

```bash
# Código (ejemplo)
install -d /opt/spanel/scripts
cp -a scripts/spanel_cert_agent /opt/spanel/scripts/
install -d /opt/spanel/docs
cp docs/certificate-manager.md /opt/spanel/docs/

# Configuración y directorios
install -d -m 0750 -o root -g spanel-cert /var/lib/spanel/cert-manager/jobs
install -d -m 0770 -o spanel -g spanel-cert /var/lib/spanel/cert-manager/requests
install -d -m 0755 /etc/ssl/spanel/mail
install -m 0600 -o root -g root cert-agent.toml /etc/spanel/cert-agent.toml

# Unidades
install -m 0644 scripts/systemd/spanel-cert-agent.service /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-agent.path /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-agent.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now spanel-cert-agent.path
systemctl enable --now spanel-cert-agent.timer
```

Comprobación manual:

```bash
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent refresh \
  --config /etc/spanel/cert-agent.toml
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent status \
  --config /etc/spanel/cert-agent.toml --json
```

`run-once` es la unidad que ejecuta systemd. Procesa todas las solicitudes
pendientes, escribe un job por cada una y refresca `state.json`.

## Comportamiento de emisión

| Acción | Certbot |
|---|---|
| `ensure` (linaje existente) | `certbot renew --cert-name <hostname>` |
| `ensure` (sin linaje) | `certbot certonly --cert-name <hostname> -d <hostname>` |
| `force` | `certbot certonly --force-renewal --cert-name <hostname> -d <hostname>` |

El método de desafío sale de la configuración (`auto`, `webroot`, `dns-01`), no
de la solicitud. El agente rechaza campos extra, cualquier flag
(`--pre-hook`, `--post-hook`, `--deploy-hook`, `--force-renewal` en `ensure`),
rutas y variables de entorno. Solo ejecuta Certbot para hostnames de la
allowlist.

Antes de instalar valida:

- que la clave privada corresponda al certificado;
- que el SAN contenga el hostname;
- que las fechas sean válidas y no esté vencido;
- la cadena si `ca_file` está configurado.

Solo después copia `fullchain.pem` (`0644`) y `privkey.pem` (`0600`) a
`/etc/ssl/spanel/mail/<hostname>/`, regenera el mapa SNI de Postfix, el bloque
`local_name` de Dovecot y ejecuta las recargas configuradas. El contenedor de
Docker Mailserver no se recrea.

`cf-mail` no tiene ruta de instalación en el agente: se lee desde
`default_cert_path`/`default_key_path` y se publica como `scope="global"`,
`deployment="default"`. Ningún certificado SNI lo reemplaza para conexiones sin
SNI.

## API

Namespace `/api/v1/plugins/domains/certificates`.

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/certificates` | `domains.certificates.read` |
| GET | `/certificates/{hostname}` | `domains.certificates.read` |
| GET | `/certificates/{hostname}/jobs/{job_id}` | `domains.certificates.read` |
| POST | `/certificates/{hostname}/request` | `domains.certificates.manage` |
| POST | `/certificates/refresh` | `domains.certificates.manage` |

`POST .../request` acepta `{"action":"ensure"|"force","reason":"..."}`, escribe
`requests/<uuid>.json` de forma atómica con modo `0600` y responde `202`:

```json
{
  "job_id": "uuid",
  "hostname": "mail.systutor.com",
  "state": "queued",
  "poll_url": "/api/v1/plugins/domains/certificates/mail.systutor.com/jobs/uuid"
}
```

Una segunda solicitud con un job activo para el mismo hostname responde `409`
con el `job_id` existente. `cf-mail` responde `409`: no se regenera en esta
fase.

`GET /certificates` devuelve `items`, `stale`, `generated_at`, `agent_last_seen`
y `error`. Un tenant ve solo `mail.<dominio>` de sus dominios de Hosting; los
hostnames sin dominio asociado y `cf-mail` solo los ve el administrador de
infraestructura (`core.platform.admin`). Un hostname de otro tenant responde
`404`, no `403`, para no filtrar su existencia.

El endpoint existente `GET /{domain}/certificate` (certificado web del proxy) no
cambia.

## Permisos

| Permiso | Quién | Alcance |
|---|---|---|
| `domains.certificates.read` | admin de infraestructura y tenants | propios dominios; admin ve todo |
| `domains.certificates.manage` | solo admin de infraestructura | solicitar, forzar y refrescar |

`domains.certificates.manage` **no** está en `tenant_admin_permissions`. Un
tenant puede consultar pero no ejecutar Certbot.

## UI

En **Hosting**, doble clic sobre una fila abre **Certificados TLS** (subtítulo
**Spanel · Infraestructura de correo**). La misma acción está en el detalle del
sitio como botón **Certificados TLS**, para teclado y móvil.

Cada fila muestra hostname, fuente/despliegue (`Certbot · SNI`,
`Global · Predeterminado`), estado (`Válido`, `Por vencer`, `Vencido`, `Sin
certificado`, `Pendiente`, `Error`), vencimiento y acciones. **Regenerar** usa
`ensure`; **Forzar** pide confirmación con advertencia de rate limit de Let’s
Encrypt. Los botones se deshabilitan sin `domains.certificates.manage`, con un
job activo, para `cf-mail` o cuando el estado está viejo. Un fallo del agente se
muestra una vez y no se reintenta en bucle.

## Operación

- Un certificado por hostname; los SAN múltiples se aceptan en el modelo.
- La allowlist del agente debe coincidir con los hostnames del inventario.
- El timer refresca `state.json` cada 10 minutos aunque no haya solicitudes.
- Los logs del agente no contienen claves. Los jobs solo guardan mensajes y
  metadatos.
- Si el agente está caído, la UI muestra “Estado no disponible” y no habilita
  acciones destructivas.
- `state.json` y `jobs/` se conservan para auditoría.

## Rollback (alcance 0035)

1. Revertir el commit elimina el modal, la API, el paquete del agente y las
   unidades. Los certificados existentes y `cf-mail` no se tocan.
2. Desinstalar unidades y conservar estado/jobs:

   ```bash
   systemctl disable --now spanel-cert-agent.path spanel-cert-agent.timer
   rm /etc/systemd/system/spanel-cert-agent.{path,service,timer}
   systemctl daemon-reload
   ```

3. Si se instaló un certificado SNI nuevo, restaurar el mapa de Postfix y el
   bloque de Dovecot desde `<archivo>.spanel-bak` (el agente guarda una copia
   antes de cada escritura), recargar Postfix y Dovecot. No recrear el
   contenedor.
4. `cf-mail` no se modifica en esta fase: no hay rollback del certificado
   predeterminado.

No forma parte de 0035: verificación continua programada, rollback automático,
deploy hook y alertas (A.SPEC 0036).

## Pruebas

```bash
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/domains/backend/tests \
  vendor/systutor-core/plugins/hosting/backend/tests -q

.venv/bin/python -m pytest scripts/spanel_cert_agent/tests -q
```

Los tests del agente usan `tmp_path`, un runner falso de comandos y no tocan
red, Docker, root, `/etc` ni Certbot real.
