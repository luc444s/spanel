# Spanel

Host app del ecosistema SYSTUTOR. Compone el kernel (`systutor-core`) y el
frontend core (`systutor-shell`) en una consola administrativa.

## Stack

| Capa | Componente | Puerto |
|------|-----------|--------|
| Frontend | Vite + React (apps/web) | 5175 |
| API | FastAPI kernel (vendor/systutor-core) | 8001 |
| DB | PostgreSQL `spanel` | 5432 |
| Cache | Redis | 6379 |

> Puerto 8000 es de otro trabajo — NO matar procesos ahi.

## Scripts npm (raiz)

```bash
npm run frontend            # vite dev en 5175
npm run frontend:stop
npm run services            # uvicorn --reload en 8001 (carga .env del core)
npm run services:no-reload  # uvicorn --workers 2
npm run services:stop
```

## Setup

```bash
# 1. submodules + deps
bash install.sh

# 2. DB
psql -U postgres -c "CREATE DATABASE spanel"
cd vendor/systutor-core && python3 -c "
from systutor.core.database import Base, build_engine
from systutor.core.config import Settings
Base.metadata.create_all(build_engine(Settings(database_url='postgresql+psycopg://postgres:postgres@localhost:5432/spanel')))
"

# 3. seed demo (requiere API booteada: npm run services)
cd vendor/systutor-core && python3 -c "
from app.main import app
from systutor.api.seed import seed_demo_data
from systutor.core.database import build_session_factory
settings = app.state.settings
with build_session_factory(settings)() as db:
    print(seed_demo_data(db, settings, app.state.plugin_runtime.list_results()))
"
```

Credenciales seed: `admin@example.com` / `ChangeMe123!` (cambiar en produccion).

## Estructura

```text
apps/web/                 host frontend: rutas, layout, branding, gate auth
vendor/systutor-core/     kernel Python (submodule)
vendor/systutor-shell/    frontend core: componentes UI + vistas admin (submodule)
spec/                     A.SPECs (ADD) — contrato por cambio, con traceability
ADD/                      metodologia: manifesto, spec, template
```

## Desarrollo (ADD)

Cada cambio = una A.SPEC en `spec/SP-XXXX-*.aspec.md`. Commit por spec, en
ambos repos (shell para vistas genericas, root para composicion). Vistas
admin viven en `vendor/systutor-shell/src/admin/`; Spanel solo compone.
