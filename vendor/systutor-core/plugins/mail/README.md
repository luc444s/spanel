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
MAIL_ACCOUNTS_CACHE_TTL=300
MAIL_SSH_CONTROL_PATH=/tmp/systutor-ssh/%r@%h:%p
MAIL_SSH_CONTROL_PERSIST=60
```

`MAIL_ACCOUNTS_CACHE_ENABLED=false` disables the cache entirely: every read goes
straight to the mail server. This is the kill switch for production.

`MAIL_ACCOUNTS_CACHE_TTL` is the freshness window. After it passes, `MAIL_ACCOUNTS_CACHE_SWR`
(default on) serves the cached list and refreshes it in the background, so a visit never
waits for `docker exec` just because the TTL elapsed. `MAIL_ACCOUNTS_CACHE_STALE_TTL` is the
hard cap: an entry older than that is no longer served stale and the request blocks on the
provider. Creating a mailbox updates the cached list in place instead of dropping it, so the
refetch that follows a create is also instant. Changes made **outside** the app become
visible on the first visit after the TTL, when the background refresh runs.

## SSH connection reuse

`MAIL_SSH_CONTROL_PATH` enables OpenSSH `ControlMaster`, so the provider reuses one
session instead of opening one per call. Measured on the dev host, the handshake was
~560 ms of a ~1250 ms cold read; with multiplexing the steady state is ~610 ms.

- Empty (the default) keeps the command line byte-for-byte what it always was.
- The socket directory is created `0700` and chmod'ed on every use. A world-writable
  directory would let another local user pre-create the socket and hijack the session.
- If the directory cannot be created — read-only filesystem, unwritable `/tmp` — the
  provider logs a warning and falls back to one connection per call. It never fails.
- Only has an effect with `MAIL_USE_SSH=true`. With `false` the provider runs
  `docker exec` locally and this setting is ignored.
- The path may use the `%r`, `%h` and `%p` tokens; keep the expanded socket path well
  under the ~104 character unix socket limit.

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
