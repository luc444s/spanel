# Mail Administration Plugin

Minimalist mail account management plugin for SYSTUTOR Core.

## Features

- List mail accounts for tenant domain
- Create new mail accounts
- Change mail account passwords

## Architecture

- **Provider**: Docker Mailserver via SSH (subprocess + system ssh)
- **Isolation**: Multi-tenant by domain filtering
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
```

## Routes

- `GET /api/v1/plugins/mail/mail/accounts` — List accounts
- `POST /api/v1/plugins/mail/mail/accounts` — Create account
- `PUT /api/v1/plugins/mail/mail/accounts/{email}/password` — Change password

## Permissions

- `mail.accounts.read` — View accounts
- `mail.accounts.create` — Create accounts
- `mail.accounts.password.update` — Change passwords
