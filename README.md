# Spanel

Sistema de administración multi-tenant de correo electrónico, dominios web y sitios WordPress/estáticos sobre Docker. Permite gestionar cuentas de correo, sitios web y dominios para múltiples tenants a través de una interfaz web moderna.

## Características Principales

- **Multi-tenant**: Aislamiento completo entre dominios/organizaciones
- **Docker Mailserver**: Integración directa con DMS vía SSH
- **Gestión de cuentas**: Crear, listar y cambiar contraseñas de correos
- **RBAC**: Sistema de permisos por rol (superadmin, admin, usuario)
- **UI Moderna**: Frontend con React, Tailwind CSS y shadcn/ui
- **Plugin System**: Arquitectura modular basada en plugins
- **Hosting WordPress y estático**: creación por tipo, usuarios, configuración, ciclo de vida y adopción de sitios existentes
- **Dominios y proxy**: dominios primarios y extra publicados por Traefik, con estado de certificado
- **Gestor de archivos**: explorador binario con ZIP para sitios WordPress y estáticos
- **Acceso a base de datos**: credencial propia del sitio y apertura de phpMyAdmin, sin exponerlo a Internet
- **Auditoría**: Registro de eventos para trazabilidad

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (React)                       │
│                    apps/web/ (Vite + TypeScript)            │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP/REST
┌─────────────────────▼───────────────────────────────────────┐
│                    Backend (FastAPI)                         │
│              vendor/systutor-core/ (Python)                 │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │   Auth   │  │   RBAC   │  │  Tenant  │  │  Audit   │   │
│  │  (JWT)   │  │ (Perms)  │  │(Multi-T) │  │ (Events) │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
├─────────────────────────────────────────────────────────────┤
│                     Plugin System                           │
│  ┌────────────────────┐  ┌────────────────────┐            │
│  │   Mail Plugin      │  │   Tenant Plugin    │            │
│  │ (CRUD de correos)  │  │ (Gestión tenants)  │            │
│  └─────────┬──────────┘  └────────────────────┘            │
│            │ SSH (paramiko)                                 │
└────────────┼────────────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────────────┐
│              Docker Mailserver (DMS)                         │
│              mailserver (container)                          │
└─────────────────────────────────────────────────────────────┘
```

## Requisitos Previos

- **Docker** y **Docker Compose** (v2+)
- **Node.js** 20+ (para desarrollo local del frontend)
- **Python** 3.12+ (para desarrollo local del backend)
- **PostgreSQL** 16+ (base de datos)
- **Redis** 7+ (caché y colas)

## Instalación Rápida

### Con Docker (Recomendado)

```bash
# 1. Clonar el repositorio
git clone <repository-url>
cd mailadmin

# 2. Configurar variables de entorno
cp .env.docker.example .env.docker
# Editar .env.docker con valores reales del servidor
# Nunca commitear .env.docker

# 3. Iniciar servicios
docker compose up -d --build

# 4. Acceder a la aplicación
# Frontend: http://localhost:3000
# Backend API: http://localhost:3000/api/v1
```

### Desarrollo Local

```bash
# 1. Clonar el repositorio
git clone <repository-url>
cd mailadmin

# 2. Configurar entorno Python
python -m venv .venv
source .venv/bin/activate
pip install -e vendor/systutor-core

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env con valores locales reales
# Nunca commitear .env

# 4. Instalar dependencias del frontend
cd apps/web
npm install
cd ../..

# 5. Iniciar servicios de desarrollo
npm run dev
```

## Configuración

### Variables de Entorno Principales

| Variable | Descripción | Default |
|----------|-------------|---------|
| `SYSTUTOR_DATABASE_URL` | URL de conexión PostgreSQL | - |
| `SYSTUTOR_REDIS_URL` | URL de conexión Redis | - |
| `SYSTUTOR_JWT_SECRET_KEY` | Secreto para firmar JWT | - |
| `SYSTUTOR_SEED_ADMIN_EMAIL` | Email del admin inicial | - |
| `SYSTUTOR_SEED_ADMIN_PASSWORD` | Password del admin inicial | - |
| `SYSTUTOR_CORS_ORIGINS` | Orígenes permitidos CORS | - |
| `MAIL_SERVER_HOST` | Host del servidor de correo | - |
| `MAIL_SERVER_PORT` | Puerto SSH del servidor | `22` |
| `MAIL_SERVER_USER` | Usuario SSH | `root` |
| `MAIL_SERVER_PASSWORD` | Password SSH | - |
| `MAIL_DMS_CONTAINER` | Nombre del container DMS | `mailserver` |
| `MAIL_ACCOUNTS_CACHE_ENABLED` | Cache Redis de la lista de correos | `true` |
| `MAIL_ACCOUNTS_CACHE_TTL` | TTL (segundos) de la lista de correos en cache | `60` |
| `SPANEL_DOCKER_TRANSPORT` | Cómo llega el panel al host Docker (`ssh` o `local`) | `ssh` |
| `SPANEL_DOCKER_SSH_HOST` | Host Docker para el plugin de hosting | - |
| `SPANEL_DOCKER_SSH_USER` | Usuario SSH del host Docker | - |
| `SPANEL_HOSTING_SITES_DIR` | Directorio estable por sitio en el host Docker | `/opt/spanel/sites` |
| `SPANEL_HOSTING_STATIC_IMAGE` | Imagen de los sitios estáticos | `nginx:alpine` |
| `SPANEL_PHPMYADMIN_URL` | URL legada incluida en la respuesta de credenciales (la UI ya no la usa) | - |
| `SPANEL_PHPMYADMIN_INTERNAL_URL` | URL interna de phpMyAdmin, accesible desde el contenedor de la app en una red Docker compartida | `http://spanel-phpmyadmin:8080` |
| `SPANEL_PHPMYADMIN_SESSION_TTL` | Duración de la sesión de la pestaña (segundos, máximo 3600) | `1500` |
| `SPANEL_PHPMYADMIN_TIMEOUT` | Timeout HTTP al servicio interno (segundos) | `15` |
| `SPANEL_PHPMYADMIN_ARBITRARY` | `true` envía `pma_servername` en el login (hub multi-base) | `false` |
| `SPANEL_PHPMYADMIN_DB_NETWORK` | Red interna que comparten phpMyAdmin y los MariaDB; vacío = desactivado | - |
| `SPANEL_HOSTING_FILES_ROOT` | Raíz interna autorizada para el gestor de archivos de sitios WordPress | `/var/www/html` |
| `SPANEL_FILEMANAGER_HELPER_IMAGE` | Imagen con PHP + ZipArchive usada para el gestor de archivos de sitios estáticos | `wordpress:cli` |

### Configuración del Servidor de Correo

El sistema está diseñado para funcionar con **Docker Mailserver (DMS)** ejecutándose en un servidor remoto. La comunicación se realiza vía SSH:

```bash
# Ejemplo de configuración en .env.docker o en el gestor de secretos del servidor
MAIL_SERVER_HOST=<mail-server-host>
MAIL_SERVER_PORT=22
MAIL_SERVER_USER=<ssh-user>
MAIL_SERVER_PASSWORD=<ssh-password>
MAIL_DMS_CONTAINER=<dms-container-name>
MAIL_USE_SSH=true

# La lista de correos se cachea en Redis (read-through, TTL) y se invalida al crear una cuenta
MAIL_ACCOUNTS_CACHE_ENABLED=true
MAIL_ACCOUNTS_CACHE_TTL=60
```

Con la cache activa, `GET /mail/accounts` no abre viaje al servidor de correo mientras
la entrada siga viva, y dos requests consecutivos producen una sola llamada al
servidor. `MAIL_ACCOUNTS_CACHE_ENABLED=false` desactiva la cache por completo.

## Plugins

### Plugin de Correo (`mail`)

Gestiona cuentas de correo electrónico para cada tenant.

**Endpoints:**
- `GET /api/v1/plugins/mail/mail/accounts` - Listar cuentas del tenant
- `POST /api/v1/plugins/mail/mail/accounts` - Crear nueva cuenta
- `PUT /api/v1/plugins/mail/mail/accounts/{email}/password` - Cambiar contraseña

**Permisos:**
- `mail.accounts.read` - Ver cuentas
- `mail.accounts.create` - Crear cuentas
- `mail.accounts.password.update` - Cambiar contraseñas

### Plugin de Tenant (`tenant`)

Gestión de tenants (solo superadmin).

**Endpoints:**
- `GET /api/v1/plugins/tenant/tenants` - Listar tenants
- `POST /api/v1/plugins/tenant/tenants` - Crear tenant
- `PUT /api/v1/plugins/tenant/tenants/{tenant_id}` - Actualizar tenant
- `GET /api/v1/plugins/tenant/tenants/{tenant_id}/users` - Listar usuarios del tenant
- `POST /api/v1/plugins/tenant/tenants/{tenant_id}/users` - Asignar usuario
- `DELETE /api/v1/plugins/tenant/tenants/{tenant_id}/users/{user_id}` - Remover usuario

**Permisos:**
- `tenant.tenants.read` - Ver tenants
- `tenant.tenants.manage` - Gestionar tenants

### Plugin de Hosting (`hosting`)

Gestiona sitios WordPress y sitios estáticos que viven en contenedores Docker en
un host remoto (SSH) o local. La propiedad es una fila de `hosting_sites`, no una
etiqueta de Docker: otro tenant ve 404, igual que si el sitio no existiera.

**Endpoints** (todos con `TenantContext` y guard de propiedad):

- `GET /api/v1/plugins/hosting/sites` — `hosting.sites.read`
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`; acepta
  `{"kind": "wordpress"|"static", "name": "...", "domain": "..."}`. WordPress
  devuelve la contraseña de admin una sola vez; un estático devuelve `null`.
- `POST /api/v1/plugins/hosting/sites/{id}/{start|stop|restart}` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/logs` — `hosting.sites.read`
- `GET|PATCH /api/v1/plugins/hosting/sites/{id}/config` — `hosting.sites.read` / `manage`
- `POST /api/v1/plugins/hosting/sites/{id}/secrets/salts` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/users` — `hosting.users.read`
- `POST /api/v1/plugins/hosting/sites/{id}/users/{wp_user_id}/password` — `hosting.users.password`
- `POST /api/v1/plugins/hosting/sites/{id}/database` — `hosting.sites.manage`
- `DELETE /api/v1/plugins/hosting/sites/{id}` — `hosting.sites.delete` (plataforma)

Un sitio `static` aprovisiona `spanel-<slug>-web` y un volumen propio con
`nginx:alpine`, `index.html`, `styles.css` y `app.js`. No crea base de datos,
usuarios WordPress ni contraseña. Las rutas de configuración, base de datos,
usuarios, salts y phpMyAdmin responden `409 hosting_site_wrong_kind` sobre un
estático; ciclo de vida, logs, borrado y gestor de archivos sirven para ambos.

Sitios existentes se adoptan con un CLI de operador, nunca por HTTP:

```bash
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run
```

#### Base de datos

El botón `Base de datos` del diálogo del sitio revela la credencial **propia del
sitio** (el usuario que ya usa WordPress, no root) y ofrece abrir phpMyAdmin.
La respuesta va con `Cache-Control: no-store`, el host se valida como
contenedor y la contraseña nunca aparece en la URL, el log ni la auditoría. Si
`SPANEL_PHPMYADMIN_URL` está vacío, la credencial se muestra igual. El botón
abre una pestaña nueva bajo el origen del panel: el servidor inicia sesión con
el usuario propio del sitio y emite un ticket de un solo uso.
`SPANEL_PHPMYADMIN_INTERNAL_URL` debe ser accesible desde el contenedor de la
app (red interna Docker compartida con phpMyAdmin). Si no es accesible, el
diálogo muestra un error controlado. No se publica un puerto ni un subdominio nuevo.

phpMyAdmin se levanta aparte como hub de todas las bases:

```bash
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal --dry-run
./scripts/phpmyadmin_up.sh --down --yes
```

El script crea la red interna `spanel-db` si falta, conecta el MariaDB heredado
y phpMyAdmin a ella (sin quitarlos de sus redes) y monta
`scripts/phpmyadmin_arbitrary.php`, que solo admite nombres de contenedor de
base (`mariadb`, `spanel-*-db`, `*-db`). Nunca crea bases ni toca volúmenes, y
siempre publica en `127.0.0.1`. Cada usuario de base ve solo su propia base. Para
entrar desde otra máquina usá un túnel SSH o `tailscale serve`; nunca publiques
el puerto en `0.0.0.0` ni lo expongas con Traefik. `SPANEL_PHPMYADMIN_URL` se
conserva solo por compatibilidad; el proxy usa `SPANEL_PHPMYADMIN_INTERNAL_URL`
y la pestaña no contacta el puerto de phpMyAdmin directamente. La app y
phpMyAdmin deben compartir una red Docker existente (por ejemplo
`mariadb_internal`): la app puede sumarse con un `docker-compose.override.yml`
de despliegue:

```yaml
services:
  app:
    networks: [default, proxy, mariadb_internal]
networks:
  mariadb_internal:
    external: true
```

#### Un phpMyAdmin para todas las bases

Con `SPANEL_PHPMYADMIN_ARBITRARY=true` y `SPANEL_PHPMYADMIN_DB_NETWORK=spanel-db`
el login de cada sitio envía `pma_servername=<contenedor-db>` y, si ese
contenedor aún no está en `spanel-db`, el panel lo conecta una sola vez antes de
entrar (idempotente: si ya está, no ejecuta Docker). El provisioning de un sitio
WordPress nuevo conecta su MariaDB a la misma red; si esa conexión falla, el
alta falla y limpia contenedores y volúmenes como siempre. La auditoría
`hosting.site.phpmyadmin_access` incluye `database`, `host` y, solo si hubo
conexión, `network`; nunca la contraseña. Con cualquiera de las dos variables
apagada o vacía, el login no envía `pma_servername` y no se ejecuta ningún
`docker network connect`: el comportamiento es el anterior.

Configurá `SPANEL_PHPMYADMIN_INTERNAL_URL=http://<nombre-del-contenedor>:8080`
en el entorno de la app. En despliegues con Docker local usá
`SPANEL_DOCKER_TRANSPORT=local`; no hace falta publicar otro puerto.

#### Gestor de archivos

Habilitá el plugin `filemanager` y asigná `filemanager.files.read` (explorar y
descargar) o `filemanager.files.manage` (subir, editar, crear, renombrar,
borrar, comprimir y extraer). El detalle de un sitio en marcha muestra
**Gestor de archivos**, tanto para WordPress como para estáticos. El plugin y
Hosting comprueban en cada operación el usuario, tenant, permisos y propiedad
del sitio; el navegador solo envía rutas relativas. La raíz de WordPress es
configurable con `SPANEL_HOSTING_FILES_ROOT` (por defecto `/var/www/html`); la
de un estático es siempre `/usr/share/nginx/html`. Ninguna se elige desde el
cliente. Las subidas por botón o arrastrando, las descargas, ZIP y el editor
mínimo usan un canal binario local/SSH hacia el contenedor. El editor rechaza
cambios concurrentes y no abre binarios. Los ZIP se validan contra rutas
absolutas, `..`, enlaces y límites de tamaño antes de extraer.

Los contenedores WordPress deben incluir la extensión PHP `zip` (la imagen
oficial la trae). Los sitios estáticos usan `nginx:alpine`, que no tiene PHP:
para ellos el mismo script fijo se ejecuta en un contenedor desechable
`wordpress:cli` que monta el volumen del sitio con `--volumes-from`. La imagen
se puede cambiar con `SPANEL_FILEMANAGER_HELPER_IMAGE`. Conviene predescargarla
en el host Docker para que la primera operación no espere un pull:

```bash
docker pull wordpress:cli
```

El helper corre como root dentro de un contenedor efímero y sin red, con acceso
solo a los volúmenes del sitio. No se modifican permisos de MariaDB ni otros
contenedores.

### Plugin de Dominios (`domains`)

Administra los **dominios extra** de un sitio (los que viven en el file provider
de Traefik) y muestra los primarios en solo lectura. El dominio primario lo
escribe Hosting; el extra lo escribe este plugin; el dominio de correo pertenece
a Tenant/Mail. Un Host nunca tiene dos dueños y el backend rechaza cruzar ese
límite.

**Endpoints:**

- `GET /api/v1/plugins/domains` — `domains.domains.read`
- `POST /api/v1/plugins/domains` — `domains.domains.manage`; recibe
  `{site_id, domain, www}`
- `GET /api/v1/plugins/domains/check?domain=...` — `domains.domains.read`
- `GET /api/v1/plugins/domains/{domain}/certificate` — `domains.domains.read`
- `DELETE /api/v1/plugins/domains/{domain}` — `domains.domains.manage`

El transporte hacia Traefik es `auto` (`SSH`, Docker o directorio local) y se
configura con `SPANEL_PROXY_*`. Si ningún transporte es viable, las operaciones
responden `503 domains.proxy_unavailable` en vez de escribir a ciegas. El CLI de
operador sigue disponible:

```bash
python3 scripts/proxy_domain.py doctor
python3 scripts/proxy_domain.py list
```

## Desarrollo

### Estructura del Proyecto

```
spanel/
├── apps/
│   └── web/                    # Frontend React
│       ├── src/
│       │   ├── components/     # Componentes UI
│       │   ├── pages/          # Páginas
│       │   └── shared/         # Layout, hooks, utils
│       └── package.json
├── scripts/                    # CLIs de operador (adopción, phpMyAdmin, reset)
├── vendor/
│   └── systutor-core/          # Core del framework
│       ├── src/systutor/       # SDK / kernel / contratos
│       ├── app/                # FastAPI app
│       └── plugins/            # mail, tenant, hosting, domains, filemanager
├── docker-compose.yml
├── Dockerfile
└── entrypoint.sh
```

### Scripts Disponibles

```bash
# Desarrollo (frontend + backend)
npm run dev

# Solo frontend
npm run frontend

# Solo backend
npm run services

# Base de datos
npm run db

# Adoptar un sitio WordPress existente (operador)
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run

# phpMyAdmin interno, apuntando a un MariaDB existente
./scripts/phpmyadmin_up.sh --host <mariadb> --network <red> --dry-run
```

### Testing

```bash
# Backend de plugins (desde la raíz del repo)
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest vendor/systutor-core/plugins/mail/backend/tests -q

PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
    vendor/systutor-core/plugins/hosting/backend/tests \
    vendor/systutor-core/plugins/domains/backend/tests \
    vendor/systutor-core/plugins/filemanager/backend/tests -q

# Frontend (desde apps/web)
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

### Construir para Producción

```bash
# Frontend
cd apps/web
npm run build

# Docker completo
docker compose build
```

## API

### Autenticación

```bash
# Login
curl -X POST http://localhost:3000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "<admin-email>", "password": "<admin-password>"}'

# Respuesta: {"access_token": "<jwt-token>", "token_type": "bearer"}
```

### Uso de Endpoints

```bash
# Listar correos del tenant
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts

# Crear cuenta de correo
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "ventas", "password": "<mailbox-password>"}' \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts

# Cambiar contraseña
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"password": "<new-mailbox-password>"}' \
  http://localhost:3000/api/v1/plugins/mail/mail/accounts/ventas@example.com/password

# Credencial propia del sitio para phpMyAdmin (POST explícito, no-store)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:3000/api/v1/plugins/hosting/sites/<site-id>/database
```

## Seguridad

- **Passwords**: Nunca se almacenan en PostgreSQL, solo en DMS
- **Passwords**: Nunca se loguean ni aparecen en respuestas HTTP
- **Passwords**: Se envían vía stdin de SSH, nunca como argumentos CLI
- **Base de datos**: el panel revela la credencial propia del sitio (nunca root) con `no-store`; el host de la base se valida como contenedor y la contraseña no se escribe en logs ni en auditoría
- **phpMyAdmin**: escucha solo en `127.0.0.1`, en una red interna, sin autologin; el acceso remoto va por túnel SSH o tailnet, nunca por una URL pública
- **Multi-tenant**: Cada tenant solo ve/edita sus propias cuentas y sitios
- **RBAC**: Permisos verificados en cada endpoint
- **JWT**: Tokens de acceso con expiración configurable
- **CORS**: Orígenes configurables

## Despliegue

### Con Dokploy

El proyecto está optimizado para desplegarse con [Dokploy](https://dokploy.com/):

1. Configurar variables de entorno en Dokploy
2. El `docker-compose.yml` ya está configurado para producción
3. PostgreSQL y Redis se despliegan como servicios separados

### Manual

```bash
# Construir imagen
docker compose build

# Iniciar en background
docker compose up -d

# Ver logs
docker compose logs -f app

# Detener
docker compose down
```

## Troubleshooting

### El plugin de correo no carga

1. Verificar que el servidor de correo SSH sea accesible
2. Verificar variables `MAIL_*` en `.env.docker` o en el gestor de secretos del servidor
3. Revisar logs: `docker compose logs app`

### Error de conexión a BD

1. Verificar que PostgreSQL esté corriendo: `docker compose ps postgres`
2. Verificar `SYSTUTOR_DATABASE_URL`
3. Revisar logs de PostgreSQL: `docker compose logs postgres`

### Frontend no compila

```bash
cd apps/web
rm -rf node_modules
npm install
npm run build
```

## License

Spanel se distribuye bajo la **GNU Affero General Public License v3.0** (AGPL-3.0).

Copyright (C) 2026 luc444s

Este programa es software libre: puede redistribuirlo y/o modificarlo bajo los
términos de la GNU Affero General Public License publicada por la Free Software
Foundation, ya sea la versión 3 de la Licencia o (a su elección) cualquier
versión posterior.

Ver el texto completo en [LICENSE](./LICENSE).
