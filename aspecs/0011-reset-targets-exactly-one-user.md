# A.SPEC 0011 — Reset exactly one user, never the rows that share a login

> `risk: low` — one script, one flag, one SQL predicate. No migration, no schema change, no API
> contract. The rollback is a `git checkout` of one file. Mode: `extreme-poverty`.

## WHY

`reset_wp_password.py` resets a password with:

```sql
UPDATE {prefijo}users SET user_pass='...' WHERE user_login='{usuario}'
```

`user_login` **is not unique in the database**. Verified against the real WordPress core that ships
in `spanel-lucas-wp`, in `wp-admin/includes/schema.php`:

```
194:	user_login varchar(60) NOT NULL default '',
203:	PRIMARY KEY  (ID),
204:	KEY user_login_key (user_login),
205:	KEY user_nicename (user_nicename),
206:	KEY user_email (user_email)
```

Line 204 is `KEY`, not `UNIQUE KEY`. WordPress keeps logins unique in application code, but the
database allows duplicates, and a hand-made import or a direct `INSERT` leaves them there.

With two rows sharing a login, `WHERE user_login='admin'` updates **both**. Then the read-back
returns two hashes, which can never equal the one hash the script is comparing against, so it exits
with:

```
FALLO: el UPDATE no quedo aplicado como se esperaba
```

That message is false in the worst possible way. The update **was** applied — to two accounts. The
operator is told nothing happened, on a run that already reset an account nobody named, and the
natural reaction is to try again.

Verified on 2026-10-22 against two real rows in `spanel-demowp-wp`: the old script exits 1 with that
message and **both** `user_pass` values change. The failure is silent about the damage, which is the
only part that matters here.

**This state is reachable in practice.** `wp user create` refuses a duplicate login, which is
exactly why the bug never shows up in a normal site: the only way to reach it is the path nobody
tests — a manual import.

## WHAT

**The reset targets exactly one row, and when a login resolves to more than one it stops and shows
the IDs instead of choosing.**

Falsable today, and today **fails**: two rows with the same `user_login` are both updated.

## SCOPE

1. `scripts/reset_wp_password.py`:
   - `--id N` selects a user by primary key, which is unique by definition;
   - `--usuario` keeps working, but default becomes `None` so the script can tell "not given" from
     "given as `admin`"; when neither is given, the login falls back to `admin`, preserving today's
     behaviour;
   - passing both explicitly is an error, not a precedence rule;
   - the raw `mariadb -N -B` user rows are turned into a decision: exactly one ID, or a failure that
     lists the candidates;
   - the target is resolved **before** the hash is computed, so an ambiguous or missing login costs
     no container and no WordPress boot;
   - the `UPDATE` and the read-back both use `WHERE ID=<n>`, never the login.
2. `scripts/tests/test_reset_wp_password_live.py`: new. **Integration test against the real laptop.**
   No fixtures, no mocks: it runs the real script against the real site, creates the users, and
   reads the real database.
3. This document.

## OUT OF SCOPE

- **No offline unit tests, by explicit decision of the user (2026-10-22).** A green suite built on
  invented strings proved nothing about this script: both real bugs it ever had were in the part a
  fixture cannot reach. The test has to touch the machine.
- **The credential handling of the DB is NOT touched here.** The `-p` in the remote argv, the
  unescaped DB password and the missing prefix validation are real, but they are a different
  failure (a leak, not a wrong row). They go in A.SPEC 0012.
- **No `--listar`.** Displaying the users with their IDs is A.SPEC 0008. This A.SPEC only needs the
  IDs to appear in the ambiguity error so the operator can act.
- **No `LIMIT 1`.** That would silently pick a row, which is the bug wearing a seatbelt. The target
  has to be an ID, chosen before writing.
- **No new table, no new column, no migration.**
- **No change to `probe_docker.py`.**
- **No automatic repair of duplicate logins.** A site in that state is broken in ways this script
  should not guess about; it reports and stops.

## CONTRACT

**Preconditions**

- The site is running and installed.
- At most one of `--id` and `--usuario` is given explicitly.

**Postconditions**

- `--id N` targets the row with `ID = N`. If that row does not exist, the script fails and says so.
- `--usuario X` with exactly one match targets that row.
- `--usuario X` with **more than one** match fails, lists every candidate as `ID <n> <login> <email>`,
  and writes nothing.
- `--usuario X` with zero matches fails as it does today, with the same message.
- Neither flag: behaves as `--usuario admin`.
- Both flags explicitly: fails before touching the database.
- `--dry-run` performs the full resolution and prints the chosen `ID`, so ambiguity is caught
  without writing. It computes no hash.
- The `UPDATE` statement contains `WHERE ID=` and does not mention `user_login`.
- After the update, the read-back is by `ID` and must equal the new hash.

**New falsifiable truth**: with two rows sharing a login, a plain reset fails and writes nothing;
`--id` resets exactly one, and the other keeps its hash.

## The live test, and its safety rails

The test writes to a real site, so the rails are part of the contract, not comments.

1. **Opt-in.** It does nothing unless `SPANEL_LIVE_TESTS=1`. Without that variable every test is
   skipped, so it cannot fire by accident from a generic `pytest` run.
2. **Allowlist of sites.** The target comes from `SPANEL_LIVE_SITE` (default `spanel-demowp-wp`) and
   the name must match `^spanel-(demowp|lab)-`. A typo can never point it at another site.
3. **Backup before writing.** A `mysqldump` of `wp_users` and `wp_usermeta` is written to a local
   temporary file before the first `INSERT`.
4. **Unconditional restore.** A pytest finalizer restores both tables from that dump **even if a
   test fails in the middle**. Deleting the rows by ID is the fast path; restoring the dump is the
   guarantee.
5. **Throwaway logins only.** The test never duplicates or resets the real `admin`. It creates
   `lab_a1`, `lab_a2` and two rows of `lab_dup`. The default-admin path is checked with
   `--dry-run`, which writes nothing.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      Every write is scoped by primary key. No UPDATE in the reset path filters by `user_login`.
    proof: >
      test_caso_b_por_id_resetea_uno, which reads both rows from the real database and asserts only
      the selected ID changed; plus test_el_update_va_por_id, a static check over the file.

  - id: I2
    statement: >
      An ambiguous login never resolves by guessing. Two or more matches is a failure, never the
      first row, never the lowest ID, and the failure names every candidate.
    proof: test_caso_b_login_duplicado_falla, against two real rows.

  - id: I3
    statement: >
      An ambiguous login writes nothing and starts nothing. Resolution happens before the hash, so
      the failure costs no container and no WordPress boot.
    proof: >
      test_caso_b_login_duplicado_falla asserts both hashes are unchanged and that no
      `wordpress:cli` container was created during the attempt.

  - id: I4
    statement: >
      `--id` is exempt from login resolution and works even when the login is duplicated.
    proof: test_caso_b_por_id_resetea_uno

  - id: I5
    statement: >
      The reset itself is unchanged: same `wp_hash_password`, same single-row update, same
      self-check, and the new password actually authenticates.
    proof: >
      test_caso_a_resetea_y_la_password_nueva_entra, which runs `wp_check_password` on the real
      hash after the reset. Not a string comparison: a real login check.

  - id: I6
    statement: >
      Default behaviour is preserved. With no flags, the script still targets `admin`.
    proof: test_sin_flags_apunta_a_admin_sin_escribir, with `--dry-run` against the real site.

  - id: I7
    statement: >
      `--dry-run` writes nothing and still catches ambiguity.
    proof: test_dry_run_detecta_la_ambiguedad

  - id: I8
    statement: >
      The test leaves the site as it found it. After the suite, the hash of every pre-existing user
      is identical to the backup, and the test users are gone.
    proof: >
      The restore finalizer plus test_limpieza_deja_el_sitio_como_estaba, which compares the
      pre-existing rows against the dump.
```

## VERIFICATION

**Gates** (all three):

```bash
ruff check scripts/
python3 -m pyright scripts/
SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_reset_wp_password_live.py -q
```

The third one talks to the laptop over Tailscale and writes to `spanel-demowp-wp`. That is
intended: this A.SPEC does not accept a test that never touches the machine.

**Live tests** (`scripts/tests/test_reset_wp_password_live.py`)

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_caso_a_dos_logins_distintos` | I1 | resetting one admin changes the other |
| `test_caso_a_resetea_y_la_password_nueva_entra` | I5 | the new password does not authenticate |
| `test_caso_b_login_duplicado_falla` | I2, I3 | the script picks one silently and resets both |
| `test_el_error_de_ambiguedad_lista_todos_los_ids` | I2 | the operator cannot act on the failure |
| `test_caso_b_por_id_resetea_uno` | I1, I4 | `--id` still goes through login resolution |
| `test_dry_run_detecta_la_ambiguedad` | I7 | dry-run cannot preview the target |
| `test_sin_flags_apunta_a_admin_sin_escribir` | I6 | adding the flag changed the default |
| `test_id_y_usuario_juntos_fallan` | — | two selectors silently precedence |
| `test_el_update_va_por_id` | I1 | the UPDATE still filters by login |
| `test_limpieza_deja_el_sitio_como_estaba` | I8 | the suite leaves rows or changes behind |

**Evidence that goes in the commit message** (the "before" and "after"):

- Before the fix, with two real rows sharing a login: the `UPDATE` exits 1 with
  `el UPDATE no quedo aplicado como se esperaba`, and **both** `user_pass` values changed anyway.
  Captured on 2026-10-22: `FILAS CAMBIADAS: 2 de 2`, rc=1. That run is what proves the bug is real
  and that its message lies.
- After the fix: the plain reset exits non-zero, both hashes stay identical, and `--id` changes
  exactly one.

**Proof of falsifiability**: against the code before this A.SPEC, `test_caso_b_login_duplicado_falla`
fails on its **hash** assertion, because both rows change. Its `returncode != 0` assertion is *not*
the discriminator: the old code also exits 1, with the misleading "el UPDATE no quedo aplicado"
message. The bug is not that it returns success; it is that it writes to both rows and then claims
it did nothing.

## ROLLBACK

`git checkout scripts/reset_wp_password.py` and delete
`scripts/tests/test_reset_wp_password_live.py`. No migration, no schema change, no state on the
server: the live test restores its own dump, and the fallback is that same dump applied by hand.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/reset_wp_password.py
    - scripts/tests/test_reset_wp_password_live.py
    - aspecs/0011-reset-targets-exactly-one-user.md
  prohibited:
    - scripts/probe_docker.py                  # not this failure
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
    - scripts/reset_wp_password.py (one flag, the target resolution, the UPDATE predicate)
    - scripts/tests/test_reset_wp_password_live.py (new, ~10 integration tests)
  indirect:
    - Each test run writes and deletes rows on spanel-demowp-wp, and restores both user tables from
      a dump. The site is a demo; a failure mid-run is recovered by the finalizer.
    - The reset now does one extra read before writing, replacing the read it already did.
  must_not_affect:
    - surface: hash_de_password
      surfaces: [hash_de_password, PHP_HASH_AUTOCOMPROBADO]
      invariant: I5
    - surface: seleccion_de_la_fila
      surfaces: [UPDATE, read-back]
      invariant: I1
    - surface: comando_por_defecto
      surfaces: [main resolucion de argumentos]
      invariant: I6
    - surface: sitios_que_no_son_el_target
      surfaces: [SPANEL_LIVE_SITE, allowlist del test]
      invariant: I8
    - surface: credenciales
      surfaces: [pedir, redactar, build_docker_argv]
      invariant: I5 — this A.SPEC does not fix them, and must not regress them either
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - A.SPEC 0008   # the listing must show the ID that this A.SPEC makes selectable
  systemic_invariants:
    - The prefix keeps coming from prefijo_de_tablas, the same discovery the listing uses.
    - A.SPEC 0012 will change how the DB credentials travel. This A.SPEC must not add a new place
      that puts them in an argv, so the later fix has one shape to change.
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
    - scripts/tests/test_reset_wp_password_live.py
```

`reset_wp_password.py` is ~391 lines and grows to ~430. The test file is separate because it has a
different reason to change: it follows the site, not the script.

## Traceability

- **Requirement**: resetting a password must touch exactly one user, even when the database has
  duplicate logins, and the operator must be able to name that user unambiguously
- **A.SPEC**: this document
- **Code**: `scripts/reset_wp_password.py` (`--id`, the target resolution, `WHERE ID=` in the UPDATE)
- **Migration**: none
- **Test**: `scripts/tests/test_reset_wp_password_live.py` (new, runs against the real laptop)
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

**Unchecked on purpose.** They get ticked when the live suite has run against the laptop, the site
came back to its original state, and the "before" evidence is in the commit message. A green gate
that never touched a database is not what this A.SPEC accepts as proof.
