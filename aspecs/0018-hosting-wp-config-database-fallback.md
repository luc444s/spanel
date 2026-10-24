# A.SPEC 0018 — Read database credentials from `wp-config.php` when the environment is silent

> `risk: high` — the result is a database credential. The fallback must never
> execute PHP, never write to the site, and never put the password in a log,
> an error message or an audit row. Mode: `full`. Approver required.

## WHY

The hosting plugin resolves a site's database only from the WordPress container
environment:

```python
env = env_of(info)
host = env.get("WORDPRESS_DB_HOST", "")
database = env.get("WORDPRESS_DB_NAME", "")
user = env.get("WORDPRESS_DB_USER", "")
password = env.get("WORDPRESS_DB_PASSWORD", "")
```

A site can be perfectly valid and still fail that check. When the image or the
operator writes the four constants directly into `/var/www/html/wp-config.php`,
there are no `WORDPRESS_DB_*` variables to read. The panel then shows:

```
El sitio no declara su base de datos en el entorno
```

and the database dialog, the user list and the password reset all stop working,
even though WordPress itself connects to its database without a problem.

The database may be a **MariaDB container outside the WordPress container**,
shared by several sites (`mariadb`, `spanel-lab-mariadb`) or addressed with a
custom port (`mariadb:3306`). That is a supported topology and the fallback must
keep it supported.

## WHAT

**When the four `WORDPRESS_DB_*` variables are not all present, the hosting
service reads the site's own `wp-config.php` as text, extracts the four literal
`DB_*` constants with a pure parser, and uses that credential exactly as if it
had come from the environment. The environment remains the first source. No PHP
is executed and nothing is written.**

Falsable today, and today **fails**: a container whose only declaration is
`wp-config.php` returns 409.

## SCOPE

`service.py` already exceeds 600 lines, so neither the parser nor the source
selection goes there. The new logic lives in focused modules and `service.py`
only wires them.

1. `plugins/hosting/backend/wpconfig.py` (new, focused module):
   - `WP_CONFIG_PATH = "/var/www/html/wp-config.php"`;
   - `parse_wp_config(texto)`: pure parser of literal `define('DB_*', '...')`
     statements; comment-aware, never evaluates PHP;
   - `read_wp_config(env, container_name, *, timeout)`: one read-only
     `docker exec ... cat /var/www/html/wp-config.php`; returns `None` on any
     failure and never includes the content in an error;
   - `credentials_from_wp_config(texto)`: returns the four raw values only when
     all of them are present, or `None`.
2. `plugins/hosting/backend/database_binding.py` (new, focused module):
   - `database_binding(info, *, wp_config=None)`: environment first, then the
     parsed file, then the existing 409;
   - `database_credentials(info, *, wp_config=None)`: the four-value wrapper;
   - host validation and port normalization live here, not in the service.
3. `plugins/hosting/backend/service.py` (only the integration):
   - a thin `database_credentials` re-export keeps the existing service API;
   - `_database_binding_for(site, info)`: calls `read_wp_config` for the owned
     container only when `database_binding` fails;
   - `_credentials(site, info)`: the same resolution for the user list and the
     password reset, building the `_contenido` env file from the resolved
     values instead of calling `conexion_de_la_base` directly;
   - `site_database`: uses the same binding; the response, audit and
     phpMyAdmin URL stay unchanged.
4. `plugins/hosting/backend/tests/test_wpconfig.py` (new): parser, comment
   handling, quoting, literal-only rule, missing constants, the reader command
   (`cat`, never `php`) and a failed read that stays silent.
5. `plugins/hosting/backend/tests/test_database_access.py`: unit tests for both
   detection methods, environment precedence, external MariaDB and custom
   ports, and the no-secret-in-error rule.
6. `scripts/tests/test_hosting_wp_config_live.py` (new): a read-only live test
   against an existing local container selected by `SPANEL_WP_CONFIG_CONTAINER`;
   skipped unless `SPANEL_LIVE_TESTS=1`. It creates, starts, stops and deletes
   nothing.
7. `plugins/hosting/README.md`: the two sources and the safety rules.
8. This document.

## OUT OF SCOPE

- **Executing PHP.** No `php -r`, no `wp eval`, no `wp config get`.
- **Writing WordPress files.** The fallback only reads; it never repairs,
  normalizes or creates `wp-config.php`.
- **Changing containers, databases or users.** No `docker start`, no `docker
  cp`, no SQL, no grants, no recreation.
- **Expressions, includes or variables in `wp-config.php`.** A value such as
  `getenv('DB_PASSWORD')`, a concatenation or an included file is not evaluated
  and is treated as not declared by the file.
- **Non-container MariaDB** reachable only over the network but invisible to
  `docker inspect` on the configured Docker host. This plugin's database
  identity is always a container on that host.
- **New routes, schemas, permissions, migrations or frontend changes.**
- **Changing the environment-first behaviour** when all four variables exist.

## CONTRACT

**Preconditions**

- The site is an owned `hosting_sites` row and its WordPress container exists
  and is running.
- The caller already has the permission of the route it invoked.
- The site container can be read with `docker exec` by the configured Docker
  transport.

**Detection order**

1. All four `WORDPRESS_DB_*` values from `Config.Env` and a valid host win.
   `wp-config.php` is not read.
2. Otherwise the service reads `/var/www/html/wp-config.php` from the owned
   site container with `cat`.
3. The parser extracts `DB_HOST`, `DB_NAME`, `DB_USER` and `DB_PASSWORD`.
4. If either source yields all four values and a valid host, the result is
   `(host, database, user, password)`.
5. If neither does, the existing `409 hosting_database_unavailable` is raised.
   The message may name both sources; it never contains a value.

**`wp-config.php` parser**

- Only literal calls of the form `define('DB_NAME', 'value')` or
  `define("DB_NAME", "value")` are accepted.
- Comments `//`, `#` and `/* ... */` are ignored, including commented-out
  defines.
- A quoted value may contain the other quote, `$`, `#`, `//`, spaces and
  escaped quotes; those characters are data, not PHP.
- The first literal definition of a constant wins, matching `define()`.
- A constant defined through a variable, an expression, `getenv()` or an
  include is absent, not guessed.
- The parser is a pure function over the text; it has no Docker, filesystem or
  PHP dependency.

**Host and port**

- `DB_HOST` is validated with the same rule as `WORDPRESS_DB_HOST`.
- `mariadb:3306` is accepted and normalized to `mariadb` for `docker inspect`.
- A shared MariaDB container (`spanel-lab-mariadb`), an external container with
  a DNS-style name (`mariadb`, `db.example.com`) and a per-site container
  (`spanel-<slug>-db`) are all accepted when they are Docker containers on the
  configured host.
- A scheme, `localhost`, `127.0.0.1`, `::1` or a path is still 409.

**Consumers**

- `POST /sites/{site_id}/database` uses the same resolved credential for the
  response, the running check and the audit.
- `GET /sites/{site_id}/users` and
  `POST /sites/{site_id}/users/{wp_user_id}/password` use the same resolved
  credential for the database container and the SQL channel.
- The password is still never an argv element and never appears in a response
  outside the explicit database route.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: The container environment is the first and preferred source.
    proof: >
      test_environment_wins_over_wp_config declares different values in both
      sources and asserts the environment tuple. test_credentials_come_from_the_site_environment
      (existing) still passes unchanged.

  - id: I2
    statement: The fallback is read-only and never executes PHP.
    proof: >
      test_read_wp_config_uses_cat_and_not_php asserts the constructed command
      contains `cat /var/www/html/wp-config.php` and no `php`. The only side
      effect of the fallback is one docker exec read.

  - id: I3
    statement: No credential value is logged, audited or included in an error.
    proof: >
      Tests assert that a 409 message does not contain the parsed password and
      that the read failure returns None without embedding stdout. The audit
      row code is unchanged and still receives only database and host.

  - id: I4
    statement: A MariaDB container outside WordPress works, including a custom port.
    proof: >
      A unit test uses DB_HOST='mariadb:3306' and asserts the resolved host is
      'mariadb'; another uses 'spanel-lab-mariadb'. Both pass through the same
      host validator as the environment path.

  - id: I5
    statement: The database dialog, the user list and the reset use one resolution rule.
    proof: >
      HostingService._credentials tests cover the wp-config source, and the
      existing environment tests keep the original path. No consumer keeps a
      second environment-only parser for the same values.

  - id: I6
    statement: A site that declares its database nowhere is still a controlled 409.
    proof: >
      test_missing_in_environment_and_wp_config_returns_no_credential asserts
      status 409, code hosting_database_unavailable and no secret in the message.
```

## VERIFICATION

**Unit**

- Environment path: the four values are returned and win over a different
  `wp-config.php`.
- `wp-config.php` path: single and double quotes, indentation, line and block
  comments, commented defines, a password containing `$`, `#`, `//`, spaces and
  escaped quotes.
- Missing one of the four constants returns 409 with no secret in the message.
- Missing from both sources returns 409 with the same code.
- `DB_HOST='mariadb:3306'` resolves to `mariadb`; `spanel-lab-mariadb` is
  accepted; `localhost`, an IP, a scheme and a path are rejected.
- The reader constructs `docker exec <container> cat
  /var/www/html/wp-config.php`, quotes the container name and never mentions
  `php`.
- A failed read returns `None` and does not include the file content.

**Live, read-only**

```bash
SPANEL_LIVE_TESTS=1 \
SPANEL_WP_CONFIG_CONTAINER=<container> \
python3 -m pytest scripts/tests/test_hosting_wp_config_live.py -q
```

- The selected container is inspected and its `wp-config.php` is read with the
  production reader.
- The parser returns the same four values WordPress uses.
- The test does not start, stop, recreate or delete the container, does not run
  PHP and does not write to the site volume.

**Gates**

```bash
python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
ruff check vendor/systutor-core/plugins/hosting/backend/wpconfig.py \
  vendor/systutor-core/plugins/hosting/backend/database_binding.py \
  vendor/systutor-core/plugins/hosting/backend/service.py \
  vendor/systutor-core/plugins/hosting/backend/tests
python3 -m pyright vendor/systutor-core/plugins/hosting/backend
```

## ROLLBACK

Revert `wpconfig.py`, `database_binding.py`, the changes in `service.py`, the
tests, the README and this document. No migration, no SQL, no container change
and no WordPress file was ever written, so the rollback is a `git checkout`.
The panel returns to the environment-only behaviour.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0018-hosting-wp-config-database-fallback.md
    - vendor/systutor-core/plugins/hosting/backend/wpconfig.py
    - vendor/systutor-core/plugins/hosting/backend/database_binding.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_wpconfig.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_database_access.py
    - scripts/tests/test_hosting_wp_config_live.py
    - vendor/systutor-core/plugins/hosting/README.md
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/database.py
    - vendor/systutor-core/plugins/hosting/backend/inspection.py
    - vendor/systutor-core/plugins/hosting/backend/config.py
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/models.py
    - vendor/systutor-core/plugins/hosting/migrations/**
    - vendor/systutor-core/plugins/hosting/frontend/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/src/systutor/**
    - apps/web/src/**
    - docker-compose.yml
    - .env
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - one docker inspect of the owned WordPress container
    - one read-only docker exec of wp-config.php when the environment is incomplete
    - the database dialog, the user list and the password reset share the new source
  indirect:
    - one extra inspect of the database container when the fallback resolves it
    - the same audit row as before
  must_not_affect:
    - surface: wordpress
      invariant: no file and no container is modified
    - surface: mariadb_data
      invariant: no SQL statement is executed by the detection itself
    - surface: secrets
      invariant: the password never enters argv, logs, audit or an error message
    - surface: other_tenants
      invariant: the file is read from the owned site container only
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013   # ownership and site list
    - 0014   # site lifecycle
    - 0015   # secret policy and stable env file
    - 0017   # database route and phpMyAdmin URL
  must_compose_with:
    - WordPress container running on the configured Docker host
    - external MariaDB container reachable on the same Docker network
  composition_checks:
    - python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
    - SPANEL_LIVE_TESTS=1 SPANEL_WP_CONFIG_CONTAINER=<container> python3 -m pytest scripts/tests/test_hosting_wp_config_live.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/wpconfig.py
    - vendor/systutor-core/plugins/hosting/backend/database_binding.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
```

`router.py` keeps its signature. `wpconfig.py` owns the parser and the
read-only reader. `database_binding.py` owns the source order and the host
validation. `service.py` only wires the binding into the database dialog, the
user list and the reset; it does not grow by another parser, command builder or
host validator. The transport still owns the command construction, and
`database.py` keeps its existing environment-only primitive for the standalone
scripts; it is not duplicated for the fallback.

## Traceability

- **Requirement**: a WordPress site whose database is declared only in
  `wp-config.php` exposes its database dialog, user list and password reset.
- **A.SPEC**: this document
- **Code**: `plugins/hosting/backend/wpconfig.py`,
  `plugins/hosting/backend/database_binding.py`,
  `plugins/hosting/backend/service.py`
- **Migration**: none
- **Test**: `plugins/hosting/backend/tests/test_wpconfig.py`,
  `plugins/hosting/backend/tests/test_database_access.py`,
  `scripts/tests/test_hosting_wp_config_live.py`
- **Commit**: `TBD`
- **Deployment**: rebuild the image. No migration and no SQL.
- **owner**: `TBD`
- **approver**: `TBD`

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest
- [ ] Composition checks passed when applicable
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
