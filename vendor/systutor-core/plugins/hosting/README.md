# WordPress Hosting

A.SPEC 0013/0014. This plugin lets a tenant administrator see the WordPress sites
that **their tenant owns**, list the users of one of those sites, and reset the
password of exactly one user by numeric WordPress ID.

## Ownership is a row, not a label

`hosting_sites` is the source of truth. A container is not owned because it
exists, because its image says `wordpress`, or because it carries a
`spanel.tenant` label: anybody with Docker access can write a label. Every
site lookup filters `tenant_id` against the core `TenantContext`.

Existing containers have no owner until an operator adopts them:

```bash
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run
```

Adoption is an operator CLI, never an HTTP endpoint, and it refuses a container
that is already in `hosting_sites`.

## API

All three routes require the kernel `TenantContext`:

- `GET /api/v1/plugins/hosting/sites` — `hosting.sites.read`
- `GET /api/v1/plugins/hosting/sites/{site_id}/users` — `hosting.users.read`
- `POST /api/v1/plugins/hosting/sites/{site_id}/users/{wp_user_id}/password` —
  `hosting.users.password`
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`; accepts name,
  domain and admin_email, and returns the generated admin password once.
- `POST /api/v1/plugins/hosting/sites/{site_id}/{start|stop|restart}` —
  `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{site_id}/logs?lines=200` — `hosting.sites.read`
- `DELETE /api/v1/plugins/hosting/sites/{site_id}` — `hosting.sites.delete`
  (platform permission only; permanently removes the site's containers and data).

The request schemas forbid extra fields: a client cannot send `tenant_id`, a
container name, or any other Docker selector. A site that belongs to another
tenant is indistinguishable from a missing site and returns 404.

The container name is resolved from the owned row and then used with the proven
Docker/WordPress core from A.SPEC 0012 (`discovery`, `database`, `passwords`,
`reset`). A container that no longer exists is listed as `missing`; the users
and reset routes answer 409 and name the container.

## Reset

`POST .../users/{wp_user_id}/password` resolves the user by ID only, generates a
password when the body omits it, writes exactly one row, reads it back by ID,
and returns the generated password once with `Cache-Control: no-store`. A
password supplied by the caller is never echoed. No password or hash is written
to logs, audit details or list responses.

The write is audited with `record_audit` (`hosting.user.password_reset`); this
plugin declares no events in A.SPEC 0013.

## Configuration

The Docker transport comes from the core `.env`:

- `SPANEL_DOCKER_TRANSPORT` (`ssh` or `local`)
- `SPANEL_DOCKER_SSH_HOST`, `SPANEL_DOCKER_SSH_USER`, `SPANEL_DOCKER_SSH_PORT`,
  `SPANEL_DOCKER_SSH_STRICT_HOST_KEY`
- `SPANEL_DOCKER_SSH_PASSWORD`, falling back to `MAIL_SERVER_PASSWORD`
- `SPANEL_HOSTING_COMMAND_TIMEOUT` (default 60 seconds)
- `SPANEL_HOSTING_WP_IMAGE` (`wordpress:latest`), `SPANEL_HOSTING_DB_IMAGE`
  (`mariadb:11`), `SPANEL_HOSTING_NETWORK` (`proxy`, must already exist)
- `SPANEL_HOSTING_TRAEFIK` (`true`), `SPANEL_HOSTING_INSTALL_TIMEOUT` (180 s),
  `SPANEL_HOSTING_LOG_LINES` (200, capped at 1000)

Provisioning creates two labeled containers and two labeled volumes, installs
WordPress using `wordpress:cli` and only then records ownership. A failed
installation compensates by removing only resources from that attempt. Database
and admin passwords never travel in command arguments; transient environment
files are removed after provisioning. The created network is shared and never
removed by the site deletion path.

No secret is logged and the settings object is never returned by a route.

## Rollback

Disable the plugin: the routes and navigation disappear and the
`hosting_sites` rows remain. A full rollback runs the migration downgrade,
which drops the table and therefore loses ownership metadata; the containers
and volumes are untouched.
