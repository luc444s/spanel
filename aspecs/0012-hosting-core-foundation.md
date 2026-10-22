# A.SPEC 0012 — Close the hosting core: credentials, tenant permissions and extraction

> `risk: high` — this is the only A.SPEC in the hosting plan that changes the kernel contract and
> moves code that is already proven against a real machine. It has three contracts (credentials,
> tenant permissions, extraction), no migration and no endpoint. Mode: `full`. A human approver is
> required because the permission change is systemic: a mistake there does not fail a screen, it
> grants a tenant access it should not have.
>
> This document **absorbs and replaces** the previous `0012-database-password-out-of-argv.md`.
> The credential part is kept verbatim where it was already precise; the tenant-permission and
> extraction parts are the former planned 0013 and 0014.

## WHY

The `hosting` plugin cannot be built on top of three debts that already exist in the repository.
Each one is small on its own and blocking on its own. Together they are the foundation the plugin
needs before a single endpoint exists.

### 1. The database password travels in the argv

Every database call in `scripts/reset_wp_password.py` is built like this:

```
docker exec {db} mariadb -u{usuario} -p{password} {base} -e "..."
```

That string travels to the laptop, where a shell splits it into words, and the words become the
`argv` of a `docker exec` process. Three consequences, all real:

**1. The password is visible in `ps`.** Any user on the laptop can read the `argv` of another
user's process. During every list, every prefix discovery and every `UPDATE`, the database password
sits there in plain text. The same happens inside the container: `mariadb -p...` carries it in its
own `argv` for the life of the query.

**2. The password is not escaped.** The hash is escaped (`para_sql` turns `$` into `\$`) because
the remote shell would expand it. The database password gets no such treatment. A password
containing `$`, a space, or a quote is split or expanded by the shell before it reaches `mariadb`:
the command fails, or it runs with a different password than the one configured. Database passwords
with symbols are common, not exotic.

**3. The prefix is not validated, and a comment says it is.** The comment above `RE_SQL_SEGURO`
claims that the table prefix is validated. `main` validates only the login. `prefijo` — which comes
from `SHOW TABLES`, that is, from the database — is interpolated raw into `SELECT` and `UPDATE`. A
table with a crafted name ends up inside a statement executed with the Docker socket's privileges.

**Verified against the real DB container (`spanel-demowp-db`), 2026-10-22:** it already exposes
`MARIADB_USER` and `MARIADB_PASSWORD` in its own environment. The password is available where it is
needed without ever being written into a command.

### 2. A new tenant does not inherit the plugin's permissions

Tenant separation in `systutor-core` is already correct: `get_current_tenant_context` builds the
`TenantContext` from the authenticated user, `require_permission` checks against it, and
`list_user_permissions` filters by `User.tenant_id` and `Role.tenant_id`. The plugin must use
those, not invent its own.

The gap is **how a newly created tenant gets the permissions declared by installed plugins**:

- `vendor/systutor-core/plugins/tenant/backend/service.py` hardcodes `MAIL_PERMISSIONS` and gives a
  new tenant's `tenant_admin` role only those mail permissions plus
  `core.plugin.runtime.read`.
- `vendor/systutor-core/src/systutor/api/seed.py` (`sync_permissions`) grants **all** plugin
  permissions only to the seed tenant's `admin` role, on every boot.

So a tenant created from the panel would not see `hosting` at all. The obvious shortcut — adding
`hosting.*` to the hardcoded list in the tenant plugin — creates a second source of truth and does
not scale to the next plugin. It also invites the opposite mistake: granting every plugin
permission to every tenant admin would hand `tenant.tenants.manage` to each tenant, which is
exactly the cross-tenant power the core is designed to prevent.

### 3. The proven logic lives where the plugin cannot reach it

`scripts/probe_docker.py` and `scripts/reset_wp_password.py` are the only code that has been
verified against the real Docker and the real database. That is the value they carry.

But `Dockerfile` copies `vendor/systutor-core/plugins/` to `/app/plugins`; it does **not** copy
`scripts/`. A plugin that imports `scripts.probe_docker` works on a developer machine and fails in
production. Copying the functions into the plugin creates two sources of truth for detection, for
the prefix and for the hash, and the live suite would only cover one of them.

The logic has to move into the plugin namespace. The scripts stay as thin, tested wrappers.

## WHAT

**The foundation of `hosting` is true: no database secret appears in any argv, a tenant's admin
role receives exactly the permissions its plugins declare for tenants, and the proven Docker and
WordPress logic lives in `plugins/hosting/backend/` with the scripts delegating to it.**

Falsifiable today, and today **fails** on all three:

- the database password appears in every `mariadb` argv;
- a tenant created by the tenant plugin receives no `hosting.*` permission;
- the proven logic exists only in `scripts/`, which the production image does not contain.

## SCOPE

### 1. Credentials

1. One helper builds every database command, so there is a single place where credentials are
   handled instead of four:
   - the client runs inside the DB container with `MYSQL_PWD` taken from that container's
     `MARIADB_PASSWORD`, and `-u` from its `MARIADB_USER`; neither value is interpolated on the
     host;
   - fallback when the container does not expose those variables: a `--defaults-extra-file`
     written inside the container over stdin with mode `600`, used and removed in the same
     command;
   - `RE_SQL_SEGURO` is applied to `prefijo` as well as to the login, right after discovery;
   - the four call sites (`prefijo_de_tablas`, `filas_de_usuarios`, the `UPDATE`, the read-back)
     use the helper.
2. `scripts/tests/test_reset_wp_password_live.py`: add the live checks listed in VERIFICATION.

### 2. Tenant permissions

1. `vendor/systutor-core/src/systutor/contracts/plugins.py`:
   - add `tenant_admin_permissions: list[str] = Field(default_factory=list)`;
   - it is a **subset** of `permissions`;
   - no duplicates, same plugin namespace, same permission pattern;
   - an empty list is valid: a plugin may declare permissions that only a platform administrator
     should receive.
2. `vendor/systutor-core/src/systutor/kernel/plugins/persistent.py` (or the smallest core module
   that already owns manifest synchronisation):
   - persist the declared subset so the tenant-creation path can read it without re-parsing files;
   - keep the existing `permissions_json` semantics unchanged.
3. One core function syncs a tenant's `tenant_admin` role from the installed plugins:
   - grants exactly the union of `tenant_admin_permissions` plus the kernel base
     `core.plugin.runtime.read`, without which the shell never loads plugin navigation;
   - is idempotent and safe to call on every boot;
   - never grants a permission that is not declared by a plugin.
4. `vendor/systutor-core/plugins/tenant/backend/service.py`:
   - `MAIL_PERMISSIONS` stops being the source of truth for `tenant_admin`;
   - tenant creation and tenant-user creation call the core sync function;
   - the tenant plugin itself declares no `tenant_admin_permissions`, because tenant management is
     a platform capability.
5. `vendor/systutor-core/plugins/mail/plugin.json`:
   - declares the mail permissions a tenant administrator should have:
     `mail.accounts.read`, `mail.accounts.create`, `mail.account.password`;
   - does **not** declare `mail.accounts.all`, which is the cross-domain permission.
6. New tenant, existing tenant and seed admin are all covered by tests. The seed admin keeps
   receiving every declared plugin permission.

### 3. Extraction

1. New package `vendor/systutor-core/plugins/hosting/backend/` containing, at minimum:
   - `transport.py`: `load_env_file`, `build_docker_argv`, `run` (the SSH and local transport);
   - `discovery.py`: `Candidato`, `parse_labels`, `detectar_capa1`, `detectar_capa2`,
     `detectar_todos`, `es_operable`, `es_prefiltrado`;
   - `database.py`: the credential-safe command builder and the prefix/user queries;
   - `passwords.py`: hash validation, `para_sql`, `PHP_HASH_AUTOCOMPROBADO`, `hash_de_password`;
   - `reset.py`: target resolution by ID/login, `UPDATE` by ID and read-back.
   This A.SPEC deliberately does **not** add `plugin.json`. The package is importable but is not a
   discoverable plugin until A.SPEC 0013.
2. `scripts/probe_docker.py` and `scripts/reset_wp_password.py` become thin wrappers:
   - add `vendor/systutor-core` to `sys.path` (the repo layout) and import the package;
   - keep argparse, help text, output format and exit codes byte-for-byte compatible;
   - contain no copy of the moved logic.
3. The existing tests keep working:
   - `scripts/tests/test_probe_docker.py` imports the new module and keeps its 12 cases plus label
     tests;
   - `scripts/tests/test_reset_wp_password_live.py` keeps running the CLI as a subprocess, so the
     wrapper is exercised end to end.

## OUT OF SCOPE

- **No `plugin.json`, no manifest, no router, no frontend and no `hosting_sites` table.** Those are
  A.SPEC 0013.
- **No site creation, start/stop, delete or logs.** Those are A.SPEC 0014.
- **No offline unit tests for the parts that touch the machine.** The user's rule stands: a fixture
  cannot prove that `ps` is clean or that a real MariaDB accepts the command. Pure detection tests
  stay offline because they are pure functions.
- **No change to `--password` on the command line.** It is also visible in `ps`, but only when the
  operator chooses to pass it; the database password leaks on every run whether the operator thinks
  about it or not.
- **No change to the temporary env-file with the DB password.** It is a different secret at a
  different moment; A.SPEC 0011 keeps writing it for `wordpress:cli`.
- **No new dependency, no driver, no `pymysql`:** still the `mariadb` client inside the container.
- **No creation of the A.SPEC 0009 lab.** The hostile-password and hostile-prefix proofs depend on
  it and are named as such below. This A.SPEC does not pretend they ran.
- **No kernel business logic.** The extracted package lives under the plugin namespace; `src/
  systutor` does not learn about Docker or WordPress.
- **No change to the detection decisions of A.SPEC 0007.** Moving code is not an invitation to
  revisit the 12 cases.

## CONTRACT

**Preconditions**

- The DB container is running.
- The DB container exposes `MARIADB_USER` and `MARIADB_PASSWORD`, **or** the fallback is used.
- The plugin manifests load successfully.
- The scripts are executed from the repository root.

**Postconditions — credentials**

- No `argv` on the laptop contains the database password, in any query this script runs.
- No `argv` inside the DB container contains it either.
- A database password containing `$`, spaces, quotes or backslashes works, because it is never
  parsed by a shell.
- The prefix is validated with `RE_SQL_SEGURO`. A prefix outside `[A-Za-z0-9_]` is rejected before
  any statement is built, with a message naming the prefix and saying it was refused, not escaped.
- The login keeps being validated exactly as today.
- Every query result is identical to today's; this changes the channel, not the SQL.
- Errors still redact the password.

**Postconditions — tenant permissions**

- A newly created tenant's `tenant_admin` role receives exactly the union of the
  `tenant_admin_permissions` declared by installed plugins, plus the kernel base
  `core.plugin.runtime.read`. It still does not receive `tenant.tenants.manage` or
  `mail.accounts.all`.
- It does not receive `tenant.tenants.manage` unless the tenant plugin explicitly declares it,
  which it does not.
- The seed tenant's `admin` role still receives every declared plugin permission.
- An existing tenant receives new declared permissions on the next boot, idempotently.
- A manifest whose `tenant_admin_permissions` is not a subset of `permissions` fails validation
  with a message naming the offending permission.

**Postconditions — extraction**

- `python3 -c "from plugins.hosting.backend import discovery, transport, database, passwords,
  reset"` succeeds with `vendor/systutor-core` on `sys.path`.
- Both CLIs keep the same flags, output and exit codes.
- `scripts/probe_docker.py` and `scripts/reset_wp_password.py` contain no duplicated detection,
  SQL or hash implementation.
- The existing live suite still passes unchanged.

**New falsifiable truths**

1. During a real reset, `ps` on the laptop and `docker top` on the DB container never show the
   database password.
2. A tenant created from the panel has `hosting.sites.read` only if the hosting manifest declares
   it as a tenant-admin permission, and has no `tenant.tenants.manage` unless declared.
3. The Docker image can import the proven logic from `plugins/hosting/backend/`, because the
   logic is there and not in `scripts/`.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      No database secret appears in any argv: not on the host, not inside the container.
    proof: >
      test_la_password_no_aparece_en_ps, which samples `ps -eww -o args=` on the laptop and
      `docker top` on the DB container while a real reset runs, and asserts the password from the
      container's environment never appears in either.

  - id: I2
    statement: >
      The database password is never interpolated into a string that a shell parses. There is one
      helper that builds every DB command, and it does not take the password as text.
    proof: >
      test_ningun_comando_lleva_la_password, a static check that no f-string in the file places a
      password variable inside a command, plus the live I1 check.

  - id: I3
    statement: >
      The prefix is validated with the same rule as the login, before any SQL is built.
    proof: >
      test_el_prefijo_se_valida, a static check that `prefijo` passes through `RE_SQL_SEGURO`, and
      a pure test with a prefix that contains `;` and a quote.

  - id: I4
    statement: >
      Query semantics do not change. Same statements, same rows, same single-row UPDATE by ID.
    proof: >
      The existing A.SPEC 0011 live suite still passes unchanged (11 tests), which exercises the
      full reset against the real database. A.SPEC 0008's listing tests stay green too.

  - id: I5
    statement: >
      If the container does not expose MARIADB_USER/MARIADB_PASSWORD, the script still works and
      still does not put the password in an argv.
    proof: >
      The fallback path is exercised in the lab (A.SPEC 0009) by a DB container created without
      those variables; the password then travels only through the defaults-extra-file over stdin.
      Until 0009 exists this invariant is declared NOT PROVEN, not assumed.

  - id: I6
    statement: >
      Every error message keeps redacting the password.
    proof: >
      test_un_error_no_imprime_la_password, a live failure test asserting the DB password is absent
      from stderr.

  - id: I7
    statement: >
      Tenant administrators receive only the permissions their installed plugins declare for
      tenants plus the kernel base `core.plugin.runtime.read`, never the full plugin permission
      set.
    proof: >
      test_tenant_admin_recibe_solo_los_declarados creates a tenant through the real tenant
      plugin and asserts the exact permission set, including the absence of
      `tenant.tenants.manage` and `mail.accounts.all`.

  - id: I8
    statement: >
      The manifest rejects a `tenant_admin_permissions` entry that is not part of `permissions`.
    proof: >
      test_manifest_rechaza_permiso_de_tenant_fuera_de_permissions, a pure contract test.

  - id: I9
    statement: >
      The seed tenant's admin role still receives every declared plugin permission, and existing
      tenants are backfilled without duplicates.
    proof: >
      test_seed_admin_conserva_todos_los_permisos and test_backfill_es_idempotente, both against
      the real database.

  - id: I10
    statement: >
      The extracted package is importable from the plugin namespace and contains no business logic
      in `systutor.*`.
    proof: >
      test_el_paquete_es_importable and a static check that no file under
      `vendor/systutor-core/src/systutor` mentions Docker, WordPress or MariaDB.

  - id: I11
    statement: >
      The CLIs are wrappers, not forks. They keep the same flags, output and exit codes.
    proof: >
      test_los_scripts_no_duplican_logica (static: the moved function names do not appear as
      definitions in `scripts/`) plus the existing live suite, which invokes the CLI.

  - id: I12
    statement: >
      Turning the feature off is possible without losing data: the extracted package has no
      side effects at import, and no migration exists in this A.SPEC.
    proof: >
      test_import_sin_efectos_secundarios, asserting that importing the package opens no
      subprocess and touches no file.
```

## VERIFICATION

**Gates**

```bash
ruff check scripts/ vendor/systutor-core
python3 -m pyright scripts/
(cd vendor/systutor-core && python3 -m pyright plugins/hosting/backend)
PYTHONPATH=vendor/systutor-core/src python3 -m pyright \
  vendor/systutor-core/src/systutor/contracts/plugins.py \
  vendor/systutor-core/src/systutor/kernel/permissions/service.py \
  vendor/systutor-core/src/systutor/kernel/plugins/persistent.py \
  vendor/systutor-core/src/systutor/api/seed.py \
  vendor/systutor-core/plugins/tenant/backend/service.py \
  vendor/systutor-core/plugins/tenant/backend/router.py \
  vendor/systutor-core/tests/test_hosting_core_foundation.py
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_probe_docker.py -q
python3 -m pytest vendor/systutor-core/tests -q
```

The full-tree `pyright vendor/systutor-core` has eight pre-existing errors in
files this A.SPEC does not touch (`plugins/mail/backend/provider.py` and
`tests/test_permission_sync.py`). The gate above checks the files that changed
and the new package instead of pretending the whole tree was green before.

**Live tests to add**

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_la_password_no_aparece_en_ps` | I1 | the password is readable while a query runs |
| `test_la_password_no_aparece_dentro_del_contenedor` | I1 | the leak moved into the container |
| `test_ningun_comando_lleva_la_password` | I2 | a second code path still interpolates it |
| `test_el_prefijo_se_valida` | I3 | the comment promises a check that does not exist |
| `test_un_error_no_imprime_la_password` | I6 | a failure path leaks the secret |
| `test_el_reset_sigue_funcionando` | I4 | closing the leak broke the tool |
| `test_tenant_admin_recibe_solo_los_declarados` | I7 | a new tenant gets more than it should |
| `test_seed_admin_conserva_todos_los_permisos` | I9 | the fix starved the platform administrator |
| `test_backfill_es_idempotente` | I9 | booting twice duplicates or removes permissions |
| `test_el_paquete_es_importable` | I10 | the package only works from the repo layout |
| `test_los_scripts_no_duplican_logica` | I11 | the move became a copy |

**What cannot be proven here, and where it is proven instead**

- **A password with shell metacharacters** cannot be tested by changing the real DB password. It
  is proven in the lab (A.SPEC 0009), with a DB container created with a hostile password. A pure
  test proves the constructed command never contains it; that is not the same as MariaDB accepting
  it, and this document says so.
- **A hostile prefix** cannot be created without renaming the real tables. It is proven in the lab
  (A.SPEC 0009), with a site installed under a prefix that the validator must reject. The pure
  validator test is additional, not a substitute.
- **The production image import** is proven by building the image and importing the package inside
  it, not by assuming that `COPY plugins/` is enough.

## ROLLBACK

`git revert` of the single commit. There is no migration and no state on the server:

- the credential helper only changes how a command is assembled;
- the manifest field defaults to an empty list, so reverting the core leaves existing manifests
  valid;
- reverting the tenant plugin restores `MAIL_PERMISSIONS`; existing role assignments are not
  deleted, so no tenant loses a permission it already had;
- reverting the extraction restores the scripts as the only copy. No container, volume or database
  row is created by this A.SPEC.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0012-hosting-core-foundation.md
    - scripts/probe_docker.py
    - scripts/reset_wp_password.py
    - scripts/tests/test_probe_docker.py
    - scripts/tests/test_reset_wp_password_live.py
    - vendor/systutor-core/src/systutor/contracts/plugins.py
    - vendor/systutor-core/src/systutor/kernel/plugins/persistent.py
    - vendor/systutor-core/src/systutor/kernel/permissions/service.py
    - vendor/systutor-core/src/systutor/api/seed.py
    - vendor/systutor-core/plugins/tenant/backend/service.py
    - vendor/systutor-core/plugins/tenant/backend/router.py
    - vendor/systutor-core/plugins/mail/plugin.json
    - vendor/systutor-core/plugins/hosting/backend/**   # new package, no plugin.json yet
    - vendor/systutor-core/tests/**                     # contract and permission tests
  prohibited:
    - vendor/systutor-core/plugins/hosting/plugin.json  # belongs to A.SPEC 0013
    - vendor/systutor-core/plugins/hosting/migrations/** # belongs to A.SPEC 0013
    - vendor/systutor-core/plugins/hosting/frontend/**   # belongs to A.SPEC 0013
    - scripts/lab_wordpress.sh                           # belongs to A.SPEC 0009
    - docker-compose.yml
    - .env
    - .env.docker
    - apps/web/**                                        # no UI in this A.SPEC
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - every database query in the reset/list script (same SQL, different channel)
    - the permission set of a newly created tenant
    - the location of the detection and reset logic
  indirect:
    - plugin install/enable synchronisation, which now persists one more manifest field
    - every tenant created from now on
    - the seed admin's permission set (must not shrink)
    - the production image import path
  must_not_affect:
    - surface: reset_de_contrasena
      surfaces: [main camino reset, hash_de_password, UPDATE por ID]
      invariant: I4
    - surface: deteccion_wordpress
      surfaces: [detectar_capa1, detectar_capa2, los 12 casos]
      invariant: I11
    - surface: permisos_del_admin_semilla
      surfaces: [sync_permissions, rol admin]
      invariant: I9
    - surface: separacion_tenant
      surfaces: [TenantContext, require_permission, list_user_permissions]
      invariant: I7
    - surface: credenciales
      surfaces: [pedir, redactar, build_docker_argv, db_command]
      invariant: I1, I2, I6
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0009   # hostile password and hostile prefix proofs; not created here
  must_compose_with:
    - 0013   # the plugin manifest declares tenant_admin_permissions
    - 0014   # provisioning consumes the extracted core and the tenant binding
  systemic_invariants:
    - There is one prefix discovery. The extraction must not introduce a second.
    - There is one transport builder. The plugin must not build a second SSH argv.
    - Tenant identity always comes from TenantContext; never from a request field.
  composition_checks:
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
    - python3 -m pytest vendor/systutor-core/tests -q
    - docker build -t spanel-hosting-core-check . && docker run --rm spanel-hosting-core-check python -c "from plugins.hosting.backend import discovery"
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/
    - vendor/systutor-core/src/systutor/contracts/plugins.py
    - vendor/systutor-core/src/systutor/kernel/permissions/service.py
```

The extracted modules are split by failure domain, not by layer: transport, discovery, database,
passwords and reset. `reset.py` may call `database.py` and `passwords.py`; no module imports
`scripts.*`.

## Traceability

- **Requirement**: the hosting plugin needs a safe, tenant-aware, production-reachable core
- **A.SPEC**: this document
- **Code**: `scripts/{probe_docker,reset_wp_password}.py`, `vendor/systutor-core/plugins/hosting/backend/**`,
  `vendor/systutor-core/src/systutor/contracts/plugins.py`, `vendor/systutor-core/plugins/tenant/backend/service.py`
- **Migration**: none
- **Test**: `scripts/tests/test_reset_wp_password_live.py`, `scripts/tests/test_probe_docker.py`,
  `vendor/systutor-core/tests/**`
- **Commit**: `TBD`
- **Deployment**: rebuild the image; the extracted package is copied by the existing `COPY plugins/`
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

**I5 stays unchecked until A.SPEC 0009 exists.** The hostile-password and hostile-prefix proofs are
the only part of this contract that cannot run on the demo site, and this document says so instead
of claiming coverage it does not have.
