# Mail Administration Plugin

Minimalist mail account management plugin for SYSTUTOR Core.

## Features

- List mail accounts for tenant domain
- Create new mail accounts
- Change mail account passwords

## Architecture

- **Provider**: Docker Mailserver via SSH (subprocess + system ssh)
- **Isolation**: Multi-tenant by domain filtering. Holders of `mail.accounts.all`
  read and change accounts of every domain; everyone else is limited to the
  domain of their own tenant.
- **Cache**: the raw account list is cached in the kernel `CacheBackend` (Redis)
  with a TTL, shared by all tenants; filtering by domain happens after the read
- **Security**: Passwords never logged, never in responses

## Configuration

Set environment variables:

```bash
MAIL_PROVIDER=docker-mailserver
MAIL_SERVER_HOST=your-mail-server
MAIL_SERVER_PORT=22
MAIL_SERVER_USER=root
MAIL_SERVER_PASSWORD=your-password
MAIL_DMS_CONTAINER=mailserver
MAIL_ACCOUNTS_CACHE_ENABLED=true
MAIL_ACCOUNTS_CACHE_TTL=60
```

`MAIL_ACCOUNTS_CACHE_ENABLED=false` disables the cache entirely: every read goes
straight to the mail server. This is the kill switch for production.

## Routes

- `GET /api/v1/plugins/mail/mail/accounts` — List accounts
- `POST /api/v1/plugins/mail/mail/accounts` — Create account
- `PUT /api/v1/plugins/mail/mail/accounts/{email}/password` — Change password

## Permissions

- `mail.accounts.read` — View accounts of the tenant domain
- `mail.accounts.all` — View accounts of every domain (implies domain-wide scope
  for password changes too)
- `mail.accounts.create` — Create accounts
- `mail.account.password` — Change passwords
