# Atomizer Python

## Purpose

Skill for splitting oversized or mixed-responsibility Python files under ADD.

Primary goal:

> Preserve one coherent responsibility surface and one main reason to change
> per file.

This skill exists to prevent `plugin.py`, `main.py`, `router.py`, or similar
entrypoints from becoming god-files.

## Use When

Use this skill when at least one of these is true:

- file mixes unrelated behaviors
- file has multiple reasons to change
- file is hard to navigate or review safely
- entrypoint file contains domain logic, orchestration, persistence, and HTTP
  handling all together
- file is already under structural pressure
- a new A.SPEC would otherwise keep appending behavior into same file

Heuristic only, not primary rule:

- `>400` lines: review cohesion
- `>600` lines: extraction strongly recommended

## Do Not Use When

- file is large but still clearly one cohesive unit
- requested change is tiny and does not add new responsibility
- split would be purely cosmetic with no structural gain

## Core Law

Order of judgment:

1. responsibility coherence
2. coupling
3. navigability
4. size

Size is warning signal. Not primary rule.

## Expected Inputs

- source file path
- current A.SPEC or change request
- allowed change surface
- invariants
- verification commands

## Required Output

Produce:

1. reason-to-change map
2. proposed target modules
3. extraction order
4. verification plan
5. final thin entrypoint shape

## Reason-To-Change Map

Before moving code, classify each block.

Typical categories:

- HTTP routes
- schemas / request-response models
- orchestration / workflows
- persistence / SQL queries
- external integration
- auth / permission guards
- serialization / formatting helpers
- constants / configuration

If one file contains many categories, split candidate is strong.

## Target Layout Patterns

Choose smallest layout that restores cohesion.

### Pattern A — Entry + routes + services

```text
backend/
  plugin.py
  routes.py
  services.py
  schemas.py
```

Use when one feature exists but file mixes HTTP and business logic.

### Pattern B — Entry + route groups

```text
backend/
  plugin.py
  routes/
    sites.py
    lifecycle.py
    backups.py
```

Use when one plugin exposes several endpoint families.

### Pattern C — Route + service + repo

```text
backend/
  plugin.py
  routes/
    sites.py
  services/
    provision.py
    backups.py
  repos/
    sites.py
  schemas.py
```

Use when SQL, orchestration, and route handling are mixed.

### Pattern D — Utility extraction only

```text
backend/
  plugin.py
  utils.py
```

Use when file is still cohesive but helper noise is too high.

## Entry Point Rule

Files named `plugin.py`, `main.py`, `register.py`, or `router.py` should stay
thin.

They may:

- create router objects
- import and include route modules
- wire dependencies
- expose framework entrypoints

They should not become the permanent home for all feature logic.

## Extraction Algorithm

### 1. Freeze behavior

- do not redesign behavior while splitting
- no opportunistic refactor
- preserve contracts and invariants first

### 2. Extract lowest-risk units first

Start with:

- constants
- pure helpers
- serializers
- pydantic models

Then move:

- endpoint families
- orchestration blocks
- persistence blocks

### 3. Separate by reason to change

Good split axes:

- `lifecycle`
- `provision`
- `backups`
- `sso`
- `files`

Bad split axes:

- arbitrary line counts
- "top half / bottom half"
- mixed util dumps

### 4. Keep imports directional

Prefer:

- routes -> services
- services -> repos
- services -> integrations

Avoid:

- repos importing routes
- circular imports between route modules
- plugin entrypoint importing symbols only to re-export hidden coupling

### 5. Keep shared state explicit

If modules need common helpers, move those helpers to dedicated module instead
of cross-importing route files.

### 6. Re-read final entrypoint

After extraction, `plugin.py` should read like wiring, not like product logic.

## Python-Specific Guidance

### FastAPI

- group related endpoints in same router module
- keep shared dependencies near the route family that uses them
- avoid one global router file with every endpoint in plugin

### SQLAlchemy

- keep raw query helpers together
- if several endpoints reuse same query patterns, move them to repo/helper
- do not scatter identical SQL fragments across many modules

### Pydantic

- co-locate schemas with feature family, or centralize in `schemas.py`
- do not leave request/response models buried inside giant route file if used
  across several modules

### External integrations

- docker, ssh, wp-cli, mailserver, HTTP clients should live behind focused
  helper/service modules once reused by more than one route family

## Safety Rules

- preserve public route paths
- preserve request/response schema unless A.SPEC says otherwise
- preserve permissions and auth guards
- preserve side effects and rollback behavior
- preserve existing verification commands
- split structure, not semantics

## Red Flags

Split is failing if:

- new modules still each have multiple unrelated responsibilities
- `plugin.py` still contains most logic after extraction
- circular imports appear
- moved code forces broad rename churn unrelated to contract
- verification surface gets bigger without feature value

## Minimal Acceptable Improvement

Even if full cleanup is too large, a valid extraction should at least achieve
one of these:

- new endpoint family leaves `plugin.py`
- orchestration logic leaves route file
- persistence/query logic leaves route file
- schemas leave oversized mixed file

## Example

Bad:

```text
backend/plugin.py
  list sites
  adopt site
  provision wordpress
  backups
  sso
  filebrowser
  logs
  lifecycle
```

Better:

```text
backend/
  plugin.py
  schemas.py
  routes/
    sites.py
    lifecycle.py
    backups.py
    sso.py
    files.py
  services/
    provision.py
    docker_bridge.py
```

## Completion Checklist

- [ ] file responsibilities are clearer than before
- [ ] entrypoint became thinner
- [ ] no invariant changed
- [ ] no opportunistic redesign introduced
- [ ] verification still passes
- [ ] diff remains traceable to current A.SPEC
