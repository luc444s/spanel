# GitFlow Lite ADD

## Purpose

Skill for running GitFlow's lightest practical variant under ADD: `main` plus
short-lived `add/*` branches, no `develop` staging branch.

> One A.SPEC = one branch = one squash-commit on `main`.

Keeps transactional history while avoiding the overhead of a shared
integration branch for small teams or agent-driven development.

## Use When

- single developer or 1-2 agents
- `main` must always be deployable
- no need for a shared pre-release integration surface
- releases are cut by tagging `main`
- the team values minimal branch moves over a formal release ceremony

## Do Not Use When

- multiple developers or agents integrate in parallel every day
- you need a shared buffer to integrate work before any release candidate
- you want release branches with freeze + hotfix ceremony separate from `main`
- strict governance or compliance requires a staging integration point

## Core Law

`main` is the only long-lived branch. Every change merges into `main` as a
single atomic commit.

No long-lived branches other than `main`. No `develop`. No `release/*`.

## Branch Model

```text
main                        ← production, always deployable, tagged vX.Y.Z
  ├── add/HOST-0042 ──┐ squash-merge
  ├── add/HOST-0043 ──┼──────────→ main
  └── hotfix/HOST-0045 ──┘
```

Branch naming: `add/<PROJECT>-<NUMBER>-<verb>` or `hotfix/<...>`.

## Cycle ADD → GitFlow

| Phase ADD | GitFlow artifact |
|-----------|------------------|
| DEFINE | Issue + PR description (WHAT observable) |
| BOUND | Change Surface in PR (allowed / prohibited) |
| CONTRACT | CONTRACT + INVARIANTS sections of the A.SPEC |
| IMPLEMENT | commits on `add/<ID>` branch |
| VERIFY | CI + tests on the branch, before merge |
| INTEGRATE | squash-merge → `main` = 1 commit per A.SPEC |

## Rules

1. `add/` branch always off the latest `main`.
2. One branch lives and dies for exactly one A.SPEC.
3. Squash-merge is mandatory; never fast-forward the branch.
4. PR description is the A.SPEC, written before any code.
5. No opportunistic refactoring: a new improvement opens a new `add/` branch.
6. Revert of a merged commit = ROLLBACK of the A.SPEC in one step.
7. Tag releases on `main`: `vX.Y.Z`, with release changelog (ADD rule 11.3).

## Integration

```bash
git checkout -b add/HOST-0042-discover-wordpress main
# ... implement + verify on branch ...
git commit -m "feat: HOST-0042 Discover existing WordPress containers"
git checkout main && git pull
git merge --squash add/HOST-0042-discover-wordpress
git commit -m "add: HOST-0042 Discover existing WordPress containers"
git push origin main
```

## Hotfix

- branch `hotfix/<ID>` off `main`
- merge direct to `main`
- still carries an A.SPEC (contract + invariants)
- tagged immediately if it ships a release fix

## Reverse / Rollback

- `git revert <squash-commit>` reverts the whole A.SPEC atomically
- release tag rollback = `git revert` of the tag commit on `main`

## Blast Radius Note

Without `develop` there is no shared integration surface. Two agents merging
in parallel can conflict at `main`. Mitigate by:

- keeping branches short-lived
- rebasing `add/*` onto latest `main` before merge
- reviewing PRs sequentially when surface overlaps

## Completion Checklist

- [ ] branch named after a single A.SPEC
- [ ] PR description carries the A.SPEC (WHAT, CONTRACT, INVARIANTS)
- [ ] squash-merged into `main` as one commit
- [ ] A.SPEC traceable from requirement to commit to deployment
- [ ] no opportunistic refactoring leak
- [ ] invariants verified before merge