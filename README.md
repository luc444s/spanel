# Spanel

Panel de gestión web sobre Docker remoto. Hosting, correo, proxy y plugins.

## Qué es

Spanel es un gestor de sitios web que opera sobre un servidor Docker remoto conectado via SSH (Tailscale o directo). Permite adoptar containers existentes, provisionar WordPress, gestionar dominios con Traefik, y administrar correo con docker-mailserver — todo desde una UI web.

## Arquitectura

```
┌─────────────┐    SSH (Tailscale)   ┌──────────────────────────┐
│   Spanel    │ ──────────────────►  │   Docker remoto (VPS)    │
│             │                      │                          │
│  API :8001  │                      │  ┌─ Traefik (:80/443)    │
│  Web :5175  │                      │  ├─ WordPress sites       │
│  DB  :5432  │                      │  ├─ docker-mailserver     │
│  Redis :6379│                      │  └─ containers...        │
└─────────────┘                      └──────────────────────────┘
```

## Modos de operación

| Modo | Uso | Acceso |
|------|-----|--------|
| `SPANEL_MODE=tailscale` | Desarrollo | Solo dentro de la tailnet |
| `SPANEL_MODE=production` | VPS público | Dominios públicos via internet |

## Stack

| Capa | Componente | Puerto |
|------|-----------|--------|
| Frontend | Vite + React + Tailwind v4 | 5175 |
| API | FastAPI + SQLAlchemy | 8001 |
| DB | PostgreSQL | 5432 |
| Cache | Redis | 6379 |
| Proxy | Traefik v3 (remoto) | 80, 443 |
| Mail | docker-mailserver (remoto) | 25, 465, 587, 143, 993 |

## Plugins

| Plugin | Función |
|--------|---------|
| `docker_infra` | Adapter SSH → Docker remoto (ps, inspect, start, stop, logs, exec) |
| `hosting` | Sites: adoptar containers, provisionar WordPress, lifecycle, backups, SSO |
| `proxy` | Dominios + Traefik: CRUD dominios, rutas dinámicas, sync WordPress siteurl |
| `mail` | Correo: dominios mail, buzones, estadísticas de almacenamiento |

## Deploy con Docker (producción)

```bash
git clone --recurse-submodules https://github.com/luc444s/spanel.git
cd spanel

# Crear .env
cp .env.example .env
# Editar: DB_PASSWORD, SYSTUTOR_JWT_SECRET_KEY, SPANEL_SSO_SECRET,
#         SPANEL_DOCKER_SSH_*, SPANEL_API_URL

# Desarrollo Docker (vite hot-reload)
docker compose --profile dev up -d --build

# Producción (frontend estático + nginx)
docker compose --profile production build
docker compose --profile production up -d
```

### Services Docker

| Service | Puerto | Modo |
|---------|--------|------|
| `db` | 5432 | siempre |
| `redis` | 6379 | siempre |
| `api` | 8001 | siempre |
| `web` | 5175 | `--profile dev` |
| `web-prod` | 80 | `--profile production` |

### Variables de entorno (.env)

Ver [`.env.example`](.env.example) para todas las variables.

```env
# Modo
SPANEL_MODE=production
SPANEL_API_URL=http://host.docker.internal:8001

# PostgreSQL del compose
DB_PASSWORD=<secret>

# Docker remoto
SPANEL_DOCKER_SSH_USER=lucas
SPANEL_DOCKER_SSH_HOST=100.67.5.50
SPANEL_DOCKER_SSH_PASSWORD=<password>

# API / SSO
SYSTUTOR_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/spanel
SYSTUTOR_JWT_SECRET_KEY=<secret>
SPANEL_SSO_SECRET=<secret>
```

### Runbook producción

```bash
git clone --recurse-submodules https://github.com/luc444s/spanel.git
cd spanel
cp .env.example .env

# editar secretos obligatorios:
# - DB_PASSWORD
# - SYSTUTOR_JWT_SECRET_KEY
# - SPANEL_SSO_SECRET
# - SPANEL_DOCKER_SSH_USER
# - SPANEL_DOCKER_SSH_HOST
# - SPANEL_DOCKER_SSH_PASSWORD
# - SPANEL_API_URL

docker compose config
docker compose --profile production build
docker compose --profile production up -d
docker compose ps
curl -sI http://127.0.0.1/
curl -s http://127.0.0.1/api/v1/system/health
docker compose logs api
docker compose logs web-prod

# rollback básico
docker compose down
```

## Setup local (sin Docker)

### Requisitos

- Python 3.12+
- Node.js 18+
- PostgreSQL 14+

### Instalación

```bash
# 1. Clonar con submodules
git clone --recurse-submodules https://github.com/luc444s/spanel.git
cd spanel

# 2. Dependencias
bash install.sh

# 3. Variables de entorno
cp .env.example vendor/systutor-core/.env
# Editar vendor/systutor-core/.env

# 4. Levantar API + PostgreSQL local
npm run services
```

`npm run services` reutiliza cluster local canónico `~/.postgresql` en `127.0.0.1:5432`.
Si no está corriendo, lo arranca ahí; si necesitás aislamiento, usá otro puerto explícito.

`npm run services` auto-crea schema kernel + usuario demo idempotente.

Python vive en `./.venv`. `vendor/systutor-core` sigue siendo submodule/dependencia editable.
Frontend vive en `apps/web` y usa Node/Vite fuera de la venv.

Auto-activación opt-in:

```bash
echo 'source ~/ruta/a/Spanel/scripts/venv-auto-activate.sh' >> ~/.bashrc
# o ~/.zshrc
```

El hook activa `./.venv` al entrar al repo y la desactiva al salir.
Termux no necesita Docker local para desarrollar Spanel; Docker sigue remoto via SSH/Tailscale.

Credenciales demo: `admin@example.com` / `ChangeMe123!`

### Ejecutar

```bash
npm run services              # API en :8001 (reload)
npm run frontend              # Frontend en :5175
```

## Scripts npm

| Script | Descripción |
|--------|-------------|
| `npm run frontend` | Vite dev server en :5175 |
| `npm run frontend:stop` | Parar frontend |
| `npm run services` | Reutiliza `~/.postgresql` en `127.0.0.1:5432`, crea DB/schema de Spanel si falta, y luego uvicorn --reload en :8001 |
| `npm run services:no-reload` | Usa `./.venv/bin/python3` si existe y levanta uvicorn con workers en :8001 |
| `npm run services:stop` | Parar API |
| `npm run services-host:0.0.0.0` | Usa `./.venv/bin/python3` si existe y bindea uvicorn a 0.0.0.0 |

## Estructura

```text
Dockerfile                  Build API
docker-compose.yml          Deploy completo
nginx.conf                  Frontend production
.env.example                Variables documentadas
apps/web/                   Frontend: React + Vite + Tailwind
plugins/
  docker_infra/             Adapter SSH → Docker remoto
  hosting/                  Sites WordPress, adopt, lifecycle, backups, SSO
  proxy/                    Dominios, Traefik, rutas dinámicas
  mail/                     Correo: dominios, buzones, stats
vendor/
  systutor-core/            Kernel Python (submodule)
  systutor-shell/           UI components + vistas admin (submodule)
spec/                       A.SPECs — contrato por cambio
```

## API Endpoints

### Auth
- `POST /api/v1/auth/login` — login, retorna JWT
- `GET /api/v1/auth/me` — usuario actual

### Hosting
- `GET /api/v1/plugins/hosting/sites` — listar sites
- `POST /api/v1/plugins/hosting/sites/provision/wordpress` — provisionar WP (con dominio opcional)
- `POST /api/v1/plugins/hosting/sites/{id}/sso` — SSO magic link wp-admin

### Proxy (Dominios)
- `GET /api/v1/plugins/proxy/domains` — listar dominios
- `POST /api/v1/plugins/proxy/domains` — crear dominio (sync WordPress siteurl)
- `PATCH /api/v1/plugins/proxy/domains/{id}` — editar FQDN
- `DELETE /api/v1/plugins/proxy/domains/{id}` — eliminar dominio

### Mail
- `GET /api/v1/plugins/mail/server/status` — estado mail server
- `POST /api/v1/plugins/mail/server/ensure` — provisionar mail server
- `GET /api/v1/plugins/mail/domains` — listar dominios mail
- `POST /api/v1/plugins/mail/domains` — agregar dominio mail
- `DELETE /api/v1/plugins/mail/domains/{id}` — eliminar dominio mail
- `GET /api/v1/plugins/mail/mailboxes` — listar buzones (con email count + storage)
- `POST /api/v1/plugins/mail/mailboxes` — crear buzón

### Docker Infra
- `GET /api/v1/plugins/docker_infra/containers` — listar containers
- `GET /api/v1/plugins/docker_infra/containers/{name}/inspect` — inspeccionar

## Plugins

### Crear un plugin

```text
plugins/mi_plugin/
  plugin.json           # Manifest (id, name, version, requires, permissions)
  README.md
  backend/
    plugin.py           # def register(context: PluginContext)
  frontend/
    register.tsx        # export function registerPlugin()
  migrations/
    0001_initial.py     # def upgrade(db) / def downgrade(db)
```

### Manifest (plugin.json)

```json
{
  "id": "mi_plugin",
  "name": "Mi Plugin",
  "version": "0.1.0",
  "api_version": "1",
  "requires": ["docker_infra"],
  "backend_entrypoint": "backend.plugin:register",
  "frontend_entrypoint": "frontend/register.tsx",
  "permissions": ["mi_plugin.resource.action"],
  "events": ["mi_plugin.resource.verb"],
  "description": "Descripción del plugin"
}
```

## Docker remoto

Spanel se conecta al Docker remoto via SSH. Las credenciales se configuran en `.env`:

```env
SPANEL_DOCKER_SSH_USER=lucas
SPANEL_DOCKER_SSH_HOST=100.67.5.50
SPANEL_DOCKER_SSH_PASSWORD=<password>
```

## Licencia

[GNU AGPL-3.0](LICENSE)
