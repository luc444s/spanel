# Dominios administrados por tenant

La propiedad de un dominio es explícita y globalmente única. Vive en
`tenant_domains(domain PK, tenant_id, created_at, updated_at)` y no se deduce
del `tenant_site_settings.site_suffix`.

## Reglas

- Un dominio pertenece a exactamente un tenant. La PK lo garantiza y la API
  responde `409 tenant_domain_conflict` si otro tenant lo reclama.
- El módulo de tenants administra el conjunto:
  - `GET /api/v1/plugins/tenant/tenants/{id}/domains`
  - `POST /api/v1/plugins/tenant/tenants/{id}/domains` con `{"domain": "..."}`
  - `DELETE /api/v1/plugins/tenant/tenants/{id}/domains/{domain}`
- Los tres endpoints requieren `core.platform.admin` además de
  `tenant.tenants.read`/`manage`.
- `tenant.domain` sigue siendo el dominio de correo primario y no cambia al
  añadir o quitar dominios administrados.
- La UI del tenant muestra el dominio primario (`tenants.domain`, el que usa
  DMS), el `site_suffix` y la lista de dominios adicionales. El formulario
  superior edita el primario y el sufijo; la sección inferior añade o quita
  secundarios de `tenant_domains`.
- El `site_suffix` sigue existiendo como configuración y valor informativo,
  pero ya no decide visibilidad, disponibilidad ni creación de sitios.

## Efectos en otros módulos

- **Dominios**: `check` está disponible para cualquier dominio libre o propio;
  `list` muestra primarios de sus sitios, extras de su YAML y también las filas
  de `tenant_domains` aún no publicadas (A.SPEC 0040): se listan como
  `published=false` con acción “Vincular”. `DELETE` de un reservado libera la
  fila si no está en uso; si el proxy no responde la lista sigue funcionando y
  avisa con `proxy_available=false`.
- **Hosting**: crear un sitio acepta cualquier dominio libre o propio. Si está
  libre lo reclama en la misma transacción; si es de otro tenant responde
  `409 hosting_site_domain_conflict`.
- **Correo**: el conjunto de dominios es `{tenant.domain}` ∪ `tenant_domains`.
  Se pueden crear cuentas y cambiar contraseñas en cualquiera de ellos; el
  dominio primario sigue siendo el valor por defecto.

## Unificación de cuentas

`scripts/unify_tenants.py` consolida varios tenants en uno. Por defecto es
dry-run y siempre escribe un plan JSON:

```bash
python3 scripts/unify_tenants.py \
  --target-slug demo \
  --domain spanel.site --domain debug.com --domain test2.com \
  --all-others --apply
```

- Mueve sitios y usuarios, reclama dominios y desactiva los orígenes.
- Nunca cambia `container_name`, `domain` ni `kind`, ni borra usuarios o
  auditoría.
- No ejecuta Docker, no reescribe YAML, no reinicia contenedores ni emite
  certificados.
- `--rollback PLAN` revierte exactamente lo registrado en el plan.
- `--purge` solo borra un origen sin dependencias; la auditoría lo bloquea.

## Sincronización

`scripts/sync_tenant_domains.py` deja `tenant_domains` coherente con la regla
«el primario no es un secundario». Por defecto es dry-run y siempre escribe un
informe JSON:

```bash
python3 scripts/sync_tenant_domains.py            # dry-run
python3 scripts/sync_tenant_domains.py --apply    # elimina filas redundantes
```

- Elimina filas de `tenant_domains` cuyo dominio es el primario del mismo
  tenant (residuo del backfill de la migración `002`).
- No inserta primarios.
- Si una fila es el primario de **otro** tenant, no escribe nada, la reporta y
  sale con código 1: se resuelve cambiando el primario o unificando cuentas.
- Dominios inválidos se reportan y se omiten.
- Es idempotente: la segunda ejecución reporta `0` filas.

## Migración

`plugins/tenant/migrations/002_tenant_domains.py` crea la tabla y hace backfill
de `tenants.domain` y de los primarios de `hosting_sites`. Es idempotente y
`downgrade()` elimina solo `tenant_domains`.

Antes de una unificación real se recomienda un backup de la base y revisar el
plan JSON generado.
