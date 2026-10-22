# A.SPEC 0009 — Build a throwaway WordPress lab to test against prefixes, roles, MariaDB versions and multiple sites

> `risk: normal` — This one is not like its neighbours. It **creates containers and volumes on a
> real machine**, and it is the first A.SPEC in this repo that adds infrastructure instead of
> logic. There is no code change to roll back; there is a `docker rm -f` and a volume prune.
> Mode: `extreme-poverty`.

## WHY

A.SPEC 0008 says, in its own OUT OF SCOPE, that it does not prove the listing works against
another WordPress version, another prefix, or another MariaDB. It can only test on
`spanel-demowp-wp`, which has one table prefix (`wp_`), one role in practice (`administrator`, the
only user), one MariaDB version, and one user.

**One happy configuration is not a test suite.** It is one data point. Every bug real de este
script se encontró por la suposición equivocada, no por la lógica:

| The assumption | Where it broke | Which config would have caught it |
|---|---|---|
| phpass `$P$B` | WordPress 7 uses bcrypt `$wp$` | a second WP version |
| prefix is `wp_` | the suffix is `users` (5), not `_users` (6) | a second prefix |
| the site has an `admin` | not all sites do | a site with no `admin` |

The first two are fixed and tested. **The third is not tested at all**: right now, if a site has no
user called `admin`, the tool says "no such user" and the operator has to guess. That is a gap this
A.SPEC exists to close.

The lab is also the only way to test the thing that will actually be hard in production:
**several sites at once**, where a wrong prefix or a cached base makes you write to the wrong
site. That failure mode does not exist on a single-site test.

## WHAT

**A reproducible lab exists on the machine, and the listing plus the reset are exercised against
every configuration in it, in a real run rather than a fixture.**

Falsable today, and today **fails**: there is no lab, so those configurations have never run.

## The lab

Four sites, deliberately different from each other and from the demo:

| Site | WP version | Table prefix | MariaDB | Why this one exists |
|---|---|---|---|---|
| `spanel-lab-wp-a` | latest (`wordpress`) | `wp_` | `mariadb:11` | the baseline, matches the demo |
| `spanel-lab-wp-b` | `wordpress:6.7-php8.1-apache` | `lab2_` | `mariadb:10.6` | **the phpass one.** WP ≤ 6.7 hashes with `$P$B`; `RE_HASH_PHASS` is in the code and has never run against a real site |
| `spanel-lab-wp-c` | `wordpress:php8.3-apache` | `deep_prefix_` | `mariadb:11` | a prefix long enough to break a naive slice, and to expose any off-by-one in the suffix |
| `spanel-lab-wp-d` | `wordpress` | `wp_` | `mariadb:11` | **the multi-site one.** Same network as A, B and C, on purpose: the point is that the tool must never mix them up |

D is not a multisite install. It is a fourth site sharing a network, which is the real risk: a base
resolved from the wrong container, or a prefix from site B applied to site A.

**Site B is the important one.** It is the only way to exercise `RE_HASH_PHASS`, and the phpass
branch of `hash_valido` is currently dead code that nobody has ever seen return true.

## SCOPE

1. `scripts/lab_wordpress.sh` (new): creates and destroys the lab. One command to up, one to down.
2. `scripts/tests/test_lab_users.py` (new): offline checks that the lab script carries the prefixes
   and the `wp-config.php` `table_prefix` in agreement — the failure being tested is them drifting
   apart.
3. `scripts/reset_wp_password.py`: **only if the lab finds something.** Allowed to be fixed here,
   with the failing site and its output named in the commit. Not planned work — a contingency.
4. `aspecs/0009-wordpress-lab-for-testing.md`: this document.

## OUT OF SCOPE

- **No change to the listing's behaviour beyond fixing what the lab finds.** The tool may be
  edited when the lab produces a concrete failure, because a bench that cannot fix what it finds
  is a bench that gets ignored. But every edit has to name the site configuration that failed and
  paste the failing output, so the fix is traceable to evidence.
- **No touch to the three real sites.** `spanel-lucas-wp`, `spanel-test-wp` and `spanel-demowp-wp`
  are not part of the lab and are never stopped, started, or written to by the lab script. The lab
  matches its containers by the `spanel-lab-` prefix and refuses to act on anything else.
- **No plugin `hosting`.** Same boundary as 0007 and 0008.
- **No cleanup of the host.** The lab does not remove the real sites, prune all volumes, or touch
  anything outside `spanel-lab-*`.
- **No real user data.** Every lab site is created empty and destroyed after. No copying of the
  demo's content.

## CONTRACT

**Preconditions**

- The operator has accepted that four WordPress images and four MariaDB images get pulled, which
  is several GB and several minutes.
- The names `spanel-lab-{wp,db}-{a,b,c,d}` are free on the machine.

**Postconditions**

- After up: four WP containers running, four DB containers running, each site installed, each with
  at least one user whose login is **not** `admin`, so the "no admin" path is real data.
- The table prefix in the database and the `table_prefix` in `wp-config.php` agree, per site.
- After down: no `spanel-lab-*` container and no `spanel-lab-*` volume remains.
- The three real sites are byte-identical before and after, verified by listing their containers
  and their user counts.

**New falsable truth**: the reset runs successfully against site B, producing a `$P$B` hash, which
proves `RE_HASH_PHASS` works on a real site instead of only in a unit test.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      The lab only touches containers whose name starts with `spanel-lab-`. Any other name in the
      script is a bug, including in the destroy path.
    proof: >
      test_lab_solo_nombra_spanel_lab, a static check over the lab script: every container name
      literal in it begins with `spanel-lab-`. This is the invariant that keeps a `docker rm -f`
      from hitting a real site, so it is enforced by reading the file, not by trusting the author.

  - id: I2
    statement: >
      The database prefix and the `table_prefix` in `wp-config.php` are the same value per site.
    proof: >
      test_prefijo_de_la_lab_coincide, which for each site compares the declared prefix against the
      one the install wrote, from the recorded state. A mismatch here is exactly the bug class this
      lab exists to catch, so the lab itself must not be able to have it.

  - id: I3
    statement: >
      The real sites are not touched. Before and after the run, their container list, their state
      and their user counts are identical.
    proof: >
      Recorded before and after, compared by hand and in the commit message. Not automatable, and
      said so instead of pretended.

  - id: I4
    statement: >
      `down` removes the lab completely: no `spanel-lab-*` container and no volume. A leftover
      volume holds a database and is the most likely way this leaves a mess.
    proof: >
      test_down_lista_tanto_contenedores_como_volumenes, a static check that the destroy path names
      both, plus a manual `docker ps -a` and `docker volume ls` after the run.

  - id: I5
    statement: >
      The tool may be fixed in this A.SPEC, but never blind. Every change to the reset or the
      listing names the site configuration that failed and carries its failing output, so the diff
      is traceable to evidence rather than to a hunch.
    proof: >
      The commit message of this A.SPEC lists, for each change, the lab site and the step that
      failed. A change with no failing output attached does not belong here. `scripts/probe_docker.py`
      stays prohibited: the lab tests the reset and the listing, not the detection.

  - id: I6
    statement: >
      Site B's phpass hash is produced by `wp_hash_password` on WordPress 6.7 and accepted by
      `RE_HASH_PHASS`. The branch stops being dead code.
    proof: >
      The real run of VERIFICATION step 4, with the resulting hash pasted into the commit message.

  - id: I7
    statement: >
      Nothing in this lab is deployed. It is a test bench, not a feature.
    proof: change_surface; the script is not in the Dockerfile path.
```

## VERIFICATION

**Gates**

```bash
ruff check scripts/
python3 -m pytest scripts/tests -q
```

The shell script has no linter configured here; it is checked by reading it and by the static tests
in I1 and I4.

**Offline tests**

| Test | Invariant | What it would falsify |
|---|---|---|
| `test_lab_solo_nombra_spanel_lab` | I1 | a `docker rm` can reach a real site |
| `test_prefijo_de_la_lab_coincide` | I2 | the lab creates a site it cannot test |
| `test_down_lista_contenedores_y_volumenes` | I4 | the destroy leaves a database behind |
| `test_lab_declara_los_cuatro_sitios` | — | the lab quietly lost a configuration |

**Live run, the part that matters** (manual, on the real machine)

1. `up`. Then confirm: 4 WP + 4 DB running, each site installed, each with a non-`admin` user.
2. Confirm each site's real prefix from `SHOW TABLES`, and that it matches its `wp-config.php`.
3. List against A: the default prefix path.
4. **Reset against B**, the phpass site. Expected: a `$P$B` hash, 34 characters. This is the single
   most valuable line in the whole A.SPEC, because it is the only one that exercises a branch that
   is otherwise untested.
5. List against C: the long prefix. Watch the suffix slice.
6. List against D, and then A again. Expected: D and A show their own users, and running A twice
   gives the same answer. This is the cross-contamination test.
7. Reset a non-`admin` user on A. Expected: works, no guessing.
8. Run a listing on a container name that does not exist, and on a real site. Expected: a clear
   error, never a traceback, and never a write to the real site.
9. `down`. Confirm no `spanel-lab-*` container and no volume.
10. Confirm the three real sites are exactly as before (I3).

## What this A.SPEC does NOT prove

- That the fixes, if any, are complete. A lab proves the cases it happens to contain. Four sites
  cover what we thought of, not what we did not.
- Real MariaDB versions outside 10.6 and 11. Real-world sites also run MySQL 8, and MariaDB 10.3.
  Two versions is a start, not coverage.
- Multisite WordPress (`MULTISITE`), network tables, and `wp_2_users`-style per-site tables. The
  lab has four separate sites, not one multisite network. `prefijo_de_tablas` **fails closed** when
  it finds more than one prefix, which is the right behaviour, but "fails closed" is not "works".
- Performance under load, backups, restores, or a site with thousands of users.
- Anything about the plugin `hosting`, which does not exist yet.

## ROLLBACK

```bash
# remove every container
docker rm -f $(docker ps -aq --filter 'name=spanel-lab-')
# remove every volume the lab created
docker volume ls -q --filter 'name=spanel-lab-'
```

The code rollback is deleting `scripts/lab_wordpress.sh` and the test file. The infrastructure
rollback is the two commands above, and it matters more than the code one.

**Cost of the rollback being wrong**: leftover volumes holding four databases. Nothing of the real
sites, if I1 holds. If I1 does not hold, potentially a lot. That asymmetry is the whole reason I1
is a static check over the file and not a comment.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/lab_wordpress.sh
    - scripts/tests/test_lab_users.py
    - scripts/reset_wp_password.py      # I5: solo con evidencia de un fallo concreto del lab
    - aspecs/0009-wordpress-lab-for-testing.md
  prohibited:
    - scripts/probe_docker.py           # el lab prueba el reset y la lista, no la deteccion
    - vendor/systutor-core/**           # the kernel does not know about Docker
    - vendor/systutor-shell/**          # the UI is not touched
    - docker-compose.yml                # the lab stands alone, by design
    - .env
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - Four WordPress containers and four MariaDB containers on the machine (12 containers total)
    - Eight Docker volumes, four of which hold databases
    - Several GB of image pulls
  indirect:
    - The machine runs heavier while the lab is up. With 8 extra containers, the real sites share
      less CPU and RAM than before. On a small host this is noticeable.
    - `docker ps` shows 8 more rows, which the probe of A.SPEC 0007 will now list. That is fine
      and expected, and it is a free extra test of the detection against a fresh set of names.
  must_not_affect:
    - surface: sitios_reales
      surfaces: [spanel-lucas-*, spanel-test-*, spanel-demowp-*]
      invariant: I3
    - surface: alcance_del_lab
      surfaces: [lab_wordpress.sh up, lab_wordpress.sh down]
      invariant: I1
    - surface: herramienta_under_test
      surfaces: [reset_wp_password.py]
      invariant: I5 — tocable, pero cada cambio con su evidencia de por medio
    - surface: deteccion
      surfaces: [probe_docker.py]
      invariant: I5 — prohibido, el lab no re-testea la deteccion
```

## Composition

```yaml
composition:
  requires_aspecs:
    - A.SPEC 0008   # the lab is the bench on which 0008's out-of-scope claims get checked
  must_compose_with:
    - A.SPEC 0007   # the detection will see the lab's containers
  systemic_invariants:
    - The lab's containers are ordinary WordPress to the probe. A future change that makes the
      probe skip them by name would be a regression of A.SPEC 0007, not a feature.
    - Any future A.SPEC that changes the reset or the listing has to re-run the lab, or say
      explicitly that it did not.
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
    - scripts/lab_wordpress.sh
```

A shell script, because it is infrastructure, not application logic. It is expected to stay under
200 lines and it must stay readable, since the person most likely to run it under stress is a
human, not CI.

## Traceability

- **Requirement**: test the reset and the listing against prefixes, roles, WordPress versions and
  MariaDB versions other than the one happy configuration we happen to have
- **A.SPEC**: this document
- **Code**: `scripts/lab_wordpress.sh`
- **Migration**: none
- **Test**: `scripts/tests/test_lab_users.py` (new)
- **Commit**: `TBD`
- **Deployment**: none. Never deployed; exists only on the machine where it was run.

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

**Two boxes above are conditional and get answered by hand, not by a gate:**

- *Scope respected*, with `scripts/reset_wp_password.py` in the change surface, means: every hunk
  in that file names the lab site and the step that failed. If there is no failing output for a
  change, the hunk does not belong in this commit.
- *Rollback / compensation is honest* now has **two** rollbacks, not one. The code one is a
  `git checkout`; the infrastructure one is `docker rm -f` plus removing the volumes, and that is
  the one that matters.

**Unchecked, and for a different reason than in 0008.** Here the risk is not a false green test
but a real footprint on someone's machine. The boxes get ticked when the lab is up, the ten live
steps ran, and `down` left nothing behind — including on the three real sites.

**Open question for the operator, asked before the code and not after**: pulling several GB and
running 8 extra containers on this host. It is the price of not shipping a tool tested on exactly
one configuration, and it is a price worth paying only if someone will actually run the lab. If the
answer is that nobody will, the honest move is to skip this A.SPEC and to write down in 0008 that
the listing is verified on one site and nowhere else.
