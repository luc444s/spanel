# Gestor de certificados TLS de correo

Implementa las A.SPEC 0035 y 0036: inventario de certificados de correo, API en
el plugin `mail`, modal **Certificados TLS** en Correos, emisión bajo demanda
mediante un agente privilegiado, verificación continua, alertas, instalación
transaccional y rollback.

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
alerts_path = "/var/lib/spanel/cert-manager/alerts.json"
requests_dir = "/var/lib/spanel/cert-manager/requests"
jobs_dir = "/var/lib/spanel/cert-manager/jobs"
generations_root = "/var/lib/spanel/cert-manager/generations"
backup_root = "/var/lib/spanel/cert-manager/backups"
locks_dir = "/var/lib/spanel/cert-manager/locks"
renew_days = 30

[verification]
ports = [993, 465, 587]
ca_file = "/etc/ssl/certs/ca-certificates.crt"
timeout_seconds = 10
audit_interval_seconds = 600

[alerts]
warning_days = 21
critical_days = 7
# Canal externo, independiente del correo vigilado. Elegir uno:
webhook_command = ["/usr/local/bin/spanel-alert-webhook"]
# webhook_url = "https://alertas.example.com/spanel"
timeout_seconds = 10

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
`SPANEL_DOVECOT_RELOAD_CMD`, `SPANEL_CERT_ALERTS_PATH`,
`SPANEL_CERT_GENERATIONS_ROOT`, `SPANEL_CERT_BACKUP_ROOT`,
`SPANEL_CERT_LOCKS_DIR`, `SPANEL_CERT_PROBE_PORTS`,
`SPANEL_CERT_VERIFY_CA_FILE`, `SPANEL_CERT_PROBE_TIMEOUT`,
`SPANEL_CERT_AUDIT_INTERVAL`, `SPANEL_CERT_ALERT_WARN_DAYS`,
`SPANEL_CERT_ALERT_CRITICAL_DAYS`, `SPANEL_CERT_ALERT_WEBHOOK_CMD`,
`SPANEL_CERT_ALERT_WEBHOOK_URL`, `SPANEL_CERT_ALERT_TIMEOUT`.

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
install -m 0644 scripts/systemd/spanel-cert-audit.service /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-audit.timer /etc/systemd/system/
install -m 0644 scripts/systemd/spanel-cert-deploy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now spanel-cert-agent.path
systemctl enable --now spanel-cert-agent.timer
systemctl enable --now spanel-cert-audit.timer

# Deploy hook de Certbot (A.SPEC 0036)
install -m 0755 scripts/spanel_cert_agent/certbot-deploy-hook.sh \
  /etc/letsencrypt/renewal-hooks/deploy/spanel-cert-sync
```

`certbot.timer` sigue siendo el único programador de renovación. El deploy
hook **no** ejecuta `certbot renew` ni `certbot certonly`: solo deriva el
hostname de `RENEWED_LINEAGE` y llama `spanel-cert-agent sync --hostname`.
`spanel-cert-deploy.service` es el fallback oneshot que sincroniza todos los
linajes cuya huella cambió.

### Despliegue con Docker

La app lee el estado y escribe solicitudes en `/var/lib/spanel/cert-manager`.
El `docker-compose.yml` monta ese directorio en el contenedor `app`; sin el
mount la API responde `state_missing` y la UI muestra «Estado no disponible».
El contenedor corre como root, así que no hace falta crear el usuario `spanel`
en el host: alcanza con los directorios y permisos del agente.

El agente corre en el host porque necesita Certbot, escribir en
`/etc/ssl/spanel/mail` y recargar Postfix/Dovecot. Con Docker Mailserver, la
recarga se hace con `docker exec` y las rutas del mapa SNI deben ser las que el
contenedor ya monta (`/tmp/docker-mailserver/...`). Si el mapa SNI o el
`local_name` no están configurados, el agente falla cerrado y no emite.

### Comprobación manual

```bash
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent refresh \
  --config /etc/spanel/cert-agent.toml
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent status \
  --config /etc/spanel/cert-agent.toml --json
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent verify \
  --config /etc/spanel/cert-agent.toml --json
PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent audit \
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

## Verificación continua (0036)

`spanel-cert-audit.timer` ejecuta `spanel-cert-agent audit` cada
`verification.audit_interval_seconds` (10 min por defecto). La auditoría:

- valida clave/certificado, cadena, SAN, fechas y hash de cada linaje instalado;
- conecta a 993/465/587 con SNI (`server_hostname=<hostname>`,
  `check_hostname=True`, CA configurada) y comprueba SAN y serial/fingerprint
  contra el inventario;
- conecta **sin SNI** a los mismos puertos y exige que el serial sea el de
  `cf-mail`; si responde un certificado SNI, es fallo crítico;
- escribe `health` en `state.json` y `alerts.json`, sin tocar certificados ni
  recargar Postfix/Dovecot.

`verify` ejecuta los mismos probes sin escribir estado. La verificación se
considera fallida si falla cualquier puerto, la cadena, el SAN, la clave o la
vigencia.

Estado de salud (solo metadatos):

```json
{
  "generated_at": "2026-10-27T12:00:00Z",
  "agent": {"state": "ok", "last_run_at": "...", "interval_seconds": 600},
  "default": {"name": "cf-mail", "serial": "...", "no_sni_serial": "...", "ok": true},
  "certificates": [
    {
      "hostname": "mail.systutor.com",
      "serial": "...", "served_serial": "...", "no_sni_serial": "...",
      "days_left": 62, "sni": {"993": true, "465": true, "587": true},
      "no_sni": true, "ok": true,
      "last_verified_at": "...", "last_ok_at": "...",
      "consecutive_failures": 0, "last_error": null
    }
  ]
}
```

`consecutive_failures` se incrementa por hostname y vuelve a 0 al recuperar
`ok`. Un `generated_at` mayor que `2 × audit_interval_seconds` se considera
viejo y alerta. El estado nunca contiene claves, tokens ni PEM.

## Alertas

| Condición | Severidad |
|---|---|
| `days_left <= 21` | aviso |
| `days_left <= 7` o `<= 0` | crítico |
| SAN/hostname o clave incompatibles | crítico |
| Serial servido ≠ almacenado | crítico |
| Postfix (465/587) o Dovecot (993) falla | crítico |
| Agente sin ejecutar > 2 intervalos | crítico |
| `cf-mail` sin SNI no coincide | crítico |
| Instalación fallida | crítico |

Las alertas se registran en `alerts.json` y en el journal de systemd, y se
entregan por `alerts.webhook_command` (JSON por stdin) o `alerts.webhook_url`.
El canal **no** usa el SMTP vigilado: si el correo está caído, la alerta sale
igual. Se deduplican por código/hostname y se reenvían como máximo cada 6 horas
o cuando cambia la severidad.

## Instalación transaccional y rollback

`install` (y el `sync` del deploy hook) sigue este orden:

```text
1. lock no bloqueante por hostname
2. validar clave, cadena, SAN, fechas y EKU del material nuevo
3. copiar a generación staging
4. backup de generación activa, mapa SNI, snippet Dovecot y health previo
5. activar generación (symlink atómico + copia atómica a rutas estables)
6. regenerar mapa SNI y snippet local_name
7. postmap si corresponde
8. postfix reload + verificar 465/587
9. dovecot reload + verificar 993
10. verificar todos los SNI y el certificado sin SNI
11. commit: podar generaciones viejas (se conservan 3)
```

Si falla cualquier paso 5-10 se restauran generación, mapa y snippet; se
recargan **ambos** demonios y se verifican 465/587/993 y sin SNI. Un fallo de
poda o de métricas posteriores al commit **no** revierte el certificado activo.
Una instalación fallida entra en backoff exponencial (60 s hasta 1 h) y alerta;
no hay reintentos infinitos. Dos ejecuciones simultáneas se serializan por lock
y la segunda sale sin cambios.

`rollback` revierte explícitamente a la generación anterior, regenera el mapa y
el snippet, recarga Postfix y Dovecot y verifica. `cf-mail` no se instala ni se
revierte desde el panel.

El agente no toca cuentas, contraseñas, buzones ni maildir, no recrea el
contenedor y nunca ejecuta `certbot renew`/`certbot certonly` en las rutas
`sync`/`install`/`rollback`/`audit`/`verify`.

## API

Namespace `/api/v1/plugins/mail/certificates`.

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/certificates` | `mail.certificates.read` |
| GET | `/certificates/health` | `mail.certificates.read` |
| GET | `/certificates/alerts` | `mail.certificates.read` |
| GET | `/certificates/{hostname}` | `mail.certificates.read` |
| GET | `/certificates/{hostname}/jobs/{job_id}` | `mail.certificates.read` |
| POST | `/certificates/{hostname}/request` | `mail.certificates.manage` |
| POST | `/certificates/refresh` | `mail.certificates.manage` |
| POST | `/certificates/{hostname}/verify` | `mail.certificates.manage` + plataforma |
| POST | `/certificates/{hostname}/sync` | `mail.certificates.manage` + plataforma |
| POST | `/certificates/{hostname}/install` | `mail.certificates.manage` + plataforma |
| POST | `/certificates/{hostname}/rollback` | `mail.certificates.manage` + plataforma |

`POST .../request` acepta `{"action":"ensure"|"force","reason":"..."}`, escribe
`requests/<uuid>.json` de forma atómica con modo `0600` y responde `202`:

```json
{
  "job_id": "uuid",
  "hostname": "mail.systutor.com",
  "state": "queued",
  "poll_url": "/api/v1/plugins/mail/certificates/mail.systutor.com/jobs/uuid"
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

`GET /certificates/health` devuelve `agent_state`, `agent_last_run_at`,
`interval_seconds`, `stale`, `default` (solo admin), `certificates` con
`last_verified_at`, `last_ok_at`, `consecutive_failures`, `served_serial`,
`sni`/`no_sni` y contadores `critical_count`/`warning_count`.
`GET /certificates/alerts` devuelve solo alertas de hostnames visibles; las
alertas globales (por ejemplo agente viejo) y `cf-mail` son solo para admin.

Las acciones `verify|sync|install|rollback` son solo del administrador de
plataforma: un tenant con `mail.certificates.manage` recibe `403`; un
hostname ajeno responde `404`. Todas encolan un job y responden `202`, y el
resultado se consulta con `/jobs/{job_id}`. Cada acción se registra en auditoría
(`domains.certificate.verified|synced|installed|rolled_back`).

El endpoint existente `GET /{domain}/certificate` (certificado web del proxy) no
cambia.

## Permisos

| Permiso | Quién | Alcance |
|---|---|---|
| `mail.certificates.read` | admin de infraestructura y tenants | propios dominios; admin ve todo |
| `mail.certificates.manage` | solo admin de infraestructura | solicitar, forzar, refrescar, verificar, instalar, sincronizar y revertir |

`mail.certificates.manage` **no** está en `tenant_admin_permissions`. Un
tenant puede consultar pero no ejecutar Certbot ni acciones de infraestructura.
Las acciones 0036 exigen además `core.platform.admin`.

## UI

En **Correos**, el botón **Certificados TLS** abre el modal del mismo nombre
(subtítulo **Spanel · Infraestructura de correo**). Muestra solo los
certificados del dominio del tenant que se está viendo (`tenant_only=true`,
incluso para el admin de plataforma); Dominios conserva únicamente los
certificados web de Traefik.

La lista no lleva acciones por fila: doble clic sobre un certificado abre un
modal anidado con sus alertas, salud, seriales, probes SNI y las acciones.
**Regenerar** usa `ensure`; **Forzar** pide confirmación con advertencia de rate
limit de Let’s Encrypt. Los botones se deshabilitan sin
`mail.certificates.manage`, con un job activo, para `cf-mail` o cuando el estado
está viejo. Las alertas críticas bloquean solo lo destructivo. Un fallo del
agente se muestra una vez y no se reintenta en bucle.

Desde 0036 el modal muestra además **Estado del agente**, **Última
verificación**, salud por certificado (`ok`, serial servido, puertos SNI) y las
alertas críticas. El admin dispone de **Verificar**, **Sincronizar** y
**Revertir**. Si hay una alerta crítica (san/serial/puerto/`cf-mail`/agente
viejo), las acciones destructivas quedan deshabilitadas hasta resolverla.

## Operación

- Un certificado por hostname; los SAN múltiples se aceptan en el modelo.
- La allowlist del agente debe coincidir con los hostnames del inventario.
- El timer refresca `state.json` cada 10 minutos aunque no haya solicitudes.
- Los logs del agente no contienen claves. Los jobs solo guardan mensajes y
  metadatos.
- Si el agente está caído, la UI muestra “Estado no disponible” y no habilita
  acciones destructivas.
- `state.json` y `jobs/` se conservan para auditoría.

## Rollback (alcance 0036)

1. Revertir el commit elimina timers, hook, endpoints y acciones nuevas; los
   certificados existentes y `cf-mail` no se tocan.
2. Desinstalar unidades y hook y conservar estado/jobs/backups para auditoría:

   ```bash
   systemctl disable --now spanel-cert-audit.timer spanel-cert-deploy.service
   rm /etc/systemd/system/spanel-cert-audit.{service,timer}
   rm /etc/systemd/system/spanel-cert-deploy.service
   rm /etc/letsencrypt/renewal-hooks/deploy/spanel-cert-sync
   systemctl daemon-reload
   ```

3. Si una instalación quedó a medias, restaurar la última generación:

   ```bash
   PYTHONPATH=/opt/spanel/scripts python3 -m spanel_cert_agent rollback \
     --hostname mail.systutor.com --config /etc/spanel/cert-agent.toml
   ```

   El agente restaura generación, mapa SNI y snippet Dovecot, recarga ambos
   demonios y verifica 465/587/993 y sin SNI.
4. `cf-mail` no se modifica: no hay rollback del predeterminado.
5. Conservar `state.json`, `alerts.json`, `jobs/`, `generations/` y `backups/`
   para auditoría.

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
   bloque de Dovecot desde los backups de generación (`backups/<hostname>/`) o
   desde `<archivo>.spanel-bak`, recargar Postfix y Dovecot. No recrear el
   contenedor.
4. `cf-mail` no se modifica en esta fase: no hay rollback del certificado
   predeterminado.

## Pruebas

```bash
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/mail/backend/tests \
  vendor/systutor-core/plugins/hosting/backend/tests -q

PYTHONPATH=scripts .venv/bin/python -m pytest \
  scripts/spanel_cert_agent/tests -q
```

Los tests del agente usan `tmp_path`, un runner falso de comandos, un connector
TLS falso y no tocan red, Docker, root, `/etc` ni Certbot real.
