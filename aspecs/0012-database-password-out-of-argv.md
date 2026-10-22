# A.SPEC 0012 — Keep the database password out of every argv

> `risk: normal` — touches every query the script builds, but not the queries' meaning. No
> migration, no schema change. The rollback is a `git checkout` of one file. Mode: `extreme-poverty`.

## WHY

Every database call in `reset_wp_password.py` is built like this:

```
docker exec {db} mariadb -u{usuario} -p{password} {base} -e "..."
```

That string travels to the laptop, where a shell splits it into words, and the words become the
`argv` of a `docker exec` process. Three consequences, all real:

**1. The password is visible in `ps`.** Any user on the laptop can read the `argv` of another user's
process. During every list, every prefix discovery and every `UPDATE`, the database password sits
there in plain text. The same happens inside the container: `mariadb -p...` carries it in its own
`argv` for the life of the query.

This directly contradicts the rule the project already follows everywhere else:
`build_docker_argv` puts the SSH password in `SSHPASS`, never in the command, and says so in a
comment. The database password gets the opposite treatment.

**2. The password is not escaped.** The hash is escaped (`para_sql` turns `$` into `\$`) because the
remote shell would expand it. The database password gets no such treatment. A password containing
`$`, a space, or a quote is split or expanded by the shell before it reaches `mariadb`: the command
fails, or it runs with a different password than the one configured. Database passwords with symbols
are common, not exotic.

**3. The prefix is not validated, and a comment says it is.** The comment above `RE_SQL_SEGURO`
claims:

> "Lo que sí o sí va dentro del SQL: el usuario **y el prefijo de tablas**. Solo letras, dígitos y
> guion bajo. Cualquier otra cosa se rechaza."

`main` validates only the login. `prefijo` — which comes from `SHOW TABLES`, that is, from the
database — is interpolated raw into `SELECT` and `UPDATE`. A table with a crafted name ends up inside
a statement executed with the Docker socket's privileges.

**Verified against the real DB container (`spanel-demowp-db`), 2026-10-22:** it already exposes
`MARIADB_USER` and `MARIADB_PASSWORD` in its own environment. The password is available where it is
needed without ever being written into a command.

## WHAT

**No database command contains the password in its text. The client reads it from the container's
own environment, and the table prefix is validated before it reaches any SQL.**

Falsable today, and today **fails**: the password appears in the `argv` of every query.

## SCOPE

1. `scripts/reset_wp_password.py`:
   - one helper builds every database command, so there is a single place where credentials are
     handled instead of four;
   - the client runs inside the DB container with `MYSQL_PWD` taken from that container's
     `MARIADB_PASSWORD`, and `-u` from its `MARIADB_USER`; neither value is interpolated on the host;
   - fallback when the container does not expose those variables: a `--defaults-extra-file` written
     inside the container over stdin with mode 600, used and removed in the same command;
   - `RE_SQL_SEGURO` is applied to `prefijo` as well as to the login, right after discovery;
   - the four call sites (`prefijo_de_tablas`, `filas_de_usuarios`, the `UPDATE`, the read-back) use
     the helper.
2. `scripts/tests/test_reset_wp_password_live.py`: add the live checks listed in VERIFICATION.
3. This document.

## OUT OF SCOPE

- **No offline unit tests**, same decision as A.SPEC 0011: the proofs touch the machine.
- **The `--password` flag on the command line is not changed here.** It is also visible in `ps`, but
  only when the operator chooses to pass it; the database password leaks on *every* run whether the
  operator thinks about it or not. The CLI flag deserves its own decision about ergonomics, and
  mixing it here would blur which leak this A.SPEC closed.
- **The temporary env-file with the DB password is not changed.** It is a different secret at a
  different moment (A.SPEC 0011 keeps writing it for `wordpress:cli`); it is `600`, owned by the SSH
  user, and removed in a `finally`. Improving its survival under SIGKILL is not part of this change.
- **No change to `probe_docker.py`.**
- **No change to the hosting plugin**, which does not exist yet.
- **No new dependency**, no driver, no `pymysql`: still the `mariadb` client inside the container.

## CONTRACT

**Preconditions**

- The DB container is running.
- The DB container exposes `MARIADB_USER` and `MARIADB_PASSWORD`, **or** the fallback is used.

**Postconditions**

- No `argv` on the laptop contains the database password, in any query this script runs.
- No `argv` inside the DB container contains it either.
- A database password containing `$`, spaces, quotes or backslashes works, because it is never
  parsed by a shell.
- The prefix is validated with `RE_SQL_SEGURO`. A prefix outside `[A-Za-z0-9_]` is rejected before any
  statement is built, with a message naming the prefix and saying it was refused, not escaped.
- The login keeps being validated exactly as today.
- Every query result is identical to today's; this changes the channel, not the SQL.
- Errors still redact the password (the existing `redactar` behavior).

**New falsifiable truth**: during a real reset, `ps` on the laptop and `ps` inside the DB container
never show the database password.

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
      the lab case in A.SPEC 0009 for a prefix that actually contains a hostile character.

  - id: I4
    statement: >
      Query semantics do not change. Same statements, same rows, same single-row UPDATE by ID.
    proof: >
      The existing A.SPEC 0011 live suite still passes unchanged (11 tests), which exercises the
      full reset against the real database.

  - id: I5
    statement: >
      If the container does not expose MARIADB_USER/MARIADB_PASSWORD, the script still works and
      still does not put the password in an argv.
    proof: >
      The fallback path is exercised in the lab (A.SPEC 0009) by a DB container created without
      those variables; the password then travels only through the defaults-extra-file over stdin.

  - id: I6
    statement: >
      Every error message keeps redacting the password.
    proof: >
      The existing `secretos=` arguments stay in place; a live failure test asserts the DB password
      is absent from stderr.
```

## VERIFICATION

**Gates**

```bash
ruff check scripts/
python3 -m pyright scripts/
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
```

**Live tests to add**

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_la_password_no_aparece_en_ps` | I1 | the password is readable while a query runs |
| `test_la_password_no_aparece_dentro_del_contenedor` | I1 | the leak moved into the container |
| `test_ningun_comando_lleva_la_password` | I2 | a second code path still interpolates it |
| `test_el_prefijo_se_valida` | I3 | the comment promises a check that does not exist |
| `test_un_error_no_imprime_la_password` | I6 | a failure path leaks the secret |
| `test_el_reset_sigue_funcionando` | I4 | closing the leak broke the tool |

**What cannot be proven on the demo site, and where it is proven instead**

- **A password with shell metacharacters** cannot be tested by changing the real DB password. It is
  proven in the lab (A.SPEC 0009), with a DB container created with a hostile password.
- **A hostile prefix** cannot be created without renaming the real tables. It is proven in the lab,
  with a site installed under a prefix that the validator must reject.

This A.SPEC says both things out loud instead of pretending the demo site covers them.

## ROLLBACK

`git checkout scripts/reset_wp_password.py` and `git checkout
scripts/tests/test_reset_wp_password_live.py`. No migration, no schema change, no state on the
server: the change only alters how a command is assembled.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/reset_wp_password.py
    - scripts/tests/test_reset_wp_password_live.py
    - aspecs/0012-database-password-out-of-argv.md
  prohibited:
    - scripts/probe_docker.py                  # its SSH secret already travels correctly
    - scripts/tests/test_probe_docker.py       # stays offline; it tests pure detection
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
    - scripts/reset_wp_password.py (one command helper, four call sites, one prefix check)
    - scripts/tests/test_reset_wp_password_live.py (six new live checks)
  indirect:
    - Every DB query now runs through `sh -c` inside the container to read its own environment.
      One more process per query; the SQL and the rows are unchanged.
  must_not_affect:
    - surface: semantica_del_sql
      surfaces: [filas_de_usuarios, UPDATE, read-back]
      invariant: I4
    - surface: validacion_del_login
      surfaces: [RE_SQL_SEGURO, main]
      invariant: I3 — se le agrega el prefijo, no se le quita al login
    - surface: mensajes_de_error
      surfaces: [redactar, secretos]
      invariant: I6
    - surface: transporte_ssh
      surfaces: [build_docker_argv, run]
      invariant: I1 — el secreto de SSH sigue en SSHPASS, sin tocar
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0011   # this removes the leak that 0011 deliberately left out of scope
  must_compose_with:
    - A.SPEC 0009   # the hostile-password and hostile-prefix proofs need the lab
  systemic_invariants:
    - The prefix is discovered once and used in one place. Adding validation must not introduce a
      second discovery.
    - A.SPEC 0011's live suite is the regression test for this change: it must keep passing without
      edits beyond the new assertions.
  composition_checks:
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
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

`reset_wp_password.py` is ~430 lines after A.SPEC 0011 and goes to ~450: the helper replaces four
repetitions, so the file grows less than the feature suggests.

## Traceability

- **Requirement**: the database password must not be readable by other users on the machine, must
  survive special characters, and the table prefix must be validated before reaching SQL
- **A.SPEC**: this document
- **Code**: `scripts/reset_wp_password.py` (the DB command helper, `RE_SQL_SEGURO` on `prefijo`)
- **Migration**: none
- **Test**: `scripts/tests/test_reset_wp_password_live.py` (six new live checks)
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

**Unchecked on purpose.** I1 is the whole point, and it is only proven by watching `ps` while a real
query runs. The hostile-password and hostile-prefix cases stay unchecked until the lab of A.SPEC 0009
exists, and this document says so rather than claiming coverage it does not have.
