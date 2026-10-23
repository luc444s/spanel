# A.SPEC 0015 — Read and change a site's configuration without exposing its secrets

> `risk: high` — this recreates live containers and writes the file that holds the WordPress
> database secret. A wrong argument in the recreate can take a site down, and a wrong response can
> leak a password. Mode: `full`. Approver required.

## WHY

After A.SPEC 0013/0014 a tenant can own, create and operate a site, but it cannot change anything
about how that site runs. Three concrete gaps:

- The database password is generated during provisioning, written to a `/tmp` env-file and deleted
  immediately. After a reboot there is no stable copy the panel can read, so rotation is impossible.
- A tenant cannot raise `PHP_MEMORY_LIMIT`, turn `WP_DEBUG` on for an incident, or rotate the
  WordPress authentication salts without SSH access to the Docker host.
- Nothing decides which environment variables are safe to expose. If the panel let a tenant rewrite
  `WORDPRESS_DB_HOST`, that tenant could point its site at another tenant's database container:
  every WordPress on the host shares the configured Docker network.

The danger is not "editing a file". It is that a container's environment plus its secrets is
effectively control over what runs with the Docker daemon's privileges. This slice therefore gives
the tenant a **narrow, whitelisted** surface, and keeps the full compose definition for a later,
platform-only A.SPEC.

## WHAT

**A tenant with `hosting.sites.manage` can read the editable environment of its own site, change
only whitelisted keys, and rotate the WordPress authentication salts. The change is persisted to a
stable mode-600 file on the Docker host and applied by recreating the container from its own
`docker inspect`, reusing its named volumes and labels. Secret values are never returned.**

Falsable today, and today **fails**: the `config` and `secrets` routes do not exist.

## SCOPE

1. `plugins/hosting/backend/config.py` (new) — the canonical model and the first adapter:
   - `ENV_WHITELIST`: the only editable keys (`PHP_MEMORY_LIMIT`, `WP_MEMORY_LIMIT`,
     `UPLOAD_LIMIT`, `WP_DEBUG`, `WP_DEBUG_LOG`, `WP_DEBUG_DISPLAY`, `WP_POST_REVISIONS`,
     `WP_AUTO_UPDATE_CORE`, `TZ`), each with a value pattern;
   - `SECRET_KEYS` (`WORDPRESS_DB_PASSWORD`, `MYSQL_PWD`): names only, never values;
   - `parse_env`, `render_env`, `validate_env_updates`, `editable_env`, `secret_names`, `env_of`;
   - `site_dir` / `env_path`: the stable per-site directory, `<sites_dir>/<container>/wp.env`;
   - `build_run_args(info, name, env_file)`: pure reconstruction of the `docker run` argv from the
     container's own inspect (image, network, labels, named and bind mounts, restart policy,
     published ports, command);
   - `RawAdapter.read_config`, `update_config`, `rotate_salts`, and `_ensure_env_file`.
2. `plugins/hosting/backend/transport.py`:
   - `escribir_archivo_remoto_en(env, ruta, contenido)`: writes a stable path with
     `mkdir -p` + `umask 077` + `chmod 600`, content over stdin, path quoted;
   - `leer_archivo_remoto(env, ruta)`: returns the text or `None`.
3. `plugins/hosting/backend/settings.py`:
   - `SPANEL_HOSTING_SITES_DIR` (default `/opt/spanel/sites`; the SSH user must be
     able to write it).
4. `plugins/hosting/backend/provisioner.py`:
   - creation writes `db.env` and `wp.env` to `<sites_dir>/<slug>/` with mode 600
     from the first second, and passes those stable paths to `--env-file`
     instead of a `/tmp` file that is deleted. A failed creation removes the
     directory it wrote.
5. `plugins/hosting/backend/schemas.py`:
   - `SiteConfigResponse` (`site_id`, `container_name`, `editable_env`, `secret_names`,
     `editable_keys`);
   - `SiteConfigUpdateRequest` (`extra="forbid"`, `env: dict[str, str]`);
   - `SecretRotationResponse` (`site_id`, `container_name`, `rotated`).
6. `plugins/hosting/backend/service.py`:
   - `get_site_config(site)` and `update_site_config(site, tenant_context, env)`;
   - `rotate_site_salts(site, tenant_context)`;
   - the delete path removes the site's stable directory, so a deleted site's secret does not
     stay on disk;
   - every write audits with the tenant, branch and actor from `TenantContext`, and the audit
     details list only the keys, never a value.
7. `plugins/hosting/backend/router.py`:
   - `GET  /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.read`;
   - `PATCH /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.manage`;
   - `POST /api/v1/plugins/hosting/sites/{site_id}/secrets/salts` — `hosting.sites.manage`.
8. `plugins/hosting/README.md`: the three routes, the whitelist, the stable directory.
9. Tests:
   - pure: `plugins/hosting/backend/tests/test_config.py` (whitelist, env round-trip, argv
     reconstruction, stable path);
   - schema: `test_schemas.py` rejects `container_name` and `tenant_id`;
   - live: `scripts/tests/test_hosting_config_live.py`, including reading the secret file itself.
10. This document.

## OUT OF SCOPE

- **The compose adapter.** A site defined by `docker-compose.yml` will implement the same adapter
  protocol in a later A.SPEC. This slice only covers the containers that exist today, created with
  `docker run`.
- **Database password rotation.** Rotating `WORDPRESS_DB_PASSWORD` requires changing the MariaDB
  user and the WordPress environment together, with a verification step. It is its own A.SPEC.
- **Editing the database keys.** `WORDPRESS_DB_*` and `MYSQL_PWD` are deliberately not editable.
- **Full compose editing** (image, volumes, networks, ports) by a tenant. That remains a
  platform-only operation.
- **Frontend.** This slice is backend and contracts only; the UI comes with the adapter it needs.
- **Backups, restores and site migration.**
- **`docker system prune` or any wildcard cleanup.**

## CONTRACT

**Preconditions**

- The site is an owned `hosting_sites` row and its container exists.
- The caller has `hosting.sites.read` for `GET`, `hosting.sites.manage` for `PATCH` and the salt
  rotation.
- The configured Docker network exists.

**Postconditions — read**

- The response contains the whitelisted environment present in the container, the sorted list of
  editable keys and the NAMES of the secrets that exist.
- No secret value appears in the response, in a log line or in the audit row.

**Postconditions — update**

- Every key in `env` is checked against `ENV_WHITELIST`; an unknown key, a database key, a newline
  or a value that does not match the key's pattern returns 422 and writes nothing.
- On success the merged environment is written to `<sites_dir>/<container>/wp.env` with mode 600,
  and the container is recreated from its own inspect with the same name, labels, network, named
  volumes, restart policy and ports.
- The named volumes are reused; the WordPress install, users and posts are unchanged.
- The response reflects the recreated container's environment.
- The write is audited with the key names only.

**Postconditions — delete**

- Deleting the site removes its stable directory after the containers and volumes are gone, so the
  database secret of a deleted site does not remain on the host.

**Postconditions — salts**

- `wp config shuffle-salts` runs against the site volume as the volume's owner; `AUTH_KEY` (and the
  other salts) change.
- No container is recreated and no request is served with the old salts after the command returns.
- The rotation is audited; neither the old nor the new value is recorded.

**New falsifiable truth**

Create a lab site, read its config, change `PHP_MEMORY_LIMIT`, read the stable env file on the
Docker host and see the new value next to the unchanged database secret, then rotate the salts and
see `AUTH_KEY` change while `wp core is-installed` still succeeds. Repeat `GET`/`PATCH` with
another tenant's token: both return 404 and the file is unchanged.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      The site is always resolved from the owned row and TenantContext; no request field can
      select a container or a tenant.
    proof: >
      test_otro_tenant_no_lee_ni_escribe: token B gets 404 on GET and PATCH, and the file is
      unchanged.

  - id: I2
    statement: >
      A secret value never leaves the server through a response, a log line or an audit row.
    proof: >
      test_get_config_no_devuelve_el_secreto reads the value from the stable file and asserts it is
      absent from the response body.

  - id: I3
    statement: >
      Only whitelisted keys are editable. The database keys are rejected with 422 and nothing is
      written.
    proof: >
      test_patch_rechaza_claves_de_conexion and the pure whitelist tests.

  - id: I4
    statement: >
      Recreating a container preserves its named volumes, so the WordPress install and its data
      survive the config change.
    proof: >
      test_patch_escribe_y_recrea_sin_perder_datos: the container id changes, the new env is
      present, and `wp core is-installed` still succeeds.

  - id: I5
    statement: >
      The stable env file lives outside /tmp and is mode 600.
    proof: >
      test_el_archivo_de_secretos_es_600.

  - id: I6
    statement: >
      Rotating salts changes the WordPress authentication keys and is audited without recording a
      key value.
    proof: >
      test_rotar_sales_cambia_wp_config_y_audita.

  - id: I7
    statement: >
      No command uses a wildcard, a prune or a container name taken from the request.
    proof: >
      static check: `config.py` only builds names from `site.container_name`, and no path contains
      `prune` or `spanel-*`.

  - id: I8
    statement: >
      Deleting a site removes its stable env file, so a deleted site's database secret does not stay
      on disk.
    proof: >
      test_delete_borra_el_archivo_de_secretos.
```

## VERIFICATION

**Gates**

```bash
ruff check vendor/systutor-core/plugins/hosting
python3 -m pyright vendor/systutor-core/plugins/hosting
PYTHONPATH=vendor/systutor-core/src:vendor/systutor-core \
  python3 -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
```

**Live, against the Docker host**

```bash
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_config_live.py -q
```

| Test | Invariant | What it falsifies |
|---|---|---|
| `test_get_config_no_devuelve_el_secreto` | I2 | the response leaks the DB password |
| `test_el_archivo_de_secretos_es_600` | I5 | the secret file is world-readable |
| `test_patch_escribe_y_recrea_sin_perder_datos` | I3, I4 | the recreate drops the volume, the env or WordPress |
| `test_patch_rechaza_claves_de_conexion` | I3 | a tenant can repoint the database |
| `test_otro_tenant_no_lee_ni_escribe` | I1 | cross-tenant config access |
| `test_rotar_sales_cambia_wp_config_y_audita` | I6 | rotation is a no-op or is not audited |
| `test_delete_borra_el_archivo_de_secretos` | I8 | a deleted site's secret stays on disk |

Result on the lab host (2026-10-23): the six config cases passed in one run (363 s), the delete
case passed separately (57 s), and the pure/schema suite passed `34`.

The live suite creates its own `spanel-lab-*` site and removes it in teardown. It refuses to run
without `SPANEL_LIVE_TESTS=1`.

## ROLLBACK

Code: `git revert` of the commit. No kernel migration and no schema change.

State: the stable directory keeps a copy of each site's environment. Reverting the code leaves the
files in place and harmless; remove `/opt/spanel/sites/<container>/wp.env` only after exporting it,
because it is the only stable copy of that site's database secret. The containers and volumes are
untouched by the revert.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0015-hosting-site-config-and-secrets.md
    - vendor/systutor-core/plugins/hosting/README.md
    - vendor/systutor-core/plugins/hosting/backend/config.py            # new
    - vendor/systutor-core/plugins/hosting/backend/transport.py
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_config.py # new
    - vendor/systutor-core/plugins/hosting/backend/tests/test_schemas.py
    - scripts/tests/test_hosting_config_live.py                          # new
    - vendor/systutor-core/.env.example
  prohibited:
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/hosting/migrations/**
    - scripts/probe_docker.py
    - scripts/reset_wp_password.py
    - docker-compose.yml
    - .env
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - the live container of every site whose config is updated (recreated)
    - the stable per-site env file on the Docker host
    - the WordPress authentication salts of a site that is rotated
  indirect:
    - the site list and the users/reset routes, which must keep working after a recreate
    - the audit log, which gains config and rotation rows
  must_not_affect:
    - surface: sitios_existentes
      surfaces: [contenedores, volumenes, usuarios, posts]
      invariant: I4
    - surface: secretos
      surfaces: [respuestas, logs, audit]
      invariant: I2
    - surface: separacion_tenant
      surfaces: [config, secrets]
      invariant: I1
    - surface: reset_y_listado
      surfaces: [A.SPEC 0013 endpoints]
      invariant: the 0013 live suite stays green
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0013   # hosting_sites, guard, service and the read routes
    - 0014   # create/operate; a lab site is created by the same API
  must_compose_with:
    - 0012   # transport and credential-safe commands
  systemic_invariants:
    - Tenant identity always comes from TenantContext.
    - A secret value is never returned, logged or audited.
    - A recreate reuses the named volumes and never removes them.
  composition_checks:
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_api_live.py -q
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_lifecycle_live.py -q
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_config_live.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/config.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
```

`config.py` owns the canonical model and the raw adapter. `service.py` owns tenant scope, audit and
orchestration. The router owns HTTP only. `transport.py` stays the single way the package talks to
the Docker host.

## Traceability

- **Requirement**: a tenant can read and change the safe configuration of its own site and rotate
  its authentication salts, without the secret ever leaving the server
- **A.SPEC**: this document
- **Code**: `config.py`, `transport.py`, `settings.py`, `provisioner.py`, `service.py`,
  `router.py`
- **Migration**: none
- **Test**: `plugins/hosting/backend/tests/test_config.py`,
  `scripts/tests/test_hosting_config_live.py`
- **Commit**: `TBD`
- **Deployment**: rebuild the image; the Docker host must allow writing to
  `SPANEL_HOSTING_SITES_DIR` and have the `wordpress:cli` image
- **owner**: `TBD`
- **approver**: `luc444s` — explicit review required before IMPLEMENT because `risk: high`

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
