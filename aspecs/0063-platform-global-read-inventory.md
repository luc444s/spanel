# A.SPEC 0063 — Inventario global de plataforma en solo lectura

> `risk: medium` — amplía listados existentes de Hosting, Dominios y Apps para
> administradores de plataforma. No habilita mutaciones cruzadas, no cambia el
> tenant activo y no requiere migraciones.

## WHY

Un usuario con `core.platform.admin` administra la plataforma completa, pero
las pantallas de Hosting, Dominios y Apps consultan únicamente el tenant activo.
Mail ya tiene una lectura transversal explícita. La plataforma necesita el
mismo inventario para diagnosticar y localizar recursos sin debilitar el
aislamiento de sus operaciones.

## SCOPE

### 1. Autorización y alcance

- La autoridad de plataforma es exclusivamente `TenantContext.is_platform`,
  derivada de `core.platform.admin`; nunca `users.is_superadmin`.
- Los listados aceptan `scope=tenant|all`, con `tenant` por defecto.
- `scope=all` exige tanto el permiso de lectura del módulo como
  `core.platform.admin`; de lo contrario responde `403`.
- El frontend solicita automáticamente `scope=all` cuando la sesión posee
  `core.platform.admin`. Un usuario normal conserva exactamente su vista actual.

### 2. Identidad del tenant

- Cada fila global expone `tenant_id` y `tenant_name`.
- Hosting y Apps incorporan esos campos opcionales a sus respuestas de lista.
- Dominios conserva los campos existentes y los añade también a las opciones de
  sitio necesarias para separar destinos propios de destinos ajenos.
- La UI muestra el nombre del tenant en las tarjetas o tabla de la vista global.

### 3. Solo lectura fuera del tenant activo

- Un recurso es propio cuando su `tenant_id` coincide con el tenant activo.
- Hosting permite abrir el resumen de un hosting ajeno, pero no ofrece logs,
  usuarios, configuración, archivos, base de datos, backups, lifecycle ni borrado.
- Dominios no permite vincular ni eliminar dominios ajenos, no ofrece sitios de
  otros tenants como destino y no consulta certificados con contexto incorrecto.
- Apps muestra tarjetas ajenas como inventario no interactivo: no carga detalle,
  operaciones, despliegues, secretos, dominios ni acciones.
- Crear recursos, conectar GitHub y administrar perfiles sigue actuando sobre el
  tenant activo.
- Para gestionar un recurso ajeno, el operador debe cambiar primero al tenant
  propietario.

### 4. Contratos HTTP

- `GET /api/v1/plugins/hosting/sites?scope=all` (ya existente).
- `GET /api/v1/plugins/domains?scope=all` (ya existente).
- `GET /api/v1/plugins/apps/applications?scope=all` (nuevo alcance del listado).
- No se crea ningún endpoint global de detalle o mutación.

## INVARIANTS

1. Una lectura global nunca cambia `request.state.current_tenant_id`.
2. Poseer permisos de gestión sin `core.platform.admin` no permite `scope=all`.
3. Poseer `core.platform.admin` no elimina el permiso de lectura del módulo.
4. Ninguna mutación acepta un `tenant_id` suministrado por el cliente.
5. Los recursos ajenos no disparan consultas tenant-scoped de detalle.

## VALIDATION

- Pruebas backend: `scope=tenant`, `scope=all` autorizado y `scope=all` denegado.
- Pruebas de contrato para metadatos de tenant.
- Pruebas frontend de la decisión propio/ajeno y generación del scope.
- `ruff check` sobre Python modificado.
- `pytest` de Hosting, Dominios y Apps afectados.
- `npm test` y `npm run build` en `apps/web`.

## OUT OF SCOPE

- Administrar varios tenants simultáneamente.
- Operaciones, detalles, secretos o logs globales.
- Un nuevo permiso `*.all`; `core.platform.admin` es la capacidad transversal.
- Migraciones, cambios en contenedores, Traefik, GitHub o datos existentes.
