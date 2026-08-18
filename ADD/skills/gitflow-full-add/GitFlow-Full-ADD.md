# GitFlow Full ADD

## Purpose

Skill for running the full GitFlow discipline under ADD: `main` +
`develop` + `add/*` + `release/*` + `hotfix/*`.

> One A.SPEC = one branch = one squash-commit on `develop`.
> A release = a bounded set of A.SPEC shipped to `main` under a tag.

Adds a shared integration buffer and formal release ceremony for teams or
parallel agent workflows.

## Use When

- multiple developers or agents integrate in parallel daily
- you need a shared pre-release integration surface before any release
- releases are frozen on `release/*` branches before touching production
- separate hotfix path for urgent production fixes
- governance wants a clear staging integration point

## Do Not Use When

- single developer, low concurrency
- you want minimal branch moves and no release ceremony
- `main` addressing production directly is sufficient

## Core Law

- `main` = releasable production, tagged.
- `develop` = integration ground for all A.SPEC.
- `add/*` = one short-lived branch per A.SPEC.
- `release/*` = frozen set of A.SPEC for a version.
- `hotfix/*` = urgent production fix.

No `add/*` merges into `main`. Everything flows through `develop` and
`release/*`.

## Branch Model

```text
main (tags vX.Y.Z)
 └── develop ............ integration
       ├── add/HOST-0042 ──┐ squash-merge
       ├── add/HOST-0043 ──┼────────→ develop
       └── release/1.4.0 ───────→ main (tag)
             │
             └── hotfix/HOST-0045 → main + develop
```

## Cycle ADD → GitFlow

| Phase ADD | GitFlow artifact |
|-----------|------------------|
| DEFINE | Issue + PR description (WHAT observable) |
| BOUND | Change Surface in PR (allowed / prohibited) |
| CONTRACT | CONTRACT + INVARIANTS sections of the A.SPEC |
| IMPLEMENT | commits on `add/<ID>` branch off `develop` |
| VERIFY | CI + tests on the branch, before merge |
| INTEGRATE | squash-merge → `develop` = 1 commit per A.SPEC |
| RELEASE | `release/x.y.z` off `develop` → merge → `main` + tag |
| HOTFIX | `hotfix/<ID>` off `main` → merge → `main` + `develop` |

## Rules

1. `add/` branch always forks from (and syncs with) `develop`.
2. One branch lives and dies for exactly one A.SPEC.
3. Squash-merge `add/*` into `develop`; never fast-forward.
4. PR description is the A.SPEC, written before any code.
5. No opportunistic refactoring: a new improvement opens a new `add/` branch.
6. `release/` freezes the A.SPEC set; only hotfix-level fixes allowed there.
7. Hotfix goes to BOTH `main` and `develop` to prevent regression on next
   release.
8. Changelog required per release (ADD rule 11.3).
9. ROLLBACK = revert the A.SPEC commit in `develop`, or revert the release tag
   commit in `main`.

## Integration

```bash
git checkout -b add/HOST-0042-discover-wordpress develop
# ... implement + verify on branch ...
git commit -m "feat: HOST-0042 Discover existing WordPress containers"
git checkout develop && git pull
git merge --squash add/HOST-0042-discover-wordpress
git commit -m "add: HOST-0042 Discover existing WordPress containers"
git push origin develop
```

## Release

```bash
git checkout -b release/1.4.0 develop
# freeze: only hotfix-level fixes, bump version, write changelog
git commit -am "release: 1.4.0"
git checkout main && git merge release/1.4.0
git tag -a v1.4.0 -m "release 1.4.0"
git checkout develop && git merge release/1.4.0   # keep parity
git push origin main develop --tags
```

## Hotfix

```bash
git checkout -b hotfix/HOST-0045 main
# fix as A.SPEC: contract + invariants
git commit -m "fix: HOST-0045 ..."
git checkout main && git merge hotfix/HOST-0045
git checkout develop && git merge hotfix/HOST-0045
# tag if it ships
git push origin main develop
```

## Parallel-Agent Note

`develop` is the conflict sink. Agents rebase `add/*` onto latest `develop`
before merge. Shared surfaces reviewed sequentially.

## Completion Checklist

- [ ] branch named after a single A.SPEC
- [ ] PR description carries the A.SPEC (WHAT, CONTRACT, INVARIANTS)
- [ ] squash-merged into `develop` as one commit
- [ ] released through `release/*` with tag + changelog
- [ ] hotfix merged into BOTH `main` and `develop`
- [ ] A.SPEC traceable from requirement to commit to deployment
- [ ] no opportunistic refactoring leak
- [ ] invariants verified before merge