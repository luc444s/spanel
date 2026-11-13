# Cloudflare OAuth: registro y reparación por CLI

[English](README-cloudflare-register-cli.md) | **Español**

Esta guía documenta cómo registrar o reparar el cliente OAuth privado de
Cloudflare usado por Spanel. El procedimiento sirve, entre otros casos, para
añadir el permiso `Zone Write` requerido por el aprovisionamiento automático de
zonas.

> El `dry-run` fue comprobado en producción. Nunca pegues tokens en comandos,
> logs, tickets o conversaciones.

## Qué modifica

Con `--apply`, el comando:

1. consulta los IDs de los scopes OAuth requeridos;
2. actualiza el cliente OAuth privado existente mediante `PATCH`;
3. conserva el `client_secret` existente;
4. si el cliente remoto fue eliminado, crea uno nuevo y cifra su secreto;
5. guarda metadatos y una auditoría sin secretos.

Scopes configurados:

```text
Zone Read
Zone Write
DNS Read
DNS Write
offline_access
```

No crea zonas, no modifica registros DNS, no cambia nameservers y no renueva
automáticamente las autorizaciones de los tenants.

## Requisitos

- Ejecutar desde la raíz del despliegue, normalmente `/opt/spanel`.
- Contenedor `app` actualizado y operativo.
- Account ID de Cloudflare.
- URL pública HTTPS de Spanel.
- API token temporal de Cloudflare con:

```text
Account → OAuth Clients → Edit
Account resources → la cuenta que se registrará
```

El API token bootstrap se usa una sola vez y Spanel nunca lo persiste.

## 1. Capturar el token sin exponerlo

```bash
umask 077
read -rsp "Cloudflare API token: " CF_TOKEN
echo
printf '%s' "$CF_TOKEN" > /root/cloudflare-bootstrap-token
unset CF_TOKEN
chmod 600 /root/cloudflare-bootstrap-token
```

Copiarlo temporalmente al contenedor:

```bash
docker compose exec -T app sh -c \
  'umask 077; cat > /tmp/cloudflare-bootstrap-token' \
  < /root/cloudflare-bootstrap-token
```

## 2. Validar el plan sin llamar a Cloudflare

Sustituye los valores entre `<...>`:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare oauth register \
  --account-id <CLOUDFLARE_ACCOUNT_ID> \
  --bootstrap-token-file /tmp/cloudflare-bootstrap-token \
  --redirect-uri https://<SPANEL_HOST>/api/v1/plugins/domains/dns/cloudflare/oauth/callback \
  --json
```

El resultado debe contener:

```json
{
  "applied": false,
  "client_secret": "<redacted>",
  "scopes": ["Zone Read", "Zone Write", "DNS Read", "DNS Write"]
}
```

Este modo no lee el token, no llama a Cloudflare y no modifica SQL ni DNS. Por
lo tanto, un `dry-run` correcto no demuestra todavía que el token sea válido.

## 3. Aplicar la reparación

Repite el comando anterior añadiendo `--apply`:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare oauth register \
  --account-id <CLOUDFLARE_ACCOUNT_ID> \
  --bootstrap-token-file /tmp/cloudflare-bootstrap-token \
  --redirect-uri https://<SPANEL_HOST>/api/v1/plugins/domains/dns/cloudflare/oauth/callback \
  --apply \
  --json
```

Continuar únicamente si devuelve `"applied": true` y muestra `Zone Write`.

## 4. Eliminar el token temporal

Después de un apply exitoso:

```bash
docker compose exec -T app rm -f /tmp/cloudflare-bootstrap-token
rm -f /root/cloudflare-bootstrap-token
```

El token también puede revocarse en Cloudflare si no se reutilizará para otra
reparación administrativa.

## 5. Reconectar cada tenant

Reparar el cliente global no amplía tokens tenant ya emitidos. Reconecta cada
tenant por slug:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare connect \
  --tenant <TENANT_SLUG> \
  --wait-seconds 0
```

Abre la URL resultante y completa el consentimiento. La conexión anterior se
conserva si el callback nuevo falla.

Verifica el resultado:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare status \
  --tenant <TENANT_SLUG> \
  --json
```

La lista `scopes` debe incluir `Zone Write`.

## 6. Probar aprovisionamiento sin modificar DNS

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare provision plan <DOMAIN> \
  --tenant <TENANT_SLUG> \
  --ipv4 <SERVER_PUBLIC_IPV4> \
  --www \
  --json
```

Un código de salida `3` significa que existe drift pendiente; es normal durante
un plan. No uses `--apply` ni `--replace` hasta revisar el JSON.

## Problemas comunes

### `No module named 'fastapi'`

El Python del host no contiene las dependencias. Ejecuta el módulo dentro del
contenedor `app`, como muestran los comandos de esta guía.

### `the bootstrap token file is missing`

El archivo se eliminó o no fue copiado al contenedor. Repite los pasos 1 y 2.

### `Cloudflare rejected the authorization`

El API token es incorrecto, expiró, pertenece a otra cuenta o no tiene
`Account → OAuth Clients → Edit`.

### `the Cloudflare OAuth client must be repaired before authorization`

El cliente global todavía no incluye todos los scopes. Completa primero el
paso 3 y después reconecta el tenant.

### `Cloudflare authorization must be renewed with Zone Write`

El cliente global ya puede estar reparado, pero el tenant conserva un token
anterior. Ejecuta el paso 5.
