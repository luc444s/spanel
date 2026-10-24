# Spanel

Sistema de administración multi-tenant de correo electrónico y sitios WordPress sobre Docker. Permite gestionar cuentas de correo y sitios WordPress para múltiples dominios/tenants a través de una interfaz web moderna.

## Características Principales

- **Multi-tenant**: Aislamiento completo entre dominios/organizaciones
- **Docker Mailserver**: Integración directa con DMS vía SSH
- **Gestión de cuentas**: Crear, listar y cambiar contraseñas de correos
- **RBAC**: Sistema de permisos por rol (superadmin, admin, usuario)
- **UI Moderna**: Frontend con React, Tailwind CSS y shadcn/ui
- **Plugin System**: Arquitectura modular basada en plugins
- **Hosting WordPress**: sitios, usuarios, configuración, ciclo de vida y adopción de sitios existentes
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
| `SPANEL_PHPMYADMIN_URL` | URL legada incluida en la respuesta de credenciales (la UI ya no la usa) | - |
| `SPANEL_PHPMYADMIN_INTERNAL_URL` | URL interna de phpMyAdmin, accesible desde el contenedor de la app en una red Docker compartida | `http://spanel-phpmyadmin:8080` |
| `SPANEL_PHPMYADMIN_SESSION_TTL` | Duración de la sesión de la pestaña (segundos, máximo 3600) | `1500` |
| `SPANEL_PHPMYADMIN_TIMEOUT` | Timeout HTTP al servicio interno (segundos) | `15` |

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

Gestiona sitios WordPress que viven en contenedores Docker en un host remoto
(SSH) o local. La propiedad es una fila de `hosting_sites`, no una etiqueta de
Docker: otro tenant ve 404, igual que si el sitio no existiera.

**Endpoints** (todos con `TenantContext` y guard de propiedad):

- `GET /api/v1/plugins/hosting/sites` — `hosting.sites.read`
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`
- `POST /api/v1/plugins/hosting/sites/{id}/{start|stop|restart}` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/logs` — `hosting.sites.read`
- `GET|PATCH /api/v1/plugins/hosting/sites/{id}/config` — `hosting.sites.read` / `manage`
- `POST /api/v1/plugins/hosting/sites/{id}/secrets/salts` — `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{id}/users` — `hosting.users.read`
- `POST /api/v1/plugins/hosting/sites/{id}/users/{wp_user_id}/password` — `hosting.users.password`
- `POST /api/v1/plugins/hosting/sites/{id}/database` — `hosting.sites.manage`
- `DELETE /api/v1/plugins/hosting/sites/{id}` — `hosting.sites.delete` (plataforma)

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

phpMyAdmin se levanta aparte, apuntando a un MariaDB que ya existe:

```bash
./scripts/phpmyadmin_up.sh --host spanel-lab-mariadb --network mariadb_internal
./scripts/phpmyadmin_up.sh --host spanel-lab-mariadb --dry-run
./scripts/phpmyadmin_up.sh --down --yes
```

El script solo agrega (o quita) el contenedor de phpMyAdmin: no crea bases, no
toca volúmenes ni otros contenedores, y siempre publica en `127.0.0.1`. Cada
usuario de base ve solo su propia base. Para entrar desde otra máquina usá un
túnel SSH o `tailscale serve`; nunca publiques el puerto en `0.0.0.0` ni lo
expongas con Traefik. `SPANEL_PHPMYADMIN_URL` se conserva solo por compatibilidad;
el proxy usa `SPANEL_PHPMYADMIN_INTERNAL_URL` y la pestaña no contacta el puerto
de phpMyAdmin directamente. Ambos contenedores deben compartir una red Docker
existente. Si phpMyAdmin está en `mariadb_internal`, por ejemplo, la app puede
sumarse con un `docker-compose.override.yml` de despliegue:

```yaml
services:
  app:
    networks: [default, proxy, mariadb_internal]
networks:
  mariadb_internal:
    external: true
```

Configurá `SPANEL_PHPMYADMIN_INTERNAL_URL=http://<nombre-del-contenedor>:8080`
en el entorno de la app. En despliegues con Docker local usá
`SPANEL_DOCKER_TRANSPORT=local`; no hace falta publicar otro puerto.

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
│       ├── src/systutor/       # SDK / kernel
│       ├── app/                # FastAPI app
│       └── plugins/            # mail, tenant, hosting (backend + frontend)
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
# Backend (desde vendor/systutor-core)
python -m pytest plugins/mail/ -v
python -m pytest plugins/tenant/ -v

# Frontend (desde apps/web)
npm run test
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
