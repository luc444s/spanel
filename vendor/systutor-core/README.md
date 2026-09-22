# systutor-core

Kernel for SYSTUTOR: an infrastructure framework for multi-tenant
applications whose business logic lives in pluggable modules. Distributed
under the MIT license.

## Scope

The kernel provides:

- **Authentication**: JWT issuance and validation, user management,
  password hashing.
- **RBAC**: declarative permission catalog with per-tenant roles.
- **Multi-tenancy**: active isolation by `tenant_id` and `branch_id`.
- **Audit**: persistent `audit_log` with actor, entity, result, and
  correlation identifiers.
- **Events**: in-process event bus with persistent `event_log` and
  `event_outbox`, a Redis-free testable dispatcher, and a Dramatiq worker.
- **Plugin runtime**: manifest-based discovery, strict validation,
  dependency resolution, lifecycle states (discovered, validated,
  installed, enabled, disabled, failed, uninstalled), per-plugin
  migrations, and lifecycle hooks.
- **Documents**: per-entity document versioning with PDF rendering and
  signed download URLs.
- **Signatures**: signature sessions with evidence records.
- **SDK** (`systutor.sdk`): plugin context, permission/event/route
  registration.
- **Contracts** (`systutor.contracts`): shared event, audit, and plugin
  contracts.

The kernel deliberately contains no business domain logic. Business
modules are external plugins that declare identity, version, permissions,
events, and their own migrations.

## Repository layout

```text
src/systutor/
├── kernel/       auth, audit, documents, events, permissions, plugins,
│                 signatures, tasks, tenants
├── core/         config, database, errors, lifecycle, logging,
│                 pagination, request_context, cache
├── api/          dependencies, demo seed, v1 management APIs
├── contracts/    shared contracts
└── sdk/          plugin SDK
app/              reference FastAPI application
tests/            kernel test suite (SQLite, no business plugins)
```

## Installation

Python 3.12 or newer.

```bash
python3 -m pip install -e ".[dev]"
```

### Migrations

```bash
alembic -c alembic.ini upgrade head
alembic -c alembic.ini history
```

## Configuration

Settings are resolved from environment variables prefixed with
`SYSTUTOR_`. A `.env` file at the repository root is loaded automatically;
see `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `SYSTUTOR_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/systutor` | SQLAlchemy database URL |
| `SYSTUTOR_REDIS_URL` | `redis://localhost:6379/0` | Redis for cache and worker |
| `SYSTUTOR_JWT_SECRET_KEY` | `change-me` | JWT signing key (required in production) |
| `SYSTUTOR_PLUGINS_DIR` | `<repo>/plugins` | Plugin discovery directory |
| `SYSTUTOR_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated CORS origins |
| `SYSTUTOR_API_PREFIX` | `/api/v1` | API prefix |

## Host applications

A host application adds its own settings by subclassing `Settings` and
registering a factory before the first call to `get_settings()`:

```python
from fastapi import FastAPI

from systutor.core.config import Settings, get_settings, register_settings_factory
from systutor.core.lifecycle import bootstrap_app_state, lifespan


class AppSettings(Settings):
    business_flag: bool = True


register_settings_factory(AppSettings)

app = FastAPI(lifespan=lifespan)
settings = get_settings()
bootstrap_app_state(app, settings)
```

All kernel internals obtain configuration through `get_settings()`, so
host-specific options propagate without the kernel depending on any
business domain. Host applications that need to reuse the base
environment resolution can build their subclass with
`env_settings_kwargs()`.

Plugins are discovered from `settings.plugins_dir`. Enabled plugin routes
are mounted under `<api_prefix>/plugins/<plugin_id>`.

## Plugin contract

A plugin is a directory containing a `plugin.json` manifest:

```json
{
  "id": "example",
  "name": "Example",
  "version": "0.1.0",
  "api_version": "1",
  "requires": [],
  "backend_entrypoint": "backend.plugin:register",
  "frontend_entrypoint": "frontend/register.ts",
  "permissions": ["example.record.read"],
  "events": ["example.record.created"]
}
```

The backend entrypoint exposes a `register` function:

```python
from systutor.sdk import PluginContext


def register(context: PluginContext) -> None:
    context.register_permissions(["example.record.read"])
    context.register_events(["example.record.created"])
```

Permissions must be namespaced by plugin id. Migrations live in
`migrations/` and are applied in order when the plugin is enabled.

## Reference application

```bash
uvicorn app.main:app --reload
```

Endpoints:

- `GET /api/v1/system/health`
- `GET /api/v1/system/ready`
- `POST /api/v1/auth/login`
- `GET /api/v1/core/users` (requires `core.users.read`)

### Demo seed

```bash
python3 -c "
from app.main import app
from systutor.api.seed import seed_demo_data
from systutor.core.database import build_session_factory

settings = app.state.settings
with build_session_factory(settings)() as db:
    print(seed_demo_data(db, settings, app.state.plugin_runtime.list_results()))
"
```

Default credentials: `admin@example.com` / `ChangeMe123!` (change in
production).

## Development

```bash
python3 -m pytest tests -q
ruff check .
python3 -m pyright
```

Tests run on SQLite with `TestClient`. Test plugins are generated in
temporary directories (`tests/conftest.py`).

## API stability

The `systutor.*` namespace is the public API. Signature changes are
breaking for host applications and must be versioned as dependencies.

## License

MIT. See `LICENSE`.
