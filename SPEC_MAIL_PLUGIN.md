# SPEC: Mail Administration Plugin — SYSTUTOR Core

## Overview

Minimalist mail account management plugin for SYSTUTOR Core. Interface between SYSTUTOR Core and Docker Mailserver (DMS) running on a remote server via SSH. Allows tenants to view, create, and change passwords for email accounts within their domain.

**DMS is the sole source of truth for mailboxes.** No mailbox data stored in PostgreSQL.

---

## Critical Finding: `tenant_domain`

The `Tenant` model (`vendor/systutor-core/src/systutor/kernel/tenants/models.py`) currently has **no domain field**. Existing fields: `id` (UUID), `name` (255), `slug` (100, unique), `is_active`, `created_at`, `updated_at`.

**Required migration**: Add a nullable `domain` column (VARCHAR 255, UNIQUE) to the `tenants` table. This is the only schema change. No new tables. Super Admin configures this via existing tenant management.

---

## Architecture

```
SYSTUTOR Core
    ├── Auth (JWT, get_current_user)
    ├── RBAC (require_permission)
    ├── Tenant (tenant_id from JWT)
    └── Audit (EventBus.publish)
          │
          ▼
    Mail Plugin (routes at /api/v1/plugins/mail/)
          │
          ├── MailProvider (ABC)
          └── DockerMailServerProvider
                │
                ▼  SSH (paramiko)
          Remote Mail Server
                │
                ▼
          Docker Mailserver (docker exec setup email ...)
```

---

## Plugin Structure

```
plugins/mail/
├── plugin.json
├── README.md
├── backend/
│   ├── __init__.py
│   ├── register.py           # Plugin entrypoint → context.register_router()
│   ├── settings.py           # Extends core Settings via register_settings_factory
│   ├── provider.py           # MailProvider ABC + DockerMailServerProvider
│   ├── router.py             # FastAPI APIRouter
│   ├── schemas.py            # Pydantic models
│   ├── service.py            # Business logic layer
│   ├── audit.py              # Audit event helpers
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── test_service.py
│       ├── test_schemas.py
│       └── test_router.py
├── frontend/
│   ├── register.ts           # PluginFrontendRegistration
│   └── pages/
│       ├── MailAccountsPage.tsx
│       └── CreateAccountPage.tsx
├── migrations/
│   └── 001_add_tenant_domain.py
├── permissions/
│   └── mail.json
└── events/
    └── mail.json
```

---

## 1. Plugin Manifest

`plugins/mail/plugin.json`:

```json
{
  "id": "mail",
  "name": "Mail Administration",
  "version": "0.1.0",
  "api_version": "1",
  "requires": [],
  "backend_entrypoint": "plugins.mail.backend.register:register",
  "frontend_entrypoint": "plugins/mail/frontend/register.ts",
  "permissions": [
    "mail.accounts.read",
    "mail.accounts.create",
    "mail.accounts.password.update"
  ],
  "events": [
    "mail.account.created",
    "mail.account.password_changed"
  ],
  "description": "Minimalist mail account management via Docker Mailserver"
}
```

---

## 2. Migration

`plugins/mail/migrations/001_add_tenant_domain.py`:

```python
revision = "001_add_tenant_domain"

def upgrade(db):
    db.execute("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS domain VARCHAR(255)
    """)
    db.execute("""
        ALTER TABLE tenants
        ADD CONSTRAINT uq_tenants_domain UNIQUE (domain)
    """)

def downgrade(db):
    db.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS uq_tenants_domain")
    db.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS domain")
```

Only migration. No new tables.

---

## 3. Settings

`plugins/mail/backend/settings.py`:

Uses `register_settings_factory()` from `systutor.core.config` to extend `Settings` with mail-specific fields. Env vars loaded from `os.environ` (already handled by core's `load_env_file()`).

Fields:

```python
class MailSettings(BaseSettings):
    mail_provider: str = "docker-mailserver"
    mail_server_host: str = ""
    mail_server_port: int = 22
    mail_server_user: str = "root"
    mail_server_password: str = ""
    mail_dms_container: str = "mailserver"
```

`.env.example`:

```
MAIL_PROVIDER=docker-mailserver
MAIL_SERVER_HOST=
MAIL_SERVER_PORT=22
MAIL_SERVER_USER=root
MAIL_SERVER_PASSWORD=
MAIL_DMS_CONTAINER=mailserver
```

Real `.env` gitignored, never logged, never in HTTP responses.

---

## 4. Provider

`plugins/mail/backend/provider.py`:

```python
from abc import ABC, abstractmethod

class MailProvider(ABC):
    @abstractmethod
    def list_accounts(self) -> list[str]: ...

    @abstractmethod
    def create_account(self, email: str, password: str) -> None: ...

    @abstractmethod
    def change_password(self, email: str, password: str) -> None: ...
```

### DockerMailServerProvider

- SSH via `paramiko` to `MAIL_SERVER_HOST:MAIL_SERVER_PORT` as `MAIL_SERVER_USER` with `MAIL_SERVER_PASSWORD`.
- Remote commands:
  - `docker exec {container} setup email list` → parse → `list[str]`
  - `docker exec {container} setup email add {email}` → password via stdin
  - `docker exec {container} setup email update {email}` → password via stdin
- `shell=True` never used. No user input in command strings.
- Passwords via SSH stdin only, never CLI arguments.
- stdout/stderr logged (without secrets) but never returned to HTTP clients.

Error mapping:
- SSH unreachable → `ServiceUnavailableError` (503)
- Account exists → `ConflictError` (409)
- Account not found → `NotFoundError` (404)
- DMS error → `AppError` (500)

Dependencies: `paramiko>=3.4,<4.0` added to `pyproject.toml`.

---

## 5. Schemas

`plugins/mail/backend/schemas.py`:

```python
from pydantic import BaseModel, field_validator
import re

class CreateAccountRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9.+_-]{1,64}$', v):
            raise ValueError(
                "Username: only letters, digits, dots, plus, hyphens, underscores; max 64 chars"
            )
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

class ChangePasswordRequest(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

class MailAccountsResponse(BaseModel):
    domain: str
    accounts: list[str]

class MailMessageResponse(BaseModel):
    message: str
    email: str | None = None
```

---

## 6. Service Layer

`plugins/mail/backend/service.py`:

```python
class MailService:
    def __init__(self, provider: MailProvider, db: AsyncSession): ...

    async def list_accounts(self, tenant_id: str) -> MailAccountsResponse:
        # Load tenant → get domain → provider.list_accounts()
        # → filter domain match (case-insensitive, structural)
        # → return filtered list

    async def create_account(
        self, tenant_id: str, user_id: str,
        username: str, password: str
    ) -> MailMessageResponse:
        # Load tenant → get domain → build email
        # → provider.create_account(email, password)
        # → publish_event("mail.account.created", ...)

    async def change_password(
        self, tenant_id: str, user_id: str,
        email: str, password: str
    ) -> MailMessageResponse:
        # Validate email domain matches tenant domain (403 if not)
        # → provider.change_password(email, password)
        # → publish_event("mail.account.password_changed", ...)
```

Domain filtering (critical):

```python
local_part, domain = email.rsplit("@", 1)
if domain.lower() != tenant_domain.lower():
    continue  # skip, do not include
```

No `grep`. No partial matching. `acme.com.fake` ≠ `acme.com`.

---

## 7. Router

`plugins/mail/backend/router.py`:

```python
from systutor.sdk.context import PluginContext
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/mail", tags=["mail"])
```

Routes follow Core conventions:
- `GET /mail/accounts` → list accounts for tenant domain
- `POST /mail/accounts` → create account
- `PUT /mail/accounts/{email}/password` → change password

Dependencies reused from Core:
- `get_current_user` from `systutor.kernel.auth.dependencies`
- `require_permission(...)` from `systutor.kernel.auth.dependencies`
- `get_db_session` from `systutor.core.database`

### GET `/mail/accounts`

```
Depends: get_current_user, require_permission("mail.accounts.read")
```

1. Get `tenant_id` from JWT.
2. Load tenant, verify `domain` is set (否则 422).
3. `service.list_accounts(tenant_id)`.
4. Return `MailAccountsResponse`.

### POST `/mail/accounts`

```
Depends: get_current_user, require_permission("mail.accounts.create")
Body: CreateAccountRequest
```

1. Validate username (regex).
2. `service.create_account(tenant_id, user_id, username, password)`.
3. Return 201 `MailMessageResponse`.

Frontend cannot control domain. Backend builds `email = username + "@" + tenant_domain`.

### PUT `/mail/accounts/{email}/password`

```
Depends: get_current_user, require_permission("mail.accounts.password.update")
Body: ChangePasswordRequest
```

1. Validate email format.
2. Extract domain from email.
3. Compare with tenant domain (403 if mismatch).
4. `service.change_password(tenant_id, user_id, email, password)`.
5. Return 200 `MailMessageResponse`.

Never trust frontend to hide other tenants' accounts.

---

## 8. Audit

`plugins/mail/backend/audit.py`:

Uses `context.publish_event()` (Core EventBus):

- `mail.account.created`: `{"tenant_id", "actor_user_id", "target_email"}`
- `mail.account.password_changed`: `{"tenant_id", "actor_user_id", "target_email"}`

Never: passwords, password_hashes, MAIL_SERVER_PASSWORD.

---

## 9. Backend Entrypoint

`plugins/mail/backend/register.py`:

```python
from systutor.sdk.context import PluginContext
from .router import router
from .settings import register_mail_settings

def register(context: PluginContext):
    register_mail_settings()
    context.register_router(router)
```

---

## 10. Frontend

### Registration

`plugins/mail/frontend/register.ts`:

```typescript
import { PluginFrontendRegistration } from "@systutor/sdk/frontend";
import MailAccountsPage from "./pages/MailAccountsPage";
import CreateAccountPage from "./pages/CreateAccountPage";

export function registerPlugin(): PluginFrontendRegistration {
  return {
    pluginId: "mail",
    routes: [
      {
        path: "/mail",
        title: "Correos",
        component: MailAccountsPage,
        requiredPermissions: ["mail.accounts.read"],
      },
      {
        path: "/mail/create",
        title: "Nuevo correo",
        component: CreateAccountPage,
        requiredPermissions: ["mail.accounts.create"],
      },
    ],
    navigation: [
      {
        to: "/mail",
        label: "Correos",
        requiredPermissions: ["mail.accounts.read"],
      },
    ],
    widgets: [],
  };
}
```

### MailAccountsPage.tsx

- Uses React Query (`useQuery`) to fetch `GET /api/v1/plugins/mail/mail/accounts`.
- Displays domain header: `Dominio: {domain}`.
- Lists accounts with inline "Cambiar contraseña" form (two password inputs + submit).
- "Agregar correo" button → navigates to `/mail/create`.
- After password change: clear fields, show success toast via `sonner`, invalidate query.
- Uses shadcn/ui: `Card`, `Button`, `Input`, `Label`.
- Follows existing layout conventions from `apps/web/src/shared/`.

### CreateAccountPage.tsx

- Form: username input (fixed `@{domain}` text beside it), password, confirm password.
- Submit → `POST /api/v1/plugins/mail/mail/accounts`.
- On success: clear form, navigate to `/mail`, show success toast.
- Domain displayed as text, not editable.
- Uses same shadcn/ui components.

---

## 11. Permissions

`plugins/mail/permissions/mail.json`:

```json
{
  "permissions": [
    {"name": "mail.accounts.read", "description": "View mail accounts for tenant domain"},
    {"name": "mail.accounts.create", "description": "Create new mail accounts"},
    {"name": "mail.accounts.password.update", "description": "Change mail account passwords"}
  ]
}
```

Naming: `<module>.<resource>.<action>` (Core convention).

---

## 12. Events

`plugins/mail/events/mail.json`:

```json
{
  "events": [
    {"name": "mail.account.created", "description": "A mail account was created"},
    {"name": "mail.account.password_changed", "description": "A mail account password was changed"}
  ]
}
```

---

## 13. Dependencies

Add to `vendor/systutor-core/pyproject.toml` `[project] dependencies`:

```
"paramiko>=3.4,<4.0"
```

---

## 14. Tests

`plugins/mail/backend/tests/`:

### test_service.py

Mock `MailProvider` at dependency level. No real DMS needed.

1. **Tenant isolation — list**: Tenant A gets only `*@acme.com`, Tenant B gets only `*@otro.com`.
2. **Domain case-insensitive**: `ACME.COM` matches `acme.com`.
3. **No partial domain match**: `acme.com.fake` does NOT match `acme.com`.
4. **No subdomain match**: `sub.acme.com` does NOT match `acme.com`.
5. **Cross-tenant create blocked**: Tenant A cannot create `user@otro.com`.
6. **Cross-tenant password change blocked**: Tenant A cannot change password for `user@otro.com`.
7. **Username injection blocked**: `ventas@otro.com` as username → rejected by schema validation.
8. **Malicious inputs rejected**: `; rm -rf /`, `$(whoami)`, backticks → all rejected.
9. **SSH failure → 503**: Provider raises → service maps to `ServiceUnavailableError`.
10. **Account exists → 409**: Provider raises → service maps to `ConflictError`.
11. **Account not found → 404**: Provider raises → service maps to `NotFoundError`.
12. **No password in logs**: Capture log output, assert no password substring.
13. **No password in audit events**: Capture published events, assert no password field.
14. **No password in response**: Response body contains no password.

### test_schemas.py

- Username: valid (`ventas`, `info.sub`, `admin+test`), invalid (`ventas@`, `a b`, `a`*65).
- Password: valid (8+ chars), invalid (< 8 chars).

### test_router.py

- Use `httpx.AsyncClient` with FastAPI test app.
- Mock `MailProvider` via `app.dependency_overrides`.
- Verify HTTP status codes for all error scenarios.

Run: `cd vendor/systutor-core && python -m pytest plugins/mail/ -v`

---

## 15. Security Constraints

| Constraint | Implementation |
|---|---|
| Passwords never in PostgreSQL | DMS stores passwords; plugin never persists them |
| Passwords never in logs | No logging of password values |
| Passwords never in audit | Audit events contain only email, not password |
| Passwords never in responses | Response schemas have no password field |
| Passwords sent via stdin only | paramiko `exec_command` + stdin channel write |
| Domain from backend only | `tenant.domain` from DB, not from request |
| Backend validates tenant ownership | Every mutation checks domain match |
| No shell=True | paramiko exec_command with args list |
| No user input in commands | Email/username validated before use |
| MAIL_SERVER_PASSWORD protected | Env var, never logged, never in HTTP |

---

## 16. Not Implemented (Pilot)

- Account deletion
- Aliases, forwarding
- Quotas
- DKIM, SPF, DMARC, DNS
- SMTP/IMAP settings
- Mail logs, statistics
- Mailbox storage tracking
- Periodic sync, jobs, workers
- Redis, Celery for mail
- Webhooks, caching
- Advanced search, pagination
- Mail-specific RBAC
- Multiple providers
- Multiple mailservers per tenant
- Auto server discovery
- Generic Docker administration

---

## 17. Success Criteria

1. Login as tenant user → open "Correos".
2. See only mailboxes for my tenant's domain.
3. Create `nuevo@mi-dominio.com`.
4. See new account immediately in list.
5. Change its password.
6. Cannot view or modify other tenants' accounts.

All via SSH to existing Docker Mailserver. Nothing more.
