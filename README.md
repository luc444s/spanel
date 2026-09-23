# Spanel

Sistema de administración de correos electrónicos multi-tenant basado en Docker Mailserver. Permite gestionar cuentas de correo para múltiples dominios/tenants a través de una interfaz web moderna.

## Características Principales

- **Multi-tenant**: Aislamiento completo entre dominios/organizaciones
- **Docker Mailserver**: Integración directa con DMS vía SSH
- **Gestión de cuentas**: Crear, listar y cambiar contraseñas de correos
- **RBAC**: Sistema de permisos por rol (superadmin, admin, usuario)
- **UI Moderna**: Frontend con React, Tailwind CSS y shadcn/ui
- **Plugin System**: Arquitectura modular basada en plugins
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
```

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
├── plugins/
│   ├── mail/                   # Plugin de correo
│   │   ├── backend/            # API routes, service, schemas
│   │   ├── frontend/           # Páginas React
│   │   ├── migrations/         # Migraciones DB
│   │   └── permissions/        # Definición de permisos
│   └── tenant/                 # Plugin de tenants
│       ├── backend/
│       ├── frontend/
│       └── permissions/
├── vendor/
│   └── systutor-core/          # Core del framework
│       ├── src/systutor/       # Código fuente del SDK
│       └── app/                # FastAPI app
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
```

## Seguridad

- **Passwords**: Nunca se almacenan en PostgreSQL, solo en DMS
- **Passwords**: Nunca se loguean ni aparecen en respuestas HTTP
- **Passwords**: Se envían vía stdin de SSH, nunca como argumentos CLI
- **Multi-tenant**: Cada tenant solo ve/edita sus propias cuentas
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

Propietario - Todos los derechos reservados.
