# A.SPEC 0035 — Gestor de certificados TLS de correo (inventario, UI y emisión bajo demanda)

> `risk: high` — crea solicitudes de emisión, ejecuta Certbot en el host y puede
> instalar certificados en Docker Mailserver. Un error deja el correo sin TLS o
> con un certificado equivocado. Exige `approver:` humano.
>
> Cubre las fases **0, 1, 2 y 3** del plan del gestor de certificados. La
> verificación continua, el rollback y la automatización quedan en A.SPEC 0036.

## WHY

Certbot ya funciona en el host de correo mediante `certbot.timer` y ya existen
certificados para `mail.systutor.com` y `mail.legalattorney.pe`. Spanel no
necesita emitir certificados públicos ni implementar ACME: necesita **verlos,
asociarlos a dominios, pedir su generación cuando falten y desplegarlos sin
romper el correo**.

Hoy no hay ninguna superficie en el panel para eso:

- `scripts/mail_cert.py` es hoy una CLI de laboratorio para un solo hostname con
  `step-ca`, no para el modelo de producción con `cf-mail` como predeterminado y
  certificados SNI por hostname. Su subcomando `audit` (Fase 0) ya aporta la
  validación de solo lectura que se reutiliza aquí.
- El plugin `domains` solo expone un `GET /{domain}/certificate` para el
  certificado web del proxy; no existe inventario de certificados de correo.
- La página de Hosting no permite ver ni solicitar el certificado de correo de
  un dominio.
- No hay un componente privilegiado limitado que pueda leer certificados y
  ejecutar recargas autorizadas sin darle a la aplicación web acceso
  indiscriminado a Docker ni a claves privadas.

El resultado esperado es que un administrador de infraestructura pueda abrir el
modal **Certificados TLS** desde una fila de Hosting, ver el estado real de
`mail.<dominio>` y pedir su emisión o regeneración. Los tenants solo consultan
el estado de sus propios dominios.

## BASE EXISTENTE — Fase 0: auditoría (ya implementada)

Antes de construir el inventario, la API y el agente, se implementó una
auditoría **de solo lectura** en `scripts/mail_cert.py`. Es la base de esta
spec: demuestra que el certificado actual del laboratorio está sano y define
las comprobaciones que el agente reutilizará.

Motivo del reparto de responsabilidades: **Certbot es dueño de la emisión y la
renovación**; Spanel solo monitoriza, valida y (más adelante) despliega. La
auditoría no emite, no instala y no recarga nada.

### Qué se agregó

| Archivo | Cambio |
|---|---|
| `scripts/mail_cert.py` | `audit [--json]`, `tls_check_identity()`, `public_key_matches()`, `audit_certificate()`, `cmd_audit()` |
| `scripts/tests/test_mail_cert.py` | tests de coincidencia de clave y de auditoría sin escrituras |

Comprobaciones de `audit` por certificado:

- presencia de `leaf.crt`, `privkey.key`, `fullchain.crt` y `meta.json`;
- serial y SAN del certificado contra `meta.json`;
- cadena contra `SPANEL_CA_ROOT`;
- vigencia, días restantes y porcentaje de vida contra el umbral de renovación;
- clave pública ↔ certificado (`public_key_matches`);
- hashes `sha256_fullchain` y `sha256_privkey` contra `meta.json`;
- montaje en el contenedor, `SSL_TYPE` y serial realmente servido;
- TLS con verificación de identidad (`-verify_hostname`) en 993, 465 y 587.

Códigos de salida: `0` OK, `2` validación, `4` transporte/Docker, `5` CA,
`7` despliegue, `8` verificación TLS. Una clave equivocada termina en `8`.

### Decisiones registradas

- Certbot corre en el mismo host; `certbot.timer` y los certificados existentes
  se conservan.
- Archivos locales + systemd; el agente privilegiado `spanel-cert-agent` tiene
  una allowlist mínima. El proceso web no recibe acceso amplio a Docker ni
  claves privadas.
- Un certificado por hostname; se admiten SANs, pero no son el modelo por
  defecto.
- `cf-mail` es el certificado predeterminado global, independiente de los
  tenants, hasta que exista una migración explícita.
- El desafío DNS-01 se mantiene independiente del proveedor; no se cambian los
  certificados webroot existentes sin necesidad; Cloudflare solo si sigue
  siendo el proveedor.
- Solo el administrador de infraestructura puede solicitar emisión o cambios;
  los tenants solo leen sus propios dominios.
- UI: doble clic en una fila de Hosting abre el modal “Certificados TLS”, con
  subtítulo “Spanel · Infraestructura de correo” y filas del tipo
  `mail.systutor.com — Certbot · SNI — Válido — Regenerar`.

### Verificación real de la Fase 0

En `lucas-ThinkPad-E570` (100.67.5.50), sin pytest disponible en el host:

```text
audit: spanel-mail
serial   AA7F4769716504ADE3B4F76A0D2EBDC0
SAN      mail.lucas.spanel, mail.spanel.local
TLS      993 OK · 465 OK · 587 OK
clave    coincide
salida   0
```

- Prueba negativa con clave incorrecta: salida `8` e informe de clave que no
  coincide.
- Local: `26 passed` en `scripts/tests/test_mail_cert.py`.
- `audit` usa `SPANEL_CA_ROOT`; producción necesita el bundle correcto (sistema
  o raíz interna).

Estado del repositorio: los cambios de la Fase 0 están en el árbol de trabajo,
**aún sin commit**. Esta spec los asume como base y no los reimplementa.

### Relación con las fases siguientes

- **0035 (esta spec)**: inventario + API + modal + emisión bajo demanda.
- **A.SPEC 0036**: verificación continua, rollback, automatización y permisos
  por tenant.

## WHAT

1. **Inventario de certificados de correo.** Un estado local, solo con
   metadatos, que describe:
   - el certificado predeterminado global `cf-mail`;
   - un certificado por hostname (`mail.<dominio>`), con fuente `certbot`,
     `static` o `step-ca`;
   - SANs, emisor, serial, fechas, huella, rutas y estado de despliegue
     (`default`, `sni`, `none`, `failed`);
   - estado de salud: `active`, `expiring`, `expired`, `missing`, `error`.

2. **API del gestor en el plugin `domains`.** Endpoints de lectura para el
   panel y un endpoint de solicitud de emisión limitado a administradores.

3. **Modal “Certificados TLS” en Hosting.** Se abre con doble clic sobre una
   fila de hosting (y con una acción accesible equivalente). Muestra el título
   “Certificados TLS”, el subtítulo “Spanel · Infraestructura de correo” y una
   fila por certificado con hostname, fuente, tipo de despliegue, estado y
   botón de acción.

4. **Solicitud de emisión por archivos locales + systemd.** El proceso web no
   ejecuta Certbot ni toca Docker. Escribe una solicitud pequeña y validada en
   un directorio vigilado; una unidad `systemd` la procesa con un componente
   privilegiado de superficie mínima.

5. **Componente privilegiado `spanel-cert-agent`.** Lee certificados, ejecuta
   Certbot con argumentos fijos y allowlist de hostnames, instala el par en
   rutas estables, actualiza los mapas SNI y ejecuta las recargas autorizadas.
   Nunca expone claves privadas a la web.

6. **Asociación dominio ↔ certificado.** `mail.<dominio>` se asocia al dominio
   existente en `domains`/`hosting`; `cf-mail` es un recurso global sin tenant.
   Un hostname que no se pueda asociar requiere intervención de un
   administrador de infraestructura.

7. **Permisos y auditoría.** `domains.certificates.read` para consultar los
   dominios propios; `domains.certificates.manage` solo para el administrador de
   infraestructura. Cada solicitud, resultado y fallo publica un evento
   auditable.

## BASE YA IMPLEMENTADA — comando `audit`

Antes de construir este gestor se implementó y probó la base de solo lectura en
`scripts/mail_cert.py`:

```bash
python3 scripts/mail_cert.py audit
python3 scripts/mail_cert.py audit --json
```

Comprueba, sin escribir ni recargar nada:

- existencia de `leaf.crt`, `fullchain.crt`, `privkey.key` y `meta.json`;
- serial y SAN contra `meta.json`;
- cadena contra la CA configurada;
- correspondencia entre clave privada y certificado;
- vencimiento y umbral de renovación;
- hash de `fullchain` y `privkey`;
- montaje en `/etc/dms/source`;
- serial realmente servido en 993;
- TLS en 993/465/587 con verificación de hostname (`-verify_hostname`).

Resultado en la ThinkPad `lucas-ThinkPad-E570` (`100.67.5.50`) sobre
`spanel-mail`:

```text
serial    AA7F4769716504ADE3B4F76A0D2EBDC0
sans      mail.lucas.spanel, mail.spanel.local
vigencia  2026-11-24T06:31:57+00:00 (27.1 días, 90%)
clave     coincide
cadena    ok
servido   serial AA7F4769716504ADE3B4F76A0D2EBDC0
montaje   sí
tls 993   ok
tls 465   ok
tls 587   ok
resultado: OK
```

Una prueba negativa con clave incorrecta devolvió exit 8 y detectó la
inconsistencia. La suite local pasó con 26 tests. El comando no emite, no
instala, no recarga y no recrea contenedores.

Esta base es el contrato de lectura que consumirá el gestor de certificados:
el agente y la API no deben inventar un estado distinto al que `audit` puede
verificar. `SPANEL_CA_ROOT` debe apuntar al bundle correcto en producción
(sistema o root de la CA interna).

## SCOPE

Entra:

- Nuevo módulo `vendor/systutor-core/plugins/domains/backend/certificates/`
  con inventario, modelo, validación, jobs y servicio.
- Endpoints nuevos bajo `/api/v1/plugins/domains/certificates`.
- Cambios mínimos en `domains/backend/router.py`, `schemas.py`, `service.py`,
  `register.py`, `plugin.json` y tests.
- Modal nuevo `CertificatesDialog` en el frontend de Hosting, reutilizando el
  `Dialog` existente; apertura por doble clic en `HostingPage`.
- Componente nuevo `scripts/spanel_cert_agent/` y unidades systemd:
  - `spanel-cert-agent.path`
  - `spanel-cert-agent.service`
  - `spanel-cert-agent.timer` (refresco de estado)
- Contrato de archivos:
  - `/var/lib/spanel/cert-manager/state.json` (metadatos, lectura web);
  - `/var/lib/spanel/cert-manager/requests/*.json` (solicitudes);
  - `/var/lib/spanel/cert-manager/jobs/*.json` (resultados).
- Documentación de operación y rollback.

No entra:

- Reimplementar la auditoría de la Fase 0: `audit`, `tls_check_identity`,
  `public_key_matches` y `audit_certificate` ya existen y se consumen tal cual.
- Implementar ACME dentro de Spanel.
- Gestionar cuentas, claves o desafíos DNS de Certbot desde la web.
- Emitir certificados para dominios que no existan en el inventario.
- Cambiar `cf-mail` por otro predeterminado.
- Recrear Docker Mailserver en cada emisión o renovación.
- Verificación continua programada, alertas y rollback automático
  (A.SPEC 0036).
- Migrar el buzón, cuentas, contraseñas o maildir.
- Wildcards como configuración predeterminada.

## CONTRACT

### Modelo de certificado

```text
Certificate
  hostname: str                  # mail.systutor.com
  name: str                      # mail.systutor.com | cf-mail
  scope: "global" | "tenant"
  domain: str | None             # systutor.com; null para cf-mail
  source: "certbot" | "static" | "step-ca"
  deployment: "default" | "sni" | "none" | "failed"
  status: "active" | "expiring" | "expired" | "missing" | "error"
  sans: list[str]
  issuer: str | None
  subject: str | None
  serial: str | None
  fingerprint: str | None
  not_before: str | None
  not_after: str | None
  cert_path: str | None          # ruta, nunca contenido
  key_path: str | None           # ruta, nunca contenido
  last_checked_at: str | None
  last_issued_at: str | None
  last_error: str | None
```

Reglas:

- La base de datos y el estado del agente guardan **solo metadatos y rutas**.
  Nunca el contenido de `privkey.pem`, tokens DNS ni contraseñas.
- `cf-mail` se registra con `scope="global"`, `deployment="default"` y sin
  tenant. Es el certificado que atiende conexiones sin SNI mientras no se
  migre.
- Cada hostname de correo tiene su propio certificado. Se aceptan SANs
  múltiples en el modelo, pero la configuración normal es un certificado por
  hostname.
- `mail.<dominio>` se asocia al dominio `<dominio>` si ese dominio existe en
  el tenant. Si no existe, el certificado solo lo ve el administrador de
  infraestructura.

### Archivos y permisos

| Ruta | Dueño / modo | Uso |
|---|---|---|
| `/var/lib/spanel/cert-manager/state.json` | `root:spanel-cert` `0640` | Estado agregado, solo metadatos |
| `/var/lib/spanel/cert-manager/requests/` | `spanel:spanel-cert` `0770` | La web escribe; el agente lee |
| `/var/lib/spanel/cert-manager/jobs/` | `root:spanel-cert` `0750` | Resultados de jobs |
| `/etc/spanel/cert-agent.toml` | `root:root` `0600` | Allowlist y comandos permitidos |
| `/etc/ssl/spanel/mail/<hostname>/fullchain.pem` | `root:root` `0644` | Cadena instalada |
| `/etc/ssl/spanel/mail/<hostname>/privkey.pem` | `root:root` `0600` | Clave instalada |

La web solo puede escribir en `requests/`. No puede leer `privkey.pem` ni
escribir en `/etc/ssl`, `/etc/postfix` o `/etc/dovecot`.

### Solicitud de emisión

Archivo `requests/<uuid>.json`:

```json
{
  "id": "uuid",
  "hostname": "mail.systutor.com",
  "action": "ensure",
  "requested_by": "user-id",
  "requested_at": "2026-10-27T12:00:00Z",
  "reason": "manual"
}
```

- `action`: `ensure` (emitir si falta, renovar si corresponde) o `force`
  (forzar reemisión con confirmación explícita y advertencia de rate limit).
- El agente valida el hostname contra la allowlist del inventario. Cualquier
  otro valor se rechaza sin ejecutar Certbot.
- No se aceptan argumentos arbitrarios de Certbot, rutas, dominios extra ni
  variables de entorno.
- Una sola solicitud activa por hostname. Si ya hay una, la API devuelve 409
  con el `job_id` existente.

### API

| Método | Ruta | Permiso | Función |
|---|---|---|---|
| `GET` | `/certificates` | `domains.certificates.read` | Inventario visible para el tenant |
| `GET` | `/certificates/{hostname}` | `domains.certificates.read` | Detalle de un certificado |
| `GET` | `/certificates/{hostname}/jobs/{job_id}` | `domains.certificates.read` | Estado de una solicitud |
| `POST` | `/certificates/{hostname}/request` | `domains.certificates.manage` | Crear solicitud de emisión |
| `POST` | `/certificates/refresh` | `domains.certificates.manage` | Pedir refresco de estado al agente |

El endpoint existente `GET /{domain}/certificate` del certificado web se
mantiene sin cambios. El nuevo namespace `/certificates` evita colisiones.

Respuesta de solicitud:

```json
{
  "job_id": "uuid",
  "hostname": "mail.systutor.com",
  "state": "queued",
  "poll_url": "/api/v1/plugins/domains/certificates/mail.systutor.com/jobs/uuid"
}
```

### Flujo de emisión

```text
Admin abre modal y pulsa Regenerar
        │
        ▼
POST /certificates/{hostname}/request
        │
        ├── valida permiso y hostname
        ├── valida que no haya job activo
        ├── escribe requests/<uuid>.json (atómico, 0600)
        └── devuelve job_id
        │
        ▼
spanel-cert-agent.path detecta el archivo
        │
        ▼
spanel-cert-agent.service (oneshot, root)
        │
        ├── valida allowlist y payload
        ├── ejecuta Certbot con argv fijo
        ├── valida clave/cadena/SAN/fechas
        ├── instala en /etc/ssl/spanel/mail/<hostname>/
        ├── actualiza mapa SNI de Postfix y bloque local_name de Dovecot
        ├── recarga Postfix y Dovecot
        ├── verifica el certificado servido
        └── escribe jobs/<uuid>.json y actualiza state.json
        │
        ▼
Frontend consulta el job y refresca el estado
```

### Certbot

- El agente usa el Certbot del host. No se introduce otra instancia en Docker.
- Para `ensure`: `certbot renew --cert-name <hostname>` si el certificado
  existe; si no existe, `certbot certonly --cert-name <hostname> -d <hostname>`.
- Para `force`: `certbot certonly --force-renewal --cert-name <hostname>
  -d <hostname>`, solo con confirmación explícita del administrador.
- El método de desafío se toma de la configuración del agente:
  - `webroot` para los certificados existentes que ya lo usan;
  - `dns-01` para nuevos certificados cuando el operador lo configure;
  - `auto` para respetar la configuración de renovación de Certbot.
- Spanel no conoce Cloudflare ni ningún proveedor DNS. El plugin DNS, las
  credenciales y la propagación viven en Certbot y en el host.
- El agente no acepta `--pre-hook`, `--post-hook`, `--deploy-hook` ni flags
  arbitrarios desde la solicitud.

### Instalación y SNI

- `cf-mail` no se reinstala en esta spec: se lee desde sus rutas actuales y se
  muestra como predeterminado global.
- Cada certificado nuevo se instala en una ruta estable por hostname.
- El agente genera/actualiza:
  - el mapa `tls_server_sni_maps` de Postfix;
  - el bloque `local_name` de Dovecot;
  - las recargas `postfix reload` y `dovecot reload`.
- Las rutas exactas y el mecanismo de persistencia en DMS se toman de la
  configuración del agente, no se inventan. Si no están configuradas, el
  agente falla cerrado con un mensaje accionable.
- No se recrea el contenedor. La adopción inicial de DMS con
  `SSL_TYPE=manual` sigue siendo responsabilidad de A.SPEC 0028.
- El certificado predeterminado sigue siendo `cf-mail`; ningún certificado SNI
  lo reemplaza.

### Permisos

| Permiso | Quién | Alcance |
|---|---|---|
| `domains.certificates.read` | admin de infraestructura y tenants | Propios dominios; admin ve todo |
| `domains.certificates.manage` | solo admin de infraestructura | Solicitar emisión, forzar y refrescar |

`domains.certificates.manage` **no** se agrega a
`tenant_admin_permissions`. El tenant puede consultar, no ejecutar Certbot.

### UI

Modal `CertificatesDialog`:

- Título: **Certificados TLS**.
- Subtítulo: **Spanel · Infraestructura de correo**.
- Se abre con doble clic en una fila de Hosting.
- Debe existir una acción accesible equivalente (botón o menú) para teclado y
  móvil.
- Lista:
  - `mail.systutor.com` — `Certbot · SNI` — `Válido` — vence en N días —
    `Regenerar`.
  - `cf-mail` — `Global · Predeterminado` — `Válido` — sin botón de
    regeneración en esta fase.
- Estados visuales: `Válido`, `Por vencer`, `Vencido`, `Sin certificado`,
  `Pendiente`, `Error`.
- El botón se deshabilita si:
  - el usuario no tiene `domains.certificates.manage`;
  - hay un job activo;
  - el certificado es `cf-mail` y no está soportada su regeneración.
- Confirmación antes de `force`, con advertencia de rate limit de Let's
  Encrypt.
- Si el agente está caído o el estado está viejo, mostrar “Estado no
  disponible” y no habilitar acciones destructivas.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: La aplicación web nunca recibe, lee ni registra claves privadas.
    proof: state.json solo contiene rutas y metadatos; tests de serialización sin "PRIVATE KEY".
  - id: I2
    statement: El agente solo ejecuta Certbot para hostnames de la allowlist y con argv fijo.
    proof: tests de hostname no allowlisted y de payload con flags extra; no se ejecuta subprocess.
  - id: I3
    statement: cf-mail sigue siendo el certificado predeterminado y no se modifica en esta spec.
    proof: state.json marca cf-mail como global/default; no existe ruta de instalación para cf-mail.
  - id: I4
    statement: Ningún certificado SNI reemplaza al predeterminado para conexiones sin SNI.
    proof: prueba TLS sin servername contra el host; serial == cf-mail.
  - id: I5
    statement: Un tenant no puede ver ni solicitar certificados de otro tenant.
    proof: tests de API con dos tenants; 404/403 y sin filtración en listados.
  - id: I6
    statement: Una solicitud es idempotente y no crea dos jobs activos para el mismo hostname.
    proof: dos POST concurrentes; uno devuelve 202 y el otro 409 con el mismo job.
  - id: I7
    statement: Un certificado solo se instala si la clave coincide, el SAN contiene el hostname y las fechas son válidas.
    proof: tests con clave incorrecta, SAN incorrecto y certificado vencido; no se escribe en rutas activas.
  - id: I8
    statement: La emisión y la instalación no tocan cuentas, contraseñas, buzones ni maildir.
    proof: no se ejecutan comandos de DMS fuera de las recargas autorizadas; snapshot antes/después en laboratorio.
  - id: I9
    statement: El estado expuesto a la web no contiene secretos ni credenciales DNS.
    proof: revisión de state.json y jobs; tests de redacción.
  - id: I10
    statement: El agente falla cerrado si falta configuración de SNI o de recarga.
    proof: test sin mapa o sin comando permitido; job failed y state sin cambios.
```

## VERIFICATION

### V0 — Gates

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/domains/backend/tests \
  vendor/systutor-core/plugins/hosting/backend/tests -q
cd vendor/systutor-core && ruff check plugins/domains/backend plugins/hosting/backend
```

### V0.5 — Auditoría base (Fase 0, sin regresiones)

```bash
.venv/bin/python -m pytest scripts/tests/test_mail_cert.py -q
# En el host de correo, solo lectura:
python3 scripts/mail_cert.py audit --json
```

Criterios: 26 tests pasan; `audit` devuelve `0` con el certificado actual sano,
`8` si se fuerza una clave incorrecta, y no modifica archivos ni recarga
servicios (comparar mtimes y `docker inspect -f '{{.Id}}'`).

### V1 — Inventario y asociación

- `state.json` con `cf-mail` global y dos certificados SNI se parsea sin error.
- `mail.systutor.com` se asocia a `systutor.com`; `cf-mail` no tiene tenant.
- Un hostname sin dominio conocido solo lo ve el administrador.
- Un estado con `PRIVATE KEY` se rechaza y no se sirve por API.

### V2 — API y permisos

- Admin ve todos los certificados.
- Tenant ve solo los de sus dominios.
- Tenant recibe 403 al intentar `POST /request`.
- Admin recibe 202 y `job_id`; el archivo de solicitud aparece con modo `0600`.
- Dos solicitudes simultáneas: una 202 y otra 409.

### V3 — Agente y systemd

- Un archivo válido en `requests/` dispara `spanel-cert-agent.service`.
- Un hostname fuera de allowlist no ejecuta Certbot y deja `jobs/<id>.json` en
  `failed`.
- Un payload con `--force-renewal` o flags extra se rechaza.
- El agente no escribe claves en `state.json` ni en logs.
- El timer de refresco actualiza `state.json` sin modificar certificados.

### V4 — Instalación

- Certbot de prueba (staging o CA local) emite `mail.test.example`.
- El agente valida clave, cadena, SAN y fechas.
- El par queda en `/etc/ssl/spanel/mail/mail.test.example/` con `0644`/`0600`.
- El mapa SNI y el bloque Dovecot se generan y recargan.
- El contenedor no cambia de ID (`docker inspect -f '{{.Id}}'` igual).
- `openssl s_client` con SNI devuelve el certificado nuevo; sin SNI devuelve
  `cf-mail`.

### V5 — UI

- Doble clic en una fila de Hosting abre el modal.
- El modal muestra “Certificados TLS”, “Spanel · Infraestructura de correo” y
  la fila del hostname con fuente `Certbot · SNI`.
- El botón `Regenerar` solo aparece para admin.
- Un job pendiente muestra `Pendiente` y deshabilita el botón.
- Un error del agente muestra un mensaje claro y no permite reintentar en
  bucle.

### V6 — Live, solo lectura

Antes de habilitar solicitudes en producción:

```bash
# Inventario real sin escribir nada
curl -s -H "Authorization: Bearer $TOKEN" \
  https://panel.example/api/v1/plugins/domains/certificates | jq .

# Verificación de los hostnames existentes
openssl s_client -connect 127.0.0.1:993 -servername mail.systutor.com \
  -CAfile /etc/ssl/certs/ca-certificates.crt </dev/null

openssl s_client -connect 127.0.0.1:993 </dev/null
```

Criterios: el inventario coincide con los certificados reales; el SNI devuelve
el certificado del hostname; sin SNI devuelve `cf-mail`; no se escribe nada.

## ROLLBACK

- Revertir el commit elimina el modal, la API y las unidades del agente. Los
  certificados existentes y `cf-mail` no se tocan.
- Desinstalar `spanel-cert-agent.path`, `.service` y `.timer`; conservar
  `state.json` y `jobs/` para auditoría.
- Si se instaló un certificado SNI nuevo, restaurar el mapa y el bloque
  Dovecot desde el backup del agente y recargar. No recrear el contenedor.
- `cf-mail` no se modifica en esta spec: no hay rollback de certificado
  predeterminado.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - aspecs/0035-certificate-manager-domain-ui.md
    - vendor/systutor-core/plugins/domains/backend/certificates/**
    - vendor/systutor-core/plugins/domains/backend/router.py
    - vendor/systutor-core/plugins/domains/backend/schemas.py
    - vendor/systutor-core/plugins/domains/backend/service.py
    - vendor/systutor-core/plugins/domains/backend/register.py
    - vendor/systutor-core/plugins/domains/plugin.json
    - vendor/systutor-core/plugins/domains/permissions/domains.json
    - vendor/systutor-core/plugins/domains/events/domains.json
    - vendor/systutor-core/plugins/domains/backend/tests/**
    - vendor/systutor-core/plugins/domains/frontend/**
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/frontend/components/CertificatesDialog.tsx   # nuevo
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - scripts/mail_cert.py                                          # solo extensión de la auditoría de Fase 0
    - scripts/tests/test_mail_cert.py
    - scripts/spanel_cert_agent/**
    - scripts/systemd/spanel-cert-agent.path
    - scripts/systemd/spanel-cert-agent.service
    - scripts/systemd/spanel-cert-agent.timer
    - docs/certificate-manager.md
  prohibited:
    - aspecs/0028-mail-cert-internal-tls.md
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/src/systutor/kernel/**
    - /etc/ssl/cf-mail.fullchain.crt
    - /etc/ssl/cf-mail.key
    - /etc/letsencrypt/**
    - /var/mail/**
    - /tmp/docker-mailserver/**
    - /opt/stacks/**
    - docker-compose.yml
    - .env.docker
```

## BLAST RADIUS

```yaml
blast_radius:
  direct:
    - inventario de certificados de correo
    - modal de Hosting
    - solicitudes de emisión
    - instalación SNI en DMS
  indirect:
    - Postfix y Dovecot (recargas)
    - disponibilidad de TLS en 993/465/587
    - auditoría de dominios
  must_not_affect:
    - cf-mail
    - cuentas, contraseñas, buzones y maildir
    - certificados web del proxy
    - plugin mail
    - plugin tenant
    - datos de otros tenants
```

## COMPOSITION

```yaml
composition:
  requires_aspecs:
    - 0028
    - 0027
  must_compose_with:
    - plugin domains
    - plugin hosting
    - Certbot del host y certbot.timer
    - Docker Mailserver con SSL_TYPE=manual
  systemic_invariants:
    - ningun secreto en la web, API, logs ni state.json
    - cf-mail sigue siendo el predeterminado sin SNI
    - un certificado por hostname
    - no se recrea el contenedor
```

## TRACEABILITY

- Requirement: gestionar certificados TLS de correo desde el panel, con
  `cf-mail` como predeterminado y certificados SNI por hostname.
- Base: auditoría de solo lectura de Fase 0 en `scripts/mail_cert.py`
  (`audit`), verificada en `lucas-ThinkPad-E570`; pendiente de commit.
- Owner: luc444s
- Approver:
- Commit:
- Deployment: laboratorio y producción, con ventana aprobada.
