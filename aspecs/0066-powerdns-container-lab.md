# A.SPEC 0066 — Operate an isolated PowerDNS Authoritative container lab

> `risk: high` — introduces an authoritative DNS service, exercises TCP and
> UDP listeners and includes an explicit production-port mode for port 53.
> The default lab is loopback-only on port 5300 and all destructive operations
> are restricted to resources owned and labelled by this lab.

## WHY

PanelTutor cannot build or verify DNS-zone management against assumptions or a
mock-only server. It needs a reproducible PowerDNS Authoritative environment
with the same PostgreSQL backend and HTTP API that later operator and FastAPI
contracts will use.

Port 53 may already belong to BIND, systemd-resolved or another container. A
DNS installer that checks only TCP, silently replaces an existing daemon or
uses broad Docker cleanup is unsafe. The laboratory must prove both DNS
transports, identify conflicts before mutation and remove only its own
resources.

## WHAT

Add an operator-controlled laboratory with:

- one pinned PowerDNS Authoritative 4.9 container using the `gpgsql` backend;
- one pinned PostgreSQL 16 container initialized with the canonical PowerDNS
  PostgreSQL schema;
- one PowerDNS process serving both `ns1` and `ns2` identities;
- an HTTP API reachable only from the private Compose network;
- authoritative DNS published on both TCP and UDP;
- loopback port `5300` by default and an explicit configurable port, including
  production port `53`;
- generated API/database secrets outside the repository and outside Compose;
- healthchecks, idempotent seed data and real `dig` verification;
- preflight reporting for existing TCP and UDP port-53 owners;
- safe status, stop and purge operations constrained by exact project identity
  and labels.

The thin entrypoint is `scripts/powerdns-lab.sh` with these commands:

```text
plan
doctor
up
status
seed
verify
down
down --purge [--yes]
```

`plan` is read-only and works without Docker. `doctor` is read-only, checks
dependencies and reports TCP/UDP port 53 independently. `up` performs preflight
before creating anything. `seed` creates or reconciles `pilot.test` with SOA,
NS and A data. `verify` performs real SOA, NS and A queries over UDP and TCP and
checks the internal API without publishing its port. Repeated `up`, `seed`,
`verify` and `down` calls are idempotent.

Runtime state defaults to
`${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-lab`. Secret files have
mode `0600`; generated configuration and command output must never print their
values. The location may be overridden with `POWERDNS_LAB_STATE_DIR`.

## SCOPE

Entra:

- the A.SPEC itself;
- the PowerDNS laboratory shell entrypoint;
- an isolated Compose definition and pinned PostgreSQL schema;
- focused shell/unit tests for rendering, preflight and ownership guards;
- a destructive live test harness that can run locally or through the
  SSH credentials explicitly supplied in environment variables;
- documentation for operating this laboratory.

The live harness may use the existing ignored `.env.docker` credentials only
when the caller explicitly maps them to `POWERDNS_LAB_SSH_*`. It must transfer
only the lab files to a unique remote temporary directory, redact credentials,
and remove that directory in `finally`/trap cleanup.

## OUT OF SCOPE

- PanelTutor/FastAPI endpoints, models, migrations or permissions.
- The reusable PowerDNS client and the DNS operator CLI from A.SPEC 0067.
- Any frontend code.
- Migrating, delegating or modifying a real Cloudflare domain.
- Registrar glue records.
- DNSSEC, AXFR, NOTIFY, secondary mode or a second PowerDNS instance.
- A second physical server or claims of nameserver redundancy.
- Publishing the PowerDNS HTTP API on a host port.
- Installing, stopping, disabling or reconfiguring BIND, systemd-resolved or
  any pre-existing DNS service.
- Changes to the root `docker-compose.yml` or application environment files.

## CONTRACT

### Preconditions

1. `bash`, Docker with Compose v2 and `dig` are available on the target lab
   host for live operation.
2. The selected host TCP and UDP DNS port are free, or the operation stops
   before creating/changing containers.
3. Existing resources with the laboratory's names but without its ownership
   labels are foreign and must never be adopted, replaced or deleted.

### Postconditions

1. `up` produces one healthy PowerDNS container and one healthy PostgreSQL
   container in a private, lab-owned network.
2. PowerDNS uses PostgreSQL, not SQLite, and survives a normal `down`/`up`
   cycle through a lab-owned volume.
3. The host exposes the selected DNS port on `127.0.0.1` for both TCP and UDP;
   no host port exposes PostgreSQL or the PowerDNS API.
4. `seed` leaves `pilot.test` authoritative with exactly the expected SOA and
   `ns1.pilot.test`/`ns2.pilot.test` NS identities. Both names may resolve to
   the same configured lab address and are explicitly reported as not
   physically redundant.
5. `verify` proves successful UDP and TCP answers with `aa` set, validates SOA
   and both NS records, and receives an authenticated response from the
   internal PowerDNS API.
6. `doctor` reports TCP and UDP port-53 availability separately and, when
   available from `ss`/Docker metadata, identifies the owning process or
   container without changing it.
7. `down` removes only the lab containers and network and preserves the
   PostgreSQL volume. `down --purge` additionally removes only the lab-owned
   volume and generated runtime state after confirmation or `--yes`.
8. No secret value appears in tracked files, `plan`, `status`, test artifacts,
   process diagnostics or normal command output.

The independent falsable truth is: from an empty labelled lab, one command can
start a PostgreSQL-backed authoritative server whose real UDP/TCP answers and
private API are verified, then remove only those owned resources.

## INVARIANTS

```yaml
invariants:
  - id: existing-dns-services-untouched
    statement: Pre-existing BIND, systemd-resolved, containers and host listeners are never stopped, reconfigured or removed.
    proof: Live and unit tests force a conflicting listener/foreign resource and assert that up fails before mutation and the owner remains present.
  - id: application-stack-untouched
    statement: The root PanelTutor Compose project, database, proxy, mail and application containers are never addressed by lab mutation commands.
    proof: Change-surface check plus ownership-guard tests assert exact lab project names and labels.
  - id: api-not-host-published
    statement: PowerDNS API and PostgreSQL have no host port mapping.
    proof: Compose config assertion and live docker inspect assertion.
  - id: secrets-not-tracked-or-printed
    statement: API and database secret values exist only in mode-0600 runtime files and are redacted from output/artifacts.
    proof: Unit/live tests inspect permissions, rendered Compose/config and captured output without printing values.
  - id: cleanup-is-owned-and-bounded
    statement: Cleanup refuses same-name foreign resources and never invokes docker system prune or broad volume/network deletion.
    proof: Static forbidden-command test and live foreign-resource guard test.
  - id: lab-default-is-loopback-high-port
    statement: Without explicit overrides DNS is published only on 127.0.0.1 port 5300 for TCP and UDP.
    proof: Render test and live docker inspect assertion.
```

## VERIFICATION

Run exactly from the repository root:

```bash
bash -n scripts/powerdns-lab.sh scripts/tests/powerdns_lab_live.sh
python3 -m pytest scripts/tests/test_powerdns_lab.py -q
scripts/powerdns-lab.sh plan
scripts/powerdns-lab.sh doctor
git diff --check
```

Run the destructive isolated proof against the configured lab host. Values are
loaded without echoing them and are mapped explicitly; the harness must fail
closed if any required value is absent:

```bash
python3 -m dotenv -f vendor/systutor-core/.env run -- bash -c '
  POWERDNS_LAB_SSH_HOST="$MAIL_SERVER_HOST" \
  POWERDNS_LAB_SSH_PORT="$MAIL_SERVER_PORT" \
  POWERDNS_LAB_SSH_USER="$MAIL_SERVER_USER" \
  POWERDNS_LAB_SSH_PASSWORD="$MAIL_SERVER_PASSWORD" \
  scripts/tests/powerdns_lab_live.sh
'
```

The live proof must execute and record PASS for this ordered lifecycle:

1. transfer to a unique remote temporary directory;
2. `plan` and `doctor` without mutation;
3. `up`, `status`, idempotent second `up`;
4. `seed`, idempotent second `seed`;
5. `verify` with UDP SOA/NS/A and TCP SOA/NS/A queries;
6. inspect that PostgreSQL/API have no published host ports;
7. `down`, then `up` and `verify` to prove persisted PostgreSQL state;
8. `down --purge --yes` and assert that only labelled lab resources and the
   unique remote temporary directory are absent;
9. retain only redacted command/result evidence locally.

## ROLLBACK

The laboratory is reversible and owns no production zone. Execute:

```bash
scripts/powerdns-lab.sh down --purge --yes
```

Rollback succeeds only when containers, network, volume and runtime state bear
the exact lab identity. If an expected resource is foreign or its label is
missing, cleanup stops and reports the resource for human inspection. It does
not broaden deletion to make rollback appear successful.

The live verification executes both non-purging removal/recreation and final
purging removal; prose or a merely present `down` branch is not sufficient.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0066-powerdns-container-lab.md
    - scripts/powerdns-lab.sh
    - scripts/powerdns-lab/compose.yml
    - scripts/powerdns-lab/init/001-schema.sql
    - scripts/powerdns-lab/README.md
    - scripts/tests/test_powerdns_lab.py
    - scripts/tests/powerdns_lab_live.sh
  prohibited:
    - docker-compose.yml
    - .env
    - .env.*
    - secrets/**
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/**
    - apps/**
    - installer/**
    - any pre-existing host DNS configuration
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - isolated PowerDNS laboratory containers, network, volume and external runtime-state directory
    - selected loopback TCP/UDP DNS port
  indirect:
    - Docker image downloads and temporary remote test directory
    - host port-53 diagnostics
  must_not_affect:
    - pre-existing host DNS services and listeners
    - root PanelTutor Compose application and its data
    - Traefik, mail and application containers
    - tracked or host application secrets
    - Cloudflare zones and public delegation
```

Every `must_not_affect` surface is covered respectively by
`existing-dns-services-untouched`, `application-stack-untouched`,
`cleanup-is-owned-and-bounded`, `secrets-not-tracked-or-printed`, and the
absence of all Cloudflare/registrar operations from the allowed surface plus
the live cleanup evidence.

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - A.SPEC 0067 consumes the private PowerDNS API and this lab contract without changing its ownership boundaries.
    - A.SPEC 0069 starts and removes this lab through its public operator commands.
  systemic_invariants:
    - Later layers never publish or return the PowerDNS API key.
    - Later layers cannot weaken labelled cleanup or port-conflict detection.
  composition_checks:
    - scripts/powerdns-lab.sh verify
    - scripts/tests/powerdns_lab_live.sh
```

This is a leaf infrastructure A.SPEC; its composition checks are proved here
and reused, not deferred, by later A.SPECs.

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: the shell entrypoint coordinates lifecycle while Compose declares services and tests own verification logic
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - scripts/powerdns-lab.sh
    - scripts/powerdns-lab/compose.yml
    - scripts/tests/test_powerdns_lab.py
    - scripts/tests/powerdns_lab_live.sh
```

## Traceability

- Requirement: A.SPEC 0066 — PowerDNS + PostgreSQL container lab with private
  API, TCP/UDP DNS, healthchecks, real dig proof and BIND conflict detection.
- owner: PanelTutor maintainers
- approver: project owner (human)
- Code: `scripts/powerdns-lab.sh`, `scripts/powerdns-lab/**`
- Test: `scripts/tests/test_powerdns_lab.py`,
  `scripts/tests/powerdns_lab_live.sh`
- Migration: none
- Commit: pending
- Deployment: isolated labelled laboratory; no production deployment

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest and executed
- [ ] Composition checks passed
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
