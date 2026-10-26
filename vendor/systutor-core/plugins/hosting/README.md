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
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`; accepts
  `{"kind": "wordpress"|"static", "name": "...", "domain": "..."}` and returns
  the generated admin password once (`null` for a static site). `admin_email`
  is no longer accepted: WordPress uses the authenticated user's email, with
  `admin@<domain>` as fallback. The domain must belong to the tenant's
  `site_suffix` and be free in `hosting_sites` and in the proxy extras.
- `POST /api/v1/plugins/hosting/sites` with `kind="static"` provisions
  `spanel-<slug>-web` plus its homonymous volume, seeds `index.html`,
  `styles.css` and `app.js`, and serves them with `nginx:alpine`. It creates no
  database, WordPress user or password. When the `domains` plugin is enabled,
  its file-provider publisher owns the primary route; otherwise the container
  gets Traefik labels exactly like WordPress. `start`, `stop`, `restart`,
  `logs` and the filemanager work for both kinds; `config`, `database`,
  `phpmyadmin`, `users`, `secrets/salts` and password reset answer
  `409 hosting_site_wrong_kind` on a static site.
- `POST /api/v1/plugins/hosting/sites/{site_id}/{start|stop|restart}` —
  `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{site_id}/logs?lines=200` — `hosting.sites.read`
- `DELETE /api/v1/plugins/hosting/sites/{site_id}` — `hosting.sites.delete`
  (platform permission only; permanently removes the site's containers and data).
- `GET /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.read`;
  returns the editable environment, the list of editable keys and the NAMES of
  the secrets that exist. Secret values are never returned.
- `PATCH /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.manage`;
  accepts `{"env": {"KEY": "value"}}` for whitelisted keys only. The change is
  written to the site's stable env file and applied by recreating the container
  from its own `docker inspect`, reusing the named volumes and labels. The
  database keys (`WORDPRESS_DB_*`, `MYSQL_PWD`) are rejected: a tenant that
  could rewrite them could point its site at another tenant's database.
- `POST /api/v1/plugins/hosting/sites/{site_id}/secrets/salts` —
  `hosting.sites.manage`; rotates the WordPress authentication salts with
  `wp config shuffle-salts`. The salts live in `wp-config.php` inside the site
  volume and PHP reads them per request, so this needs no restart and no
  recreate. The response says what was rotated, never the new value.
- `POST /api/v1/plugins/hosting/sites/{site_id}/database` — `hosting.sites.manage`;
  returns the site's **own** WordPress database credential (`host`, `database`,
  `user`, `password`) and the phpMyAdmin URL, with `Cache-Control: no-store`.
  The host comes from the container environment and must be a Docker container
  name (a shared MariaDB or a per-site one); a scheme, `localhost` or an IP is
  409. The password is audited as `hosting.site.database_access` without the
  value.

The request schemas forbid extra fields: a client cannot send `tenant_id`, a
container name, or any other Docker selector. A site that belongs to another
tenant is indistinguishable from a missing site and returns 404.

## Configuration UI

The site detail dialog has a `Configuración` action. It opens the editable
environment and the names of the secrets; values are never shown. Without
`hosting.sites.manage`, or on a site with `spanel.managed=false`, it is
read-only. Saving sends only changed, non-empty values and recreates the
container, so the dialog warns about the brief downtime. `Rotar claves de seguridad` asks for
confirmation first, because rotating the salts closes every WordPress session.

## Database access

`Base de datos`, next to `Configuración` and visible only with
`hosting.sites.manage`, reveals the site's own WordPress database user and
opens phpMyAdmin in a new same-origin tab. It is the credential the site already
uses, so
there is no new SQL user, no grant and no migration; phpMyAdmin sees exactly one
database. The host is derived from the owned site and validated as a Docker
container name. The proxy uses `SPANEL_PHPMYADMIN_INTERNAL_URL` over a
shared internal Docker network; the app container must resolve that address.
A Bearer-authenticated POST logs in server-side as the site's own WordPress DB
user and returns a 30-second one-time tab ticket. An opaque site-scoped
cookie authorizes subsequent proxy requests. No database password appears in
the tab URL, proxy cookie or session cache. `SPANEL_PHPMYADMIN_URL` remains
only in the legacy database response and is no longer followed by the UI.

### One phpMyAdmin for every database (A.SPEC 0033)

With `SPANEL_PHPMYADMIN_ARBITRARY=true` and `SPANEL_PHPMYADMIN_DB_NETWORK` set,
phpMyAdmin runs as a multi-server hub (`PMA_ARBITRARY=1` plus the
`ArbitraryServerRegexp` in `scripts/phpmyadmin_arbitrary.php`). The login POST
carries `pma_servername=<database container>`, the host already validated by
`database_binding`. Before that POST the service reads
`NetworkSettings.Networks` from the database's `docker inspect`; it runs
`docker network connect <network> <container>` only when the interface is
missing, so a second login is a no-op. The container's original networks are
untouched. `network.py` owns that pure decision; `service.py` only performs the
transport.

Provisioning a WordPress site attaches its new MariaDB to the same network right
after the container is created. If that attach fails, creation follows the
existing failure path and removes its own containers and volumes. The audit row
`hosting.site.phpmyadmin_access` includes `database`, `host` and, only when a
connection happened, `network`; never the password.

Either setting alone (or both off/empty) preserves the previous behavior
byte-for-byte: fixed `PMA_HOST`, no `pma_servername`, no Docker network command.
The deployment script creates `spanel-db` as `--internal`, keeps binding
`127.0.0.1`, and attaches both the legacy MariaDB and phpMyAdmin to it:

```bash
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
```

The four values are read from the container environment (`WORDPRESS_DB_*`)
first. When the environment does not declare them, the plugin reads
`/var/www/html/wp-config.php` as **text** and extracts the literal `DB_HOST`,
`DB_NAME`, `DB_USER` and `DB_PASSWORD` definitions. It does not execute PHP and
does not modify the file, the container or the database. A database container
outside WordPress is supported, including a custom port such as
`mariadb:3306`; the container name is validated the same way as the
environment path. The user list and the password reset use the same two
sources.

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
- `SPANEL_HOSTING_SITES_DIR` (`/opt/spanel/sites`): the stable, per-site
  directory on the Docker host. The config route writes `wp.env` there with mode
  600, so the WordPress database secret survives a reboot and can be read again.
  Before this, the env files lived in `/tmp` and were deleted after provisioning.
- `SPANEL_PHPMYADMIN_URL` (optional): base URL of the phpMyAdmin instance. It
  may contain `{server}`; retained for compatibility in the database response.
- `SPANEL_PHPMYADMIN_INTERNAL_URL` (default `http://spanel-phpmyadmin:8080`):
  server-only upstream on a network shared by phpMyAdmin and the app.
- `SPANEL_PHPMYADMIN_SESSION_TTL` (1500 s, capped at 3600),
  `SPANEL_PHPMYADMIN_TIMEOUT` (15 s).
- `SPANEL_PHPMYADMIN_ARBITRARY` (`false`): with `true`, the login sends
  `pma_servername`; ignored unless `SPANEL_PHPMYADMIN_DB_NETWORK` is set.
- `SPANEL_PHPMYADMIN_DB_NETWORK` (empty): internal Docker network shared by
  phpMyAdmin and every site MariaDB. Empty disables the multi-database mode.
- `SPANEL_HOSTING_FILES_ROOT` (`/var/www/html`): server-side root that Hosting
  supplies to the `filemanager` plugin for an owned running site. The client
  never chooses the container or an absolute path.

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
