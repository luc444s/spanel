# A.SPEC 0008 — List the users of a WordPress without guessing the login

> `risk: low` — one read-only code path in an existing script, no migration, no schema, no API
> contract. The rollback is a `git checkout` of one file. Mode: `extreme-poverty`.

## WHY

`scripts/reset_wp_password.py` can only change the password of a login you **already know**. The
default is `admin`, and if that is wrong the script says so and stops:

```python
antes = pedir(env, ... f"SELECT user_pass FROM {prefijo}users WHERE user_login='{args.usuario}'" ...)
if not antes:
    raise Fallo(f"no existe el usuario {args.usuario!r} en {prefijo}users")
```

So the script can answer "I could not find that user" and nothing else. It cannot answer "who are
the users". The only way to find out today is to write a throwaway script — which is what happened
on 2026-09-28, and that code did not survive the terminal.

`admin` is exactly the kind of assumption that already broke this script once, in the table
prefix. Guessing an identifier and writing to it is how you update a row that was never the right
one.

**Verified against the real machine, 2026-09-28.** `spanel-demowp-wp` (the only site left running
after the previous session) has **exactly one** user:

```
1	admin	admin@example.com	0
```

So on that site the guess happened to be right. That is luck, not a design.

### The part that is not where everyone looks

Roles are **not** in `wp_users`. `wp_users` has ID, login, email, registration date, status and
nothing else. WordPress stores roles in `{prefijo}usermeta`, on the row whose `meta_key` is
`{prefijo}capabilities`, with a PHP-serialized value.

Consequences that shape this A.SPEC:

- "List users with their role" needs a JOIN against the usermeta table, not a wider SELECT.
- **The prefix now appears in two places** (the users table and the meta_key). Using one of them
  twice is the same class of bug as the `wp` vs `wp_` one, and it is a real risk.
- A user with no capabilities row is legal and shows up in neither column. It must render as
  "no role", not as an error and not as an empty join that drops the user.

### The honest limit of testability

Split by whether it needs the machine:

| Part | Testable without Docker | Why |
|---|---|---|
| Parsing a `SELECT` into rows | Yes | string → list |
| Choosing which columns to print | Yes | logic |
| Knowing the base, the prefix, whether it is up | **No** | `docker inspect` and a live base |
| The query not breaking on a weird table | **No** | behaviour of MariaDB |

A fixture-based suite gives real confidence in the first half and **zero** in the second, and the
second is where both real bugs in this script lived. Fixtures in this A.SPEC are therefore copied
from real server output, and the live run against `spanel-demowp-wp` is the other half of the
proof.

## WHAT

**`reset_wp_password.py` can list every user of a site, with their role, reading only and
without guessing anything.**

Falsable today, and today **fails**: there is no way to enumerate users.

## SCOPE

1. `scripts/reset_wp_password.py`:
   - a `--listar` flag that prints the users and exits without writing anything;
   - one pure function that turns the raw `mariadb -N -B` output into rows;
   - one pure function that decides what to print, so the format is testable without a daemon;
   - the role read from usermeta, with a missing capabilities row rendering as "no role";
   - `--usuario` stops being a default of `admin` in the listing path — listing does not take a
     user at all.
2. `scripts/tests/test_reset_wp_password.py`: new, offline.
3. This document.

## OUT OF SCOPE

- **The listing never prints `user_pass` or any hash.** Not truncated, not masked — absent. The
  script already has a `redactar` helper; this is a different rule, it is about not querying the
  column at all.
- **No plugin `hosting`.** Same boundary as A.SPEC 0007: this is CLI logic, proven without a
  frontend.
- **Nothing is created, changed or deleted.** `--listar` is a read. The temporary test user from
  VERIFICATION is a manual step, not a code path.
- **No `SHOW TABLES LIKE` and no prefix guessing.** The prefix keeps coming from the existing
  discovery, which is already proven against a real prefix mismatch.
- **`vendor/systutor-core/**` is not touched.**
- **No `wp-cli` for the listing.** The `mariadb` client inside the DB container is enough, and it
  avoids a container start per call.

## CONTRACT

**Preconditions**

- The WordPress container and its DB are running. Same rule as the reset: the site is alive and
  must stay that way.
- The site is installed (its users table exists). If it is not, the listing says so and exits 1.

**Postconditions**

- `--listar` exits 0, writes nothing, and prints one line per user.
- Each line carries ID, login, email, status and role. No password column is selected.
- A user with no capabilities row appears, with an empty role. It is not dropped and not an error.
- The prefix used for the users table and the prefix used for the capabilities meta_key come from
  the **same** discovered value, in the same run.
- Without `--listar`, the script behaves byte to byte as today. `--usuario` still defaults to
  `admin` on the reset path.

**New falsifiable truth**: the count printed by `--listar` equals the count of rows in the users
table, and the reset path is unchanged.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      The listing is read-only. It runs no command that creates, updates or deletes, and it prints
      no `user_pass`.
    proof: >
      test_listado_no_selecciona_user_pass, asserting the SELECT does not name the column, plus
      test_listado_solo_comandos_de_lectura walking the file's commands.

  - id: I2
    statement: >
      The prefix for the users table and the prefix for the capabilities meta_key are the same
      discovered value, never a literal `wp_` and never two independent guesses.
    proof: >
      test_prefijo_unico_para_usuarios_y_meta, with a `wp2_`-prefixed fixture; and a test asserting
      the built SQL contains `wp2_usermeta` and `wp2_capabilities` together.

  - id: I3
    statement: >
      A user with no capabilities row is listed, with an empty role. The LEFT JOIN semantics are
      fixed by a test, not by hope.
    proof: test_usuario_sin_capabilities_aparece_con_rol_vacio

  - id: I4
    statement: >
      Fixtures are real output, copied from the server, not invented. The demowp fixture is the
      verbatim `SHOW TABLES` and the verbatim user row captured on 2026-09-28.
    proof: >
      The fixture string appears in this document, byte for byte. If WordPress later returns another
      column the test fails and says so, instead of validating a fantasy.

  - id: I5
    statement: >
      Offline tests prove the parsing and the formatting, and are not claimed to prove that the
      query runs. The live run against `spanel-demowp-wp` is the other half of the proof.
    proof: >
      VERIFICATION separates both explicitly. No test in this A.SPEC is allowed to assert anything
      about MariaDB behaviour.

  - id: I6
    statement: >
      The reset path is unchanged. Same default `admin`, same failure message, same single-row
      UPDATE, same `wp_hash_password`.
    proof: test_reset_no_cambio_con_listar, and the manual reset re-run in VERIFICATION.

  - id: I7
    statement: >
      The password of the DB never reaches the argv, and never reaches the listing output.
    proof: >
      The existing redaction covers the command; test_listado_no_imprime_passwordes covers the
      output.
```

## VERIFICATION

Gates:

```bash
ruff check scripts/
python3 -m pyright scripts/
python3 -m pytest scripts/tests -q
```

**Offline tests** (no Docker, no SSH, no `.env`)

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_parsea_salida_real_de_demowp` | I4 | the parser does not handle real output |
| `test_prefijo_windows_unico` | I2 | the metakey keeps a literal `wp_` |
| `test_prefijo_no_literal_en_la_consulta` | I2 | a `wp_` hardcoded reaches the SQL |
| `test_usuario_sin_capabilities_aparece` | I3 | a user with no role is dropped by the join |
| `test_rol_serializado_se_entiende` | I1 | a raw PHP-serialized value is printed raw |
| `test_listado_no_selecciona_user_pass` | I1 | the password column is queried |
| `test_listado_solo_comandos_de_lectura` | I1 | the listing starts writing |
| `test_login_con_apostrofe_no_rompe` | I1 | quoting in the output path |
| `test_salida_vacia_no_lanza` | I5 | an empty table is an exception |
| `test_reset_no_cambio_con_listar` | I6 | adding the flag moved the default path |

**Live run, the half that cannot be a fixture** (manual, on `spanel-demowp-wp`)

1. `--dry-run` and `--listar` on the site as it is today. Expected: one line, `admin`.
2. Create a user with the **lowest role that exercises the format** — a subscriber, never an
   administrator. A leftover admin is a real backdoor on a real site.
3. `--listar` again. Expected: two lines, the new one with its role.
4. Delete the test user.
5. `--listar` a third time. Expected: one line again. **This is the step that proves the tool left
   no trace**, and it is why step 4 is not "I will remember to do it later".
6. Re-run the reset on `admin` to confirm I6 against the real server.

Steps 2 and 4 write to a real site's user table. They are reversible, and they are the only way to
test against data that did not exist before. The decision to run them is the user's.

**What this A.SPEC does NOT prove**: that it works against another WordPress version, another
prefix in a different shape, or another MariaDB. That is what A.SPEC 0009 is for, and A.SPEC 0009
is allowed to fix anything it finds here.

## ROLLBACK

`git checkout scripts/reset_wp_password.py` and delete `scripts/tests/test_reset_wp_password.py`.
No migration, no state on the server, nothing to undo on the Docker side: the listing never wrote.
The manual test user is removed in VERIFICATION step 4, before the commit.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/reset_wp_password.py
    - scripts/tests/test_reset_wp_password.py
    - aspecs/0008-list-wordpress-users.md
  prohibited:
    - scripts/probe_docker.py                  # the detection is already proven by A.SPEC 0007
    - vendor/systutor-core/**                  # the kernel does not know about Docker
    - vendor/systutor-shell/**                 # the UI is not touched
    - docker-compose.yml                       # no new services
    - .env                                     # credentials
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - scripts/reset_wp_password.py (one flag, two pure functions, the listing branch of main)
    - scripts/tests/test_reset_wp_password.py (new, ~10 tests)
  indirect:
    - One more read query against the DB of a site, at the moment the listing runs. No locks, no
      writes, no downtime.
  must_not_affect:
    - surface: reset_de_contrasena
      surfaces: [main camino reset, hash_de_password, para_sql]
      invariant: I6
    - surface: solo_lectura
      surfaces: [--listar completo]
      invariant: I1
    - surface: descubrimiento_de_prefijo
      surfaces: [prefijo_de_tablas]
      invariant: I2
    - surface: credenciales
      surfaces: [build_docker_argv, run, redactar]
      invariant: I7
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - A.SPEC 0009   # multi-site, other prefix, other roles, other MariaDB
  systemic_invariants:
    - The detection of A.SPEC 0007 decides which containers this script may be pointed at. If the
      detection changes, the set of sites this can list changes with it.
    - The same prefix-discovery function serves both the reset and the listing. They cannot
      disagree, because there is only one.
  composition_checks:
    - python3 -m pytest scripts/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - scripts/reset_wp_password.py
```

`reset_wp_password.py` is 391 lines today and goes to roughly 470, still under the review
threshold. The two new functions are pure and stay inside the file: extracting them to a module
would be a second thing to keep in sync for no gain at this size.

## Traceability

- **Requirement**: be able to see who the users of a WordPress are, instead of assuming `admin`
- **A.SPEC**: this document
- **Code**: `scripts/reset_wp_password.py` (`--listar`, the parse function, the role reader)
- **Migration**: none
- **Test**: `scripts/tests/test_reset_wp_password.py` (new)
- **Commit**: `TBD`
- **Deployment**: none. Does not enter the Docker image.

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

**The checkboxes stay unchecked on purpose.** This document is written before the code, and I5
says out loud that a green offline suite is half the proof. They get ticked when the live run of
VERIFICATION steps 1 to 6 has happened, not before.
