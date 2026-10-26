# PROPUESTA 0030 — Crear sitio: tipo, nombre y dominio del tenant

> **Estado:** propuesta. No implementar hasta que se apruebe.
> **Fecha:** 2026-10-25.
> **Relacionado:** A.SPEC 0013/0014 (Hosting), A.SPEC 0027 (Dominios),
> `scripts/spanel-static.py` (prototipo CLI ya validado).
>
> Decisión tomada: **opción A**. El dominio del sitio debe pertenecer al
> tenant. No se crea un inventario de dominios.

## 1. Objetivo

Que un tenant pueda crear dos clases de sitio desde el mismo lugar:

- **WordPress**, con el provisioning actual.
- **Estático**, con nginx, un volumen propio y contenido HTML/CSS/JS.

El dominio no se elige de un inventario de dominios disponibles: se escribe y
se valida contra el **sufijo de sitios del tenant**. El formulario tiene
exactamente tres campos: **tipo**, **nombre** y **dominio**.

## 2. Contrato de UI (cerrado)

| Elemento | Valor |
|---|---|
| Botón | **Crear sitio** |
| Título del diálogo | **Crear sitio** |
| Campo 1 | **Tipo**: `WordPress` / `Estático` |
| Campo 2 | **Nombre** |
| Campo 3 | **Dominio** (texto con validación en vivo) |
| Acción | **Crear** |
| Extras | Ninguno |

Después de crear:

- **WordPress:** se muestra una sola vez la contraseña generada del usuario
  `admin`.
- **Estático:** se muestra la URL y el acceso al gestor de archivos.

La contraseña no es un campo del formulario; es un resultado.

## 3. Estado actual y decisión

El plugin `domains` no tiene un inventario de dominios disponibles:

- `GET /api/v1/plugins/domains` devuelve dominios primarios de Hosting y
  dominios extra ya publicados por el proxy.
- `POST /api/v1/plugins/domains` exige `site_id`; agrega un extra a un sitio
  existente.
- Un `<select>` de “disponibles” solo podría mostrar dominios ya ocupados.

Los tenants actuales son:

```text
demo      → spanel.site
systutot  → systutor.com
```

`paginadelucas.spanel` no es subdominio de ninguno de los dos. Además,
`tenant.domain` lo usa el plugin `mail` para las cuentas de correo, así que no
conviene sobrecargarlo con el sufijo de sitios.

### Decisión

Opción A, pero con un **sufijo de sitios por tenant separado del dominio de
correo**:

- `tenant.domain` sigue siendo el dominio de correo.
- `tenant.site_suffix` define qué dominios puede usar Hosting.
- El sitio `paginadelucas.spanel` se permite si el tenant demo tiene
  `site_suffix = "spanel"`.
- Para `systutot`, sería `site_suffix = "systutor.com"` y se permitirían
  `sitio.systutor.com`.

No hay tabla de dominios disponibles ni backfill de dominios. Solo una
configuración de tenant.

## 4. Sufijo de sitios del tenant

### 4.1 Dónde vive

El modelo `Tenant` es del kernel y no debe recibir campos de Hosting. El plugin
`tenant` crea una tabla propia:

```text
tenant_site_settings
  tenant_id     uuid, PK, FK tenants.id
  site_suffix   varchar(253), nullable
  created_at    timestamptz
  updated_at    timestamptz
```

Migración nueva del plugin `tenant`, sin tocar la tabla `tenants`.

Valor por defecto para tenants existentes:

- si `tenant.domain` existe, derivar el dominio registrable
  (`spanel.site` → `spanel.site`, `systutor.com` → `systutor.com`);
- el operador puede corregirlo después (`spanel` para el laboratorio).

### 4.2 Regla de validación

Un dominio es válido para el tenant si:

```text
domain == site_suffix
o
domain.endswith("." + site_suffix)
```

Ejemplos:

| tenant.site_suffix | Válido | Inválido |
|---|---|---|
| `spanel` | `paginadelucas.spanel` | `paginadelucas.spanel.site` |
| `spanel.site` | `paginadelucas.spanel.site` | `paginadelucas.spanel` |
| `systutor.com` | `sitio.systutor.com` | `sitio.otro.com` |

Además:

- el dominio se normaliza con `normalize_domain` del proxy;
- no puede estar en `hosting_sites.domain`;
- no puede estar reclamado por un YAML `spanel-*.yml` del proxy;
- si `site_suffix` está vacío, el tenant no puede crear sitios (error claro).

### 4.3 Endpoint de comprobación

```text
GET /api/v1/plugins/domains/check?domain=paginadelucas.spanel
→ { available: true, reason: null, suffix: "spanel" }
→ { available: false, reason: "ya asignado a spanel-otro-wp" }
→ { available: false, reason: "no pertenece al sufijo del tenant" }
```

El check es de solo lectura y no reserva nada. El `POST /hosting/sites` vuelve a
validar en la misma transacción para evitar carreras.

### 4.4 Configuración del tenant

La pantalla de tenants agrega **Sufijo de sitios**:

```text
Dominio de correo   [ spanel.site       ]
Sufijo de sitios    [ spanel            ]
```

- Permiso: el mismo que ya administra tenants.
- `site_suffix` vacío deshabilita el alta de sitios para ese tenant.
- No se pueden solapar sufijos entre tenants: dos tenants no pueden tener
  `spanel` y `spanel`; el backend lo rechaza con 409.

### 4.5 Dominios existentes y visibilidad

La regla del sufijo no alcanza solo al alta: también define qué dominios puede
**ver** un tenant. Hoy `GET /domains` filtra por dueño del sitio, no por
namespace, y por eso puede mostrar dominios que no pertenecen al tenant.

Reglas:

1. Un dominio es visible para el tenant si su FQDN es `site_suffix` o termina
   en `.<site_suffix>`.
2. Un dominio de un sitio del tenant pero fuera de su sufijo se marca
   `outside_tenant`:
   - no se muestra en la lista normal del tenant;
   - no se ofrece en “Crear sitio”;
   - solo lo ve el administrador de plataforma, con una etiqueta clara.
3. Los dominios extra de los YAML siguen la misma regla. No alcanza con que el
   contenedor pertenezca al tenant: el FQDN también debe pertenecer al sufijo.
4. El chequeo es de solo lectura; no borra ni reasigna rutas existentes.

Caso real:

```text
tenant demo:  domain = spanel.site
              site_suffix = spanel.site (derivado)
dominio:      lucas.spanel  (extra de spanel-lucas-wp)
resultado:    outside_tenant → no visible para demo
```

Si el operador configura `site_suffix = "spanel"` para demo, entonces
`lucas.spanel` y `paginadelucas.spanel` pasan a ser dominios propios y se
muestran normalmente. Ese es el valor que se usará en el laboratorio.

### 4.6 Diagnóstico de dominios existentes

Agregar un comando de solo lectura, por ejemplo en `doctor` o en el propio
plugin:

```text
dominios fuera del sufijo del tenant:
  lucas.spanel  → sitio spanel-lucas-wp, tenant demo (sufijo spanel.site)
```

No migra ni borra nada por sí solo. Sirve para decidir entre:

- ampliar el `site_suffix` del tenant,
- mover el dominio a otro tenant,
- o dejarlo como `outside_tenant`.

## 5. API de creación

`POST /api/v1/plugins/hosting/sites`

```json
{
  "kind": "static",
  "name": "paginadelucas",
  "domain": "paginadelucas.spanel"
}
```

- `kind`: `"wordpress"` | `"static"`.
- `name`: nombre corto; el slug se deriva como hoy (3–40 caracteres).
- `domain`: obligatorio, texto; se valida contra el sufijo del tenant y la
  disponibilidad.
- `admin_email` **desaparece del request**.
  - Para WordPress se usa el email del usuario autenticado
    (`current_user.email`).
  - Fallback: `admin@<dominio>`.
- La respuesta mantiene `admin_password`, pero es `null` para estático.

`HostingSiteSummary` agrega `kind` para que la UI sepa qué acciones mostrar.

### Cambios en `hosting_sites`

- Nueva columna `kind` (`wordpress` | `static`), `NOT NULL DEFAULT 'wordpress'`.
- Migración nueva del plugin Hosting, con backfill de las filas existentes.
- No se agrega `domain_id`.

## 6. Flujo de creación

1. Validar tenant, `kind`, slug y dominio.
2. Comprobar que el dominio pertenece al `site_suffix` del tenant.
3. Comprobar que el dominio no está en `hosting_sites` ni en el proxy.
4. Provisionar según el tipo:
   - **WordPress:** provisioning actual, con el email derivado.
   - **Estático:** reutilizar la lógica ya validada del CLI
     (`scripts/spanel_static/`): volumen, `nginx:alpine`, contenido inicial,
     labels y raíz web `/usr/share/nginx/html`.
5. Publicar el dominio primario:
   - en el laboratorio, por el file provider, con la misma operación que usa
     `proxy_domain.py` (`spanel-<slug>-web.yml`, resolver `internal`, DNS del
     lab);
   - en producción, por el mecanismo configurado (labels Docker o file
     provider), sin crear rutas duplicadas.
6. Insertar `hosting_sites` con `kind` y `domain`.
7. Auditar.

Si falla antes de terminar: se eliminan solo los recursos creados y no se deja
el dominio bloqueado. Si el contenedor quedó bien pero falló el certificado, se
conserva para diagnóstico y se informa el error.

## 7. UI

### Diálogo “Crear sitio”

```text
Tipo      [ WordPress ▼ ]   (o Estático)
Nombre    [ paginadelucas ]
Dominio   [ paginadelucas.spanel        ]  ✅ Disponible

                        [ Cancelar ] [ Crear ]
```

- El campo Dominio es un input, no un select.
- Al escribir, se consulta `GET /domains/check` con un debounce.
- Muestra el sufijo esperado: “Debe terminar en `.spanel`”.
- Estados:
  - vacío / inválido → borde normal, botón deshabilitado;
  - ocupado → “Ya lo usa `spanel-otro-wp`”;
  - fuera del sufijo → “No pertenece al sufijo del tenant”;
  - disponible → tilde verde y botón habilitado.
- El botón **Crear** se habilita con tipo, nombre y dominio válido.
- No hay campo de email ni de contraseña.
- El título dice **Crear sitio**, no “Crear sitio WordPress”.

### Listado y detalle

- Badge de tipo en cada sitio.
- Sitio estático: ocultar Usuarios, Base de datos, Configuración WP, Salts y
  phpMyAdmin.
- Mantener Logs, inicio/parada/reinicio, Gestor de archivos y Eliminar.
- Sitio WordPress: sin cambios funcionales, salvo el email derivado.

### Borrado

Al eliminar un sitio:

1. se elimina su ruta del proxy;
2. se eliminan contenedor(es) y volumen(es) propios;
3. se audita.

No hay inventario que liberar: el dominio queda libre en cuanto desaparece de
`hosting_sites` y del proxy.

## 8. Guards y compatibilidad

- Rutas WordPress-only sobre un sitio `static` → `409 hosting_site_wrong_kind`.
- `config`, `salts`, `users`, `database` y `phpmyadmin` quedan solo para
  WordPress.
- `start`, `stop`, `restart`, `logs` y filemanager sirven para ambos.
- `discovery` ya detecta por label `spanel.site`; el guard por `kind` es
  obligatorio para no confundir un nginx estático con WordPress.
- Los sitios existentes siguen siendo `wordpress` por el default de la
  migración.
- Un dominio de un sitio del tenant pero fuera de `site_suffix` se marca
  `outside_tenant` y no se lista para ese tenant. `lucas.spanel` es el caso de
  prueba: con `site_suffix = "spanel.site"` no es propio de demo; con
  `site_suffix = "spanel"` sí lo es.

## 9. Fases

| Fase | Entregable | Criterio |
|---|---|---|
| 0 | Aprobar esta propuesta y convertirla en A.SPEC | Decisiones cerradas |
| 1 | `tenant_site_settings.site_suffix` + API de tenant + `GET /domains/check` + filtro `outside_tenant` | El tenant demo puede validar `paginadelucas.spanel` y `lucas.spanel` según su sufijo |
| 2 | Hosting: `kind`, `domain`, email derivado, `create_static` | API crea ambos tipos |
| 3 | UI “Crear sitio” con tipo y dominio validado | Exactamente 3 campos |
| 4 | Ciclo de vida: listado por tipo, guards, borrado | Sin regresiones WordPress |
| 5 | Validación en laboratorio: un estático y un WordPress nuevos | HTTPS y certificado |
| 6 | README y pruebas de regresión | Gates verdes |

## 10. Criterios de aceptación

1. El botón y el diálogo dicen **Crear sitio**.
2. El formulario tiene solo tipo, nombre y dominio.
3. El dominio se valida contra el `site_suffix` del tenant y la disponibilidad.
4. Crear un estático produce nginx + volumen + contenido + HTTPS, sin base de
   datos ni contraseña.
5. Crear un WordPress funciona sin pedir email y muestra la contraseña una vez.
6. Un sitio estático no ofrece acciones de WordPress.
7. Borrar un sitio elimina su ruta y libera el dominio.
8. Los sitios y dominios existentes siguen funcionando.
9. En el laboratorio, `paginadelucas.spanel` responde 200 por HTTPS con el
   certificado de la CA local.
10. Un dominio que no pertenece al sufijo del tenant no aparece en su lista ni
    se puede elegir para crear un sitio. `lucas.spanel` solo se ve en demo si
    `site_suffix` lo incluye.

## 11. Decisiones

1. **Opción A confirmada:** el dominio pertenece al tenant.
2. **Sufijo separado del correo:** `tenant.site_suffix` en una tabla del plugin
   `tenant`, no en `Tenant.domain`. Evita que cambiar el sufijo de sitios rompa
   las cuentas de correo.
3. **Email del administrador WordPress:** email del usuario autenticado;
   fallback `admin@<dominio>`.
4. **Valor inicial de `site_suffix`:** derivado de `tenant.domain`; para el
   laboratorio se configura `spanel` a mano.
5. **`lucas.spanel`:** es un dominio extra de `spanel-lucas-wp`, un sitio de
   demo. Con el sufijo derivado `spanel.site` queda `outside_tenant`; con
   `site_suffix = "spanel"` pasa a ser propio y visible. La decisión de
   laboratorio es usar `spanel`.

## 12. Fuera de alcance

- Cloudflare, DNS-01 y certificados públicos.
- Cambiar el dominio de un sitio ya creado.
- `www` en el formulario de creación (se sigue agregando como dominio extra).
- Inventario de dominios disponibles.
- Edición del contenido estático desde la API (el gestor de archivos es la vía).
- Migración automática de sitios WordPress existentes a otro tipo.

## 13. Prototipo CLI ya validado

Antes de esta propuesta se implementó y probó el camino estático:

```text
scripts/spanel-static.py
scripts/spanel_static/
```

Resultado real en el laboratorio:

```text
contenedor: spanel-paginadelucas-web
volumen:    spanel-paginadelucas-web
ruta:       scripts/traefik-lab/dynamic/spanel-paginadelucas-web.yml
estado:     active
certificado: CN=Spanel Lab CA Intermediate CA
SAN:        paginadelucas.spanel
HTTP:       200 text/html, 200 text/css, 200 application/javascript
```

Ese código es la base de `create_static` en la Fase 2; no se reescribe, se mueve
detrás del servicio del plugin.
