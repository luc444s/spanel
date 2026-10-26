# Futuro — Dominio nuevo + correos + certificados (Cloudflare)

> Nota de análisis, no una A.SPEC. Guardada para no perder el diagnóstico.
> Fecha: 2026-10-25. Basada en el código de `vendor/systutor-core/plugins/`
> y en `aspecs/0028-mail-cert-internal-tls.md`.

## Escenario

"Quiero agregar un dominio nuevo, agregarle correos, y que estén con
certificados. Cloudflare ya los tiene."

Aclaración del alcance real: **el dominio ya está registrado en Cloudflare y su
DNS autoritativo son los nameservers de Cloudflare** (el par tipo
`luke.ns.cloudflare.com` / `*.ns.cloudflare.com`). Es decir, no hay que comprar
ni delegar nada: la zona ya se administra desde el panel de Cloudflare, a mano
o por API.

Eso **no cambia el veredicto de código**: sigue sin haber integración con
Cloudflare. Lo que cambia es que los registros DNS necesarios *se pueden crear*
en Cloudflare (MX, SPF, DKIM, DMARC, `mail`, etc.), pero **fuera del panel de
Spanel** y sin automatización. Ver §5.

## Veredicto

**El flujo completo no está soportado hoy.** Se puede hacer en pedazos y con
trabajo manual, pero no como un flujo "agrego dominio → agrego correos → todo
con certificado, Cloudflare ya los tiene".

| Lo que se quiere | ¿Soportado? | Dónde |
|---|---|---|
| Agregar un dominio web | **Sí, con límites** | Plugin `domains` |
| Agregar correos en ese dominio | **Parcial** | Plugin `mail` + plugin `tenant` |
| Reutilizar / importar el certificado de Cloudflare | **No** | — |
| Certificado TLS de correo por dominio nuevo | **No** | Solo `scripts/mail_cert.py` fijo |
| Integración con la API de Cloudflare (DNS, Origin CA) | **No existe** | Cero referencias en el repo |

## 1. Dominio web: sí, pero no es "un dominio nuevo suelto"

El plugin `domains` (`vendor/systutor-core/plugins/domains/`) agrega dominios
**extra a un sitio de Hosting que ya existe**. El request exige `site_id`
(`backend/schemas.py`: `CreateDomainRequest`), escribe un `spanel-<slug>.yml`
en el file provider de Traefik y le pone un `certResolver`.

- Perfil `public` (default): `certResolver: letsencrypt`
- Perfil `internal`: `certResolver: internal` (la `step-ca` del laboratorio)
- Se puede pisar con `SPANEL_PROXY_CERT_RESOLVER`

O sea: el sistema **pide el certificado a Traefik**, no "usa el que ya tiene
Cloudflare".

### El detalle de Cloudflare

Como la zona ya vive en Cloudflare, hay tres escenarios y el código solo cubre
uno:

1. **Cloudflare en modo DNS only (nube gris) para el registro**: Traefik puede
   sacar Let's Encrypt por HTTP-01/TLS-ALPN si los puertos 80/443 llegan al
   origen. Funciona con el perfil `public`, si Traefik tiene el resolver
   `letsencrypt` configurado. Es el único camino que hoy no exige tocar código.
2. **Cloudflare proxied (nube naranja)**: Cloudflare termina TLS; el
   HTTP-01/TLS-ALPN-01 contra el origen va a Cloudflare, no al servidor, y
   normalmente falla para emitir en el origen. Hace falta DNS-01, que el plugin
   no tiene. Tampoco sirve para correo: Cloudflare no proxya SMTP/IMAP, así que
   el hostname de correo tiene que ir DNS-only igual.
3. **Cloudflare Origin Certificate instalado en Traefik**: no hay ningún
   soporte para importar certificados ni para el almacén de confianza de
   Origin CA. Habría que configurarlo a mano en Traefik, fuera del panel.

Resumen: `domains` sirve para **enrutar** y pedir cert a un resolver de
Traefik; no es un gestor de certificados ni habla con Cloudflare. Que el DNS
esté en Cloudflare ayuda a *poder* crear los registros, no a que el sistema los
cree.

### Lo que habilita tener el DNS en Cloudflare

| Necesidad | Hoy | Con DNS en Cloudflare |
|---|---|---|
| A/AAAA del dominio y `www` | Manual en Cloudflare | Igual: manual (o API futura) |
| Cert web con proxied | No soportado | DNS-01 lo resolvería, **falta implementarlo** |
| Cert web con DNS-only | Funciona (LE HTTP-01) | Funciona, apagando el proxy del registro |
| MX, SPF, DKIM, DMARC | No automatizado | Se pueden crear a mano en Cloudflare |
| Hostname de correo (`mail.dominio`) | No gestionado | DNS-only a mano; sin proxy |
| Cert de correo público | No soportado | Factible con DNS-01/HTTP-01, **falta código** |
| Wildcard `*.dominio` | No soportado | Posible con DNS-01, **falta código** |

En corto: Cloudflare es la infraestructura correcta para hacerlo, pero Spanel
todavía no la usa. El trabajo pendiente es de integración, no de DNS.


## 2. Correos en el dominio nuevo: parcial y por efecto secundario

El plugin `mail` **no crea dominios de correo**. Solo lista cuentas, crea
cuentas y cambia contraseñas:

- `POST /api/v1/plugins/mail/mail/accounts`
- Ejecuta `docker exec <dms> setup email add usuario@dominio`
- El dominio sale de `tenant.domain` (`backend/service.py`,
  `_get_tenant_domain`)

El plugin `tenant` crea/edita el tenant y su `domain` **solo en PostgreSQL**.
No llama a DMS ni configura nada.

Según la medición de `aspecs/0028-mail-cert-internal-tls.md`, en DMS v15.1.0
agregar una cuenta dispara el changedetector y el dominio entra en
`/etc/postfix/vhost`. Es decir: **funciona, pero por un efecto colateral de
DMS, no porque el sistema tenga un paso "alta de dominio de correo"**.

Y no hay nada de lo que hace falta para correo real:

- MX, SPF, DKIM, DMARC, PTR (explícitamente fuera de alcance en 0028)
- DNS público / API de Cloudflare
- Baja de dominio de correo
- UI para administrar dominios de correo

## 3. Certificados de correo: no, salvo el caso del laboratorio

`scripts/mail_cert.py` emite y entrega **un** certificado:

- Dominio fijo por env: `SPANEL_MAIL_DOMAIN` (default `mail.lucas.spanel`)
- SANs fijos: `SPANEL_MAIL_SANS` (default `mail.spanel.local`)
- CA: `step-ca` local del laboratorio, no una CA pública
- Emisión con provisioner JWK, justamente porque descarta ACME para correo
- Recrea el contenedor de DMS para montar el cert
- No hay wildcard, no hay multi-tenant, no hay UI, no está integrado al
  plugin `mail`

Traducción: **para un dominio nuevo con correo no hay emisión automática de
certificado**. Ni desde el panel ni desde un flujo multi-dominio. Solo el caso
`mail.lucas.spanel` del lab, a mano.

## 4. "Los plugins vivos"

En disco hay **5 plugins** (`vendor/systutor-core/plugins/*/plugin.json`):

| Plugin | Qué hace |
|---|---|
| `domains` | Dominios web extra vía Traefik + estado de cert |
| `mail` | Cuentas de correo sobre DMS |
| `tenant` | Tenants, dominio del tenant y usuarios |
| `hosting` | Sitios WordPress, provisioning, ciclo de vida |
| `filemanager` | Explorador de archivos de los contenedores WordPress |

Cómo se cargan:

- Backend: descubre todos los `plugins/*/plugin.json` al arrancar
  (`src/systutor/kernel/plugins/runtime.py`). No hay lista de "plugins
  habilitados" por env: el estado vive en la tabla de registro
  (`enabled` / `disabled` / `failed`).
- Frontend: `apps/web/src/features/plugins/runtime.tsx` registra un plugin
  **solo si** su registro de runtime está `enabled` y `is_enabled`, y además
  el usuario tiene el permiso.

Para ver el estado real de un despliegue:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/plugin-runtime/debug
```

o `GET /api/v1/plugins` para los manifests. El log de arranque también imprime
`plugins_loaded` y `plugins_enabled`.

## 5. Flujo posible hoy, con DNS en Cloudflare (todo manual)

Con la zona ya en Cloudflare, el escenario se puede completar a mano así:

1. **Web**
   - Crear/asegurar el sitio en Hosting (el dominio primario se escribe ahí).
   - En el plugin `domains`, agregar el dominio extra eligiendo el sitio.
   - En Cloudflare, apuntar el registro (A/AAAA o CNAME) al origen.
   - Si está proxied, apagar el proxy del registro (nube gris) para que
     Let's Encrypt HTTP-01 valide contra Traefik; o instalar a mano un Origin
     Certificate en Traefik (fuera del panel).
2. **Correo**
   - Poner `tenant.domain` = dominio nuevo en el plugin `tenant`.
   - Crear la cuenta desde `mail`; DMS v15 registra el dominio en
     `/etc/postfix/vhost` por el changedetector.
   - En Cloudflare, crear a mano: `MX` → `mail.dominio`, `A mail` (DNS-only),
     `SPF`, y los `TXT` de DKIM (los genera DMS) y DMARC.
3. **Certificado de correo**
   - Hoy `scripts/mail_cert.py` solo sirve para `mail.lucas.spanel` con la CA
     interna del laboratorio. Para un dominio público no hay camino soportado.
   - Workaround manual: emitir el cert con `certbot`/ACME por HTTP-01 o DNS-01
     y montarlo en DMS como `SSL_TYPE=manual`, replicando lo que hace 0028 pero
     a mano y por dominio.

Ningún paso 2–3 se hace desde el panel. El sistema no se entera de que existe
un dominio nuevo de correo; solo lo refleja si el tenant y la cuenta coinciden.

## 6. Implementación que faltaría (lo que sí resuelve Cloudflare)

1. **Cliente de Cloudflare** con token acotado a la zona (`Zone:DNS:Edit`):
   crear/borrar A, MX, TXT (SPF/DKIM/DMARC) y registros de validación.
2. **DNS-01 en Traefik** para el cert web: funciona con proxied y habilita
   wildcard. Requiere un provider tipo `cloudflare` y el token por secreto.
3. **Certificado de correo por dominio**:
   - opción A: wildcard `*.dominio` (o `mail.dominio`) emitido por DNS-01 y
     montado en DMS;
   - opción B: parametrizar `mail_cert.py` por dominio y dejar de asumir
     `mail.lucas.spanel`.
4. **Alta de dominio de correo explícita** en `tenant`/`mail`: verificar el
   dominio en DMS y registrar el resultado, en vez de depender del
   changedetector.
5. **UI**: pantalla de dominio de correo con estado de DNS (MX/SPF/DKIM/DMARC)
   y de certificado.
6. **Baja ordenada**: quitar dominio de DMS y registros de Cloudflare, sin
   tocar otras zonas.

## Prioridad sugerida

El que desbloquea todo es **certificado de correo por dominio con DNS-01 de
Cloudflare**: resuelve proxied, evita depender de puertos 80/443 en el origen
y elimina el caso especial de un solo dominio del laboratorio.
