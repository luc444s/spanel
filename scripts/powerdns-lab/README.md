# Isolated PowerDNS laboratory

This laboratory runs PowerDNS Authoritative 4.9 with PostgreSQL 16. It is a
developer/operator environment, not a public DNS deployment. One PowerDNS
process answers as both `ns1.pilot.test` and `ns2.pilot.test`; those names use
the same address and do **not** provide physical redundancy.

## Requirements

- Bash, Docker Engine with Compose v2, and `dig`.
- A free loopback TCP **and** UDP port (5300 by default).

The API and PostgreSQL are reachable only on the private Compose network.
Only DNS is published, on `127.0.0.1`.

## Lifecycle

```bash
scripts/powerdns-lab.sh plan
scripts/powerdns-lab.sh doctor
scripts/powerdns-lab.sh up
scripts/powerdns-lab.sh status
scripts/powerdns-lab.sh seed
scripts/powerdns-lab.sh verify
scripts/powerdns-lab.sh down
scripts/powerdns-lab.sh up       # the PostgreSQL volume is reused
scripts/powerdns-lab.sh verify
scripts/powerdns-lab.sh down --purge --yes
```

Use port 53 only as an explicit operator choice:

```bash
POWERDNS_LAB_DNS_PORT=53 scripts/powerdns-lab.sh up
```

`up` checks TCP and UDP before mutation and never stops a conflicting service.
`doctor` always reports both port-53 transports independently. If a named
container, network, or volume lacks `com.spanel.powerdns-lab=true`, lifecycle
operations stop rather than adopting or deleting it.

## Runtime state and cleanup

Secrets are generated under
`${XDG_STATE_HOME:-$HOME/.local/state}/spanel/powerdns-lab`, outside this
repository. Override it with an **absolute** `POWERDNS_LAB_STATE_DIR`. The
directory is mode 0700 and each secret file is mode 0600. Commands do not print
secret values. PowerDNS settings are rendered atomically to an owned mode-0600
`pdns.conf`, mounted read-only, so API and database secrets never appear in the
container command line or host process diagnostics.

Normal `down` removes only the two labelled containers and labelled network;
the database volume and runtime secrets remain. `down --purge` also removes the
exact labelled volume and owned state after confirmation. Foreign files in the
state directory make purge fail closed.

`POWERDNS_LAB_ADDRESS` controls the seeded A records and defaults to the
documentation address `192.0.2.53`.
