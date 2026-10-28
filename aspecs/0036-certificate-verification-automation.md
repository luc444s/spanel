# A.SPEC 0036 — Verificación continua, rollback y automatización del gestor de certificados

> `risk: high` — verifica y recarga TLS en Docker Mailserver, y puede revertir
> certificados y configuración SNI. Un error deja hostnames sin certificado o
> sirviendo el certificado equivocado. Exige `approver:` humano.
>
> Cubre las fases **4, 5 y 6** del plan. Requiere A.SPEC 0035 implementada y
> usa como contrato de lectura el comando `audit` ya disponible en
> `scripts/mail_cert.py`.

## WHY

A.SPEC 0035 hace que los certificados existan, se vean desde el panel y se
puedan solicitar. Eso no alcanza para producción: un certificado puede vencer,
Certbot puede renovar pero fallar la instalación, Postfix puede recargar bien y
Dovecot fallar, o el contenedor puede seguir sirviendo un certificado viejo sin
que nadie lo note.

La base de solo lectura ya está implementada y probada:

```bash
python3 scripts/mail_cert.py audit
python3 scripts/mail_cert.py audit --json
```

`audit` valida archivos, clave/certificado, cadena, SAN, vigencia, hash, serial
servido, montaje y TLS 993/465/587 con `-verify_hostname`. Esta spec convierte
esa capacidad en un **servicio continuo** con:

- verificación programada de todos los hostnames, SNI y sin SNI;
- alertas por vencimiento, deriva o fallo;
- rollback transaccional de instalación;
- automatización con `certbot.timer` y deploy hook;
- permisos finos y auditoría multi-tenant.

Certbot sigue haciendo el trabajo pesado. Spanel no emite certificados
públicos, no implementa ACME y no reemplaza `certbot.timer`.

## WHAT

1. **Verificación continua.** Un timer del agente ejecuta la auditoría sobre
   todos los certificados del inventario y todos los hostnames declarados:
   - cadena, SAN, fechas, clave y hash;
   - certificado servido por SNI en 993/465/587;
   - certificado servido **sin SNI**, que debe seguir siendo `cf-mail`;
   - estado del montaje y de la configuración de Postfix/Dovecot.

2. **Estado de salud persistente.** El agente escribe un estado con
   `last_verified_at`, `last_ok_at`, `consecutive_failures`, serial servido por
   hostname, serial predeterminado y último error. La web solo lee metadatos.

3. **Alertas.** Vencimiento próximo, certificado vencido, SAN incorrecto,
   certificado servido distinto al almacenado, fallo de Postfix, fallo de
   Dovecot, agente caído o estado viejo. Las alertas no dependen del propio
   correo vigilado.

4. **Instalación transaccional con rollback.** Antes de activar un certificado
   nuevo:
   - guardar generación activa, mapa SNI y snippet Dovecot;
   - instalar en staging y validar;
   - activar;
   - recargar Postfix y verificar 465/587;
   - recargar Dovecot y verificar 993;
   - si cualquiera falla, restaurar configuración y generación anteriores,
     recargar ambos y verificar de nuevo.

5. **Automatización sin duplicar Certbot.** `certbot.timer` sigue renovando.
   Un deploy hook de Certbot notifica al agente para que valide e instale el
   nuevo material. Si el hook no está instalado, la auditoría detecta el cambio
   de huella y lo instala. Spanel no lanza una segunda renovación.

6. **Permisos finos y auditoría.** El tenant consulta el estado de sus propios
   dominios; el administrador de infraestructura ve todo y es el único que puede
   forzar una acción. Cada verificación, instalación, rollback y alerta publica
   un evento auditable.

## SCOPE

Entra:

- Extensión de `scripts/spanel_cert_agent/` creada en A.SPEC 0035:
  - `audit` programado;
  - `sync` para detectar certificados cambiados por Certbot;
  - `install` transaccional;
  - `rollback`;
  - `verify` por SNI y sin SNI.
- Unidades systemd:
  - `spanel-cert-audit.timer` y `.service`;
  - `spanel-cert-deploy.service` (oneshot invocado por el deploy hook).
- Deploy hook de Certbot:
  - `scripts/spanel_cert_agent/certbot-deploy-hook.sh` o equivalente
    instalado en `/etc/letsencrypt/renewal-hooks/deploy/`.
- Estado de salud y alertas:
  - `state.json` ampliado con `health`;
  - `alerts.json` o registro equivalente;
  - configuración de umbrales y canal de alerta.
- Endpoints de lectura de salud en el plugin `mail`.
- Tests unitarios, de integración y live de solo lectura.
- Documentación de operación, rollback y migración.

No entra:

- Emitir certificados con ACME dentro de Spanel.
- Reemplazar `certbot.timer` ni ejecutar `certbot renew` en paralelo.
- Cambiar `cf-mail` por otro predeterminado.
- Recrear Docker Mailserver.
- Modificar cuentas, contraseñas, buzones o maildir.
- Gestionar DNS, SPF, DKIM o DMARC.
- Wildcards como configuración predeterminada.

## CONTRACT

### Verificación

El agente audita cada hostname del inventario con dos probes:

1. **SNI:** conexión a 993/465/587 con `server_hostname=<hostname>`,
   `check_hostname=True` y CA configurada. Debe devolver el certificado del
   hostname, con SAN correcto y serial esperado.
2. **Sin SNI:** conexión sin extensión SNI. Debe devolver el certificado
   predeterminado `cf-mail`. Si devuelve un certificado SNI, es fallo.

Puertos:

| Puerto | Protocolo | Verificación |
|---|---|---|
| 993 | IMAPS | handshake TLS + identidad |
| 465 | SMTPS | handshake TLS + identidad |
| 587 | SMTP + STARTTLS | STARTTLS + identidad |

La verificación de identidad no se limita a `Verify return code: 0`; debe
comprobar SAN/hostname y serial/fingerprint contra el inventario.

### Estado de salud

```json
{
  "generated_at": "2026-10-27T12:00:00Z",
  "agent": {"state": "ok", "last_run_at": "..."},
  "default": {
    "name": "cf-mail",
    "serial": "...",
    "no_sni_serial": "...",
    "ok": true
  },
  "certificates": [
    {
      "hostname": "mail.systutor.com",
      "serial": "...",
      "served_serial": "...",
      "not_after": "...",
      "days_left": 62,
      "sni": {"993": true, "465": true, "587": true},
      "no_sni": false,
      "ok": true,
      "last_verified_at": "...",
      "last_ok_at": "...",
      "consecutive_failures": 0,
      "last_error": null
    }
  ]
}
```

Reglas:

- Solo metadatos; nunca claves, tokens ni contenido PEM.
- `ok` es `false` si falla cualquier probe, la cadena, el SAN, la clave o la
  vigencia.
- `consecutive_failures` se incrementa por hostname y se resetea al volver a
  `ok`.
- Un estado con `generated_at` mayor que `2 × intervalo` se considera viejo y
  alerta.

### Umbrales y alertas

| Condición | Severidad | Acción |
|---|---|---|
| `days_left <= 21` | aviso | alerta preventiva |
| `days_left <= 7` | crítico | alerta y revisión |
| `days_left <= 0` | crítico | certificado vencido |
| SAN/hostname incorrecto | crítico | no declarar activo |
| Clave/certificado incompatible | crítico | bloquear instalación |
| Serial servido ≠ almacenado | crítico | intentar instalación/rollback |
| Postfix falla y Dovecot responde | crítico | rollback de ambos |
| Dovecot falla y Postfix responde | crítico | rollback de ambos |
| Agente sin ejecutar > 2 intervalos | crítico | alerta de watchdog |
| `cf-mail` sin SNI no coincide | crítico | no tocar SNI, alertar |

Canales:

- Webhook o correo externo configurado por el operador.
- No usar exclusivamente el correo vigilado: si el correo está caído, la alerta
  no sale.
- Registrar la alerta en `state.json`/`alerts.json` y en el journal de systemd.

### Instalación transaccional

Orden obligatorio:

```text
1. lock por hostname
2. validar material nuevo (clave, cadena, SAN, fechas, EKU)
3. copiar a generación staging
4. backup de:
   - generación activa
   - mapa SNI de Postfix
   - snippet local_name de Dovecot
   - estado de salud anterior
5. activar generación (rename/symlink atómico)
6. regenerar mapa/snippet
7. postmap si el mapa es hash
8. postfix reload + verificar 465/587
9. dovecot reload + verificar 993
10. verificar SNI de todos los hostnames y no-SNI
11. commit: borrar journal, podar generaciones viejas
```

Si falla cualquier paso 5-10:

```text
a. restaurar generación, mapa y snippet desde backup
b. postfix reload
c. dovecot reload
d. verificar 465/587/993 y no-SNI
e. escribir job failed con la causa
```

Un fallo al podar generaciones o al escribir métricas **no** revierte el
certificado activo. El rollback solo aplica a fallos de activación o
verificación.

### Automatización

- `certbot.timer` sigue siendo el único programador de renovación.
- Deploy hook:

```text
certbot renew
  → deploy hook
    → spanel-cert-agent sync --hostname <hostname>
      → valida e instala si la huella cambió
```

- Si el hook no existe, `spanel-cert-audit.timer` detecta cambio de huella y
  ejecuta `sync`.
- El agente no ejecuta `certbot renew` ni `certbot certonly` en esta spec.
- Una instalación fallida no programa reintentos infinitos: backoff y alerta.
- Dos ejecuciones simultáneas se serializan con lock; la segunda sale sin
  cambios.

### Permisos

| Acción | Tenant | Admin de infraestructura |
|---|---|---|
| Ver estado de sus dominios | sí | sí |
| Ver `cf-mail` global | no | sí |
| Forzar verificación | no | sí |
| Instalar/revertir | no | sí |
| Ver jobs y alertas globales | no | sí |

Los endpoints nuevos de 0035 se reutilizan; 0036 agrega lectura de salud y
acciones administrativas.

### Observabilidad

- `state.json` con salud agregada.
- `status --json` o endpoint equivalente para el panel.
- Logs estructurados por hostname, sin material sensible.
- Métricas: días restantes, último éxito, fallos consecutivos, duración de
  verificación, resultado por puerto.
- El panel muestra “Última verificación” y “Estado del agente”.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: La verificación continua no escribe certificados ni recarga servicios; solo lee y prueba TLS.
    proof: ejecutar el timer con certificados válidos; mtime de certificados y StartedAt del contenedor sin cambios.
  - id: I2
    statement: El certificado predeterminado sin SNI sigue siendo cf-mail.
    proof: probe sin SNI en 993/465/587; serial == cf-mail.
  - id: I3
    statement: Un fallo de Postfix o Dovecot revierte ambos, no solo el que falló.
    proof: fallo inyectado en cada demonio; ambos vuelven a la configuración anterior y verifican.
  - id: I4
    statement: La automatización no ejecuta una segunda renovación de Certbot.
    proof: logs y tests; el agente solo llama sync/install, nunca certbot renew.
  - id: I5
    statement: Un certificado con clave incompatible o SAN incorrecto nunca se activa.
    proof: tests de staging; generación activa intacta.
  - id: I6
    statement: Las alertas no dependen del correo vigilado.
    proof: prueba con SMTP caído; el canal externo recibe la alerta.
  - id: I7
    statement: Un tenant no ve estado, jobs ni alertas de otro tenant.
    proof: tests de API con dos tenants.
  - id: I8
    statement: La instalación no toca cuentas, contraseñas, buzones ni maildir.
    proof: snapshot antes/después en laboratorio.
  - id: I9
    statement: Un fallo de commit posterior a la activación no revierte el certificado activo.
    proof: simular fallo de poda/métricas; el certificado sigue sirviendo y el job queda advertido.
  - id: I10
    statement: El estado expuesto no contiene secretos.
    proof: revisión de state.json, jobs y logs; tests de redacción.
```

## VERIFICATION

### V0 — Gates

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/mail/backend/tests \
  scripts/tests/test_mail_cert.py -q
cd vendor/systutor-core && ruff check plugins/mail/backend
```

### V1 — Auditoría continua

- `spanel-cert-audit.timer` se activa y ejecuta cada intervalo configurado.
- El estado contiene todos los hostnames del inventario.
- `last_verified_at` avanza; los certificados y el contenedor no cambian.
- Un certificado vencido produce `ok=false` y alerta crítica.
- Un SAN incorrecto produce `ok=false`.
- Un `cf-mail` correcto produce `no_sni=true`.

### V2 — SNI y sin SNI

- 993/465/587 con SNI devuelven el certificado de cada hostname.
- Sin SNI devuelven `cf-mail`.
- Un mapa SNI roto se detecta y no se declara activo.
- Un certificado SNI que reemplaza al predeterminado se detecta como fallo.

### V3 — Rollback

- Fallo inyectado en Postfix después de activar: se restauran generación, mapa y
  snippet; Dovecot también se recarga; ambos verifican.
- Fallo inyectado en Dovecot: mismo resultado.
- Fallo en validación antes de activar: no se toca la generación activa.
- Fallo después de activar pero antes de commit: rollback correcto.
- Fallo de poda o métricas: no se revierte el certificado.

### V4 — Automatización

- Certbot renueva un certificado de staging y dispara el deploy hook.
- El agente detecta la nueva huella y la instala una sola vez.
- Ejecutar el hook dos veces no duplica la instalación.
- Sin hook, la auditoría detecta el cambio y lo instala.
- El agente no ejecuta `certbot renew`.

### V5 — Persistencia y concurrencia

- `docker restart mailserver` conserva los certificados.
- Un `docker compose up` controlado conserva TLS y el predeterminado.
- Dos auditorías simultáneas no se pisan.
- Dos instalaciones simultáneas: una gana, la otra sale sin cambios.

### V6 — Permisos y UI

- Admin ve `cf-mail` y todos los hostnames.
- Tenant ve solo los suyos.
- Tenant no puede forzar verificación ni instalación.
- El modal muestra “Última verificación”, estado del agente y alertas.
- Una alerta crítica se muestra sin permitir acciones destructivas.

### V7 — Live de solo lectura

```bash
python3 scripts/mail_cert.py audit --json
openssl s_client -connect 127.0.0.1:993 -servername mail.systutor.com \
  -CAfile /etc/ssl/certs/ca-certificates.crt </dev/null
openssl s_client -connect 127.0.0.1:993 </dev/null
```

Criterios: el estado coincide con `audit`; todos los hostnames responden; sin
SNI devuelve `cf-mail`; no se escribe nada.

## ROLLBACK

- Revertir el commit elimina timers, hook y endpoints nuevos; los certificados
  existentes no se tocan.
- Desinstalar `spanel-cert-audit.timer`, `spanel-cert-deploy.service` y el
  deploy hook de Certbot.
- Conservar `state.json`, `jobs/` y backups para auditoría.
- Si una instalación quedó a medias, restaurar la última generación y el backup
  de configuración, recargar Postfix/Dovecot y verificar.
- `cf-mail` no se modifica: no hay rollback de predeterminado.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - aspecs/0036-certificate-verification-automation.md
    - scripts/spanel_cert_agent/**
    - scripts/systemd/spanel-cert-audit.timer
    - scripts/systemd/spanel-cert-audit.service
    - scripts/systemd/spanel-cert-deploy.service
    - scripts/spanel_cert_agent/certbot-deploy-hook.sh
    - vendor/systutor-core/plugins/mail/backend/certificates/**
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/schemas.py
    - vendor/systutor-core/plugins/mail/backend/tests/**
    - vendor/systutor-core/plugins/mail/frontend/**
    - vendor/systutor-core/plugins/mail/frontend/components/CertificatesDialog.tsx
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
    - docs/certificate-manager.md
  prohibited:
    - scripts/mail_cert.py
    - aspecs/0028-mail-cert-internal-tls.md
    - aspecs/0035-certificate-manager-domain-ui.md
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/provider.py
    - vendor/systutor-core/plugins/mail/backend/settings.py
    - vendor/systutor-core/plugins/mail/backend/audit.py
    - vendor/systutor-core/plugins/mail/migrations/**
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
    - verificacion TLS de todos los hostnames de correo
    - estado de salud del gestor
    - rollback de instalacion
    - alertas
  indirect:
    - Postfix y Dovecot (recargas)
    - disponibilidad de 993/465/587
    - panel de dominios
    - auditoria
  must_not_affect:
    - cf-mail
    - cuentas, contrasenas, buzones y maildir
    - certificados web del proxy
    - certbot.timer y sus renovaciones
    - datos de otros tenants
```

## COMPOSITION

```yaml
composition:
  requires_aspecs:
    - 0028
    - 0035
  must_compose_with:
    - plugin domains
    - plugin hosting
    - Certbot del host y certbot.timer
    - Docker Mailserver con SSL_TYPE=manual
  systemic_invariants:
    - ningun secreto en web, API, logs ni state.json
    - cf-mail sigue siendo el predeterminado sin SNI
    - un certificado por hostname
    - no se recrea el contenedor
    - no se duplica la renovacion de Certbot
```

## TRACEABILITY

- Requirement: verificar continuamente que los certificados de correo no
  vencen, se sirven correctamente por SNI y no rompen el predeterminado.
- Owner: luc444s
- Approver:
- Commit: `779c65c`
- Deployment: laboratorio y producción, con ventana aprobada. Pendiente: V7
  (probes `openssl s_client` reales) y prueba con Certbot staging en el host.

## Definition of Done

- [x] Objective satisfied (audit continuo, sync, install transaccional, rollback y alertas)
- [x] Scope respected
- [x] Contract satisfied (probes SNI/sin SNI, health, umbrales, deploy hook, permisos)
- [x] Independent falsable truth exists now (47 tests del agente + 17 de salud/acciones)
- [x] Invariants preserved (I1-I10 cubiertos por tests)
- [ ] Verification passed: V0-V6 unitarios en verde; falta V7 live
- [x] Rollback / compensation is honest (documentado, con pruebas de fallo inyectado)
- [ ] Composition checks passed when applicable (requiere el host real)
- [x] No unrelated changes
- [x] Structural constraints respected (ningún archivo > 600 líneas)
- [x] Traceability established
