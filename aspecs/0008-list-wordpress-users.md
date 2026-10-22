# A.SPEC 0008 — List the users of a WordPress with their role

> `risk: low` — one read-only flag in an existing script, no migration, no schema change, no API
> contract. The rollback is a `git checkout` of one file. Mode: `extreme-poverty`.

## WHY

`reset_wp_password.py` can only reset a login the operator already knows. It defaults to `admin`, and
if that is wrong it says so and stops:

```python
if not filas:
    raise Fallo(f"no existe el usuario {login!r} en {prefijo}users")
```

So it can answer "I could not find that user" and nothing else. It cannot answer "who are the
users". Finding out today means writing a throwaway query in a terminal — which is what happened on
2026-09-28, and that code did not survive the session.

`admin` is the third version of the same mistake. The first two were the table prefix and the hash
format, and both were found by breaking against a real site, not by reading.

**A.SPEC 0011 made this worse in a useful way.** Now that a duplicated login makes the script stop
and ask for `--id`, the operator is told to use a number the tool never shows. The listing is what
gives that number a source.

### The part that is not where everyone looks

Roles are **not** in `wp_users`. WordPress stores them in `{prefijo}usermeta`, on the row whose
`meta_key` is `{prefijo}capabilities`, as a PHP-serialized value like:

```
a:1:{s:13:"administrator";b:1;}
```

Consequences that shape this A.SPEC:

- The listing needs a `LEFT JOIN`, not a wider `SELECT`.
- **The prefix appears in two places**: the users table and the `meta_key`. Using one of them twice,
  or hardcoding `wp_` in the second, is the prefix bug again.
- A user with no capabilities row is legal. It must appear with an empty role, not disappear.

## WHAT

**`reset_wp_password.py --listar` prints every user of a site with their ID, login, email, status
and role, without writing anything.**

Falsable today, and today **fails**: there is no way to enumerate users.

## SCOPE

1. `scripts/reset_wp_password.py`:
   - `--listar`, read-only, that prints one row per user;
   - the query is a `LEFT JOIN` to `{prefijo}usermeta` on `meta_key = '{prefijo}capabilities'`,
     built from the **same** prefix value used for `{prefijo}users`;
   - the serialized roles are turned into a comma-separated list; no capabilities row renders as `-`;
   - `--listar` is mutually exclusive with `--password`, `--usuario` and `--id`, and the rejection
     happens before reading the database;
   - the listing does not require the site volume: it only needs the DB, so it works on a site whose
     `/var/www/html` is a bind mount;
   - the docstring stops claiming to be "A.SPEC 0008" for the reset — a stale forward reference that
     now points at this listing and misleads anyone reading `--help`.
2. `scripts/tests/test_reset_wp_password_live.py`: the listing tests, against the real laptop.
3. This document.

## OUT OF SCOPE

- **No offline unit tests, by explicit decision of the user (2026-10-22).** The parser and the
  formatter are still pure functions, but their proof is the real output from the real database, not
  a literal fixture. A fixture cannot catch a prefix used twice or a column that WordPress renamed.
- **No writes.** `--listar` does not create, update or delete. The test users it inspects are created
  by the existing A.SPEC 0011 fixture, which already backs up and restores.
- **No `user_pass` column.** Not masked, not truncated: absent from the query.
- **No `wp-cli`.** The `mariadb` client in the DB container is enough and starts no container.
- **No JSON output.** A human reads this to pick an ID; adding a machine format is a different
  decision.
- **No change to the reset path.** Same hash, same `UPDATE` by ID, same checks.
- **No plugin `hosting`.** Same boundary as 0007 and 0011.

## CONTRACT

**Preconditions**

- The site and its DB are running, and the site is installed.

**Postconditions**

- Output: a header line and one line per user, ordered by `ID`, with five whitespace-separated
  fields: `ID`, `LOGIN`, `EMAIL`, `ESTADO`, `ROL`.
- `ROL` is a comma-separated list of role names, or `-` when the user has no capabilities row, no
  multi-role formatting with spaces. This keeps each line parseable with a plain split.
- No line contains a password or a hash.
- A prefix used for the users table and the capabilities key is the same value, in the same run.
- `--listar` with any of `--password`, `--usuario` or `--id` fails before touching the DB.
- The listing exits 0 and writes nothing.
- Without `--listar`, the script behaves byte to byte as after A.SPEC 0011.

**New falsifiable truth**: the number of lines printed equals the number of rows in the users table,
and the `ID` printed for a duplicated login is usable as `--id` to reset exactly one of them.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      The listing is read-only. It selects no password column and runs no statement that writes.
    proof: >
      test_listar_no_imprime_hashes, asserting the output contains no `$wp$`/`$P$` and that every
      user's hash is unchanged after the run; plus test_listar_solo_lectura_en_el_codigo, a static
      check that `usuarios_con_rol` names no writing verb.

  - id: I2
    statement: >
      The prefix for the users table and for the capabilities meta_key is the same discovered value.
    proof: >
      test_listar_muestra_el_rol_administrator, which asserts a role is found on the live site;
      a hardcoded `wp_` on a lab prefix would return `-` for every user.

  - id: I3
    statement: >
      A user with no capabilities row is listed, with an empty role.
    proof: test_listar_muestra_rol_vacio_para_el_usuario_sin_capabilities

  - id: I4
    statement: >
      The listing is mutually exclusive with the write flags, and the rejection happens before any
      query.
    proof: test_listar_es_excluyente_con_los_flags_de_escritura, with a hash snapshot before/after.

  - id: I5
    statement: >
      The ID the listing prints is the ID the reset accepts. The two tools agree.
    proof: >
      test_el_id_del_listado_sirve_para_resetear: it takes one of the duplicated IDs from the
      listing, resets by it, and asserts only that row changed.

  - id: I6
    statement: >
      The reset path is unchanged.
    proof: >
      The existing A.SPEC 0011 tests still pass without edits; test_listar_no_cambia_el_reset checks
      the argparse defaults.

  - id: I7
    statement: >
      The listing works without the site volume, because it only needs the database.
    proof: >
      NOT PROVEN YET. Needs a site whose web root is a bind mount, which is A.SPEC 0009's lab. On
      the demo site the code path is verified by reading it; the live proof stays pending.
```

## VERIFICATION

**Gates**

```bash
ruff check scripts/
python3 -m pyright scripts/
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
```

**Live tests** (added to the existing suite; its fixture already creates the users, backs up both
tables and restores them)

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_listar_muestra_todos_los_usuarios` | — | a user is missing from the listing |
| `test_listar_muestra_los_dos_lab_dup_con_ids_distintos` | I5 | duplication is hidden or merged |
| `test_listar_muestra_el_rol_administrator` | I2 | the join or the serialized parser is wrong |
| `test_listar_muestra_rol_vacio_para_el_usuario_sin_capabilities` | I3 | a user without role disappears or errors |
| `test_listar_no_imprime_hashes` | I1 | the password column leaks into the output |
| `test_listar_es_excluyente_con_los_flags_de_escritura` | I4 | a read flag silently ignores a write flag |
| `test_el_id_del_listado_sirve_para_resetear` | I5 | the two tools disagree on the ID |
| `test_listar_ordena_por_id` | — | the order is unstable |
| `test_listar_no_cambia_el_reset` | I6 | adding the flag moved the reset defaults |

**New fixture user**: `lab_norol`, created without a capabilities row, to exercise I3 against a real
row instead of a fake one.

**Proof of falsifiability**: every test above fails against the code before this A.SPEC, because
`--listar` does not exist and the script exits with an argparse error.

## ROLLBACK

`git checkout scripts/reset_wp_password.py scripts/tests/test_reset_wp_password_live.py`. No
migration, no schema change, no state on the server: the listing never wrote. The fixture's restore
removes the test users.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/reset_wp_password.py
    - scripts/tests/test_reset_wp_password_live.py
    - aspecs/0008-list-wordpress-users.md
  prohibited:
    - scripts/probe_docker.py                  # the detection is not this feature
    - scripts/tests/test_probe_docker.py       # its own offline suite stays as it is
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
    - scripts/reset_wp_password.py (one flag, the listing query, the docstring fix)
    - scripts/tests/test_reset_wp_password_live.py (one fixture user, ten tests)
  indirect:
    - One extra read query against the DB when `--listar` runs. No locks, no writes, no container.
    - The site's volume is no longer required for the listing, so a bind-mounted site becomes
      listable even though it is not resettable.
  must_not_affect:
    - surface: reset_de_contrasena
      surfaces: [main camino reset, hash_de_password, UPDATE por ID]
      invariant: I6
    - surface: solo_lectura
      surfaces: [--listar completo]
      invariant: I1
    - surface: prefijo
      surfaces: [prefijo_de_tablas, la clave de capabilities]
      invariant: I2
    - surface: credenciales
      surfaces: [pedir, redactar, build_docker_argv]
      invariant: I1 — no se agrega un lugar nuevo con la password en el argv (A.SPEC 0012)
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - A.SPEC 0011   # the listing supplies the ID that 0011 demands on ambiguity
    - A.SPEC 0012   # the listing reuses the DB command path and must not add a new argv leak
  systemic_invariants:
    - There is one prefix discovery. The listing must not introduce a second.
    - The live suite is shared: the fixture created for 0011 is what makes this listing testable
      without new infrastructure.
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

`reset_wp_password.py` is ~450 lines after A.SPEC 0011 and goes to ~500 with the listing: the query,
the serialized-role parser and the aligned formatter. Under the review threshold; no new module,
because the listing shares the prefix discovery and splitting it would duplicate that.

## Traceability

- **Requirement**: see who the users of a WordPress are, with the ID needed to act on one
- **A.SPEC**: this document
- **Code**: `scripts/reset_wp_password.py` (`--listar`, the join, the role parser)
- **Migration**: none
- **Test**: `scripts/tests/test_reset_wp_password_live.py` (ten new live tests)
- **Commit**: `TBD`
- **Deployment**: none. Does not enter the Docker image.

- **owner**: `TBD`
- **approver**: `TBD`

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [ ] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established

**`Invariants preserved` stays unchecked for one reason only**: I7 (the listing works without the
site volume) needs a site whose `/var/www/html` is a bind mount, and that is A.SPEC 0009's lab. On
the demo site the code path is verified by reading it, not by running it. Everything else is proven
by the live suite: 21 tests, 151 s, and the site came back to one user.
