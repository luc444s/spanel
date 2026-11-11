# Domains

English | [Español](README.es.md)

Web domain administration plugin.

## Ownership

- A site's **primary domain** is written by Hosting (Traefik label and
  `HostingSite.domain`). This plugin shows it read-only.
- **Extra domains** are written by this plugin into `spanel-<slug>.yml` inside
  the Traefik dynamic directory, with the `# managed-by: spanel` marker.
- The **mail domain** belongs to Tenant/Mail. Here accounts are only read to
  show the count.

The `systutor.contracts.domain_ownership` contract is the only bridge to
Hosting: it publishes primaries and asks whether a domain is already an extra.
No plugin imports the other's models.

## Reserving and linking (A.SPEC 0040)

A domain can be created **without a site**: it is normalized and ownership is
claimed in `tenant_domains` without writing any `spanel-*.yml`, without probing
certificates and without running Docker. The item is listed with
`published=false` and the certificate answers `unpublished`. It is then
published with `POST /domains/{domain}/link` against a tenant site; the link
uses the same `ProxyOperations.add()` as site creation (same YAML, priority and
resolver) and keeps the reservation if the proxy fails.

- `DELETE` of a **published extra** removes the route as usual.
- `DELETE` of a **reserved** domain releases the row with the `release` provider
  of the `domain_namespace` contract (optional registration in
  `register_domain_providers`); if the domain is the tenant's mail domain the
  provider answers `409 tenant_domain_in_use`.
- `GET /domains` is **tolerant to a downed proxy**: it returns the domains with
  registered ownership plus `proxy_available=false` + `proxy_error`.

## API

| Method | Path | Permission |
|---|---|---|
| GET | `/api/v1/plugins/domains` | `domains.domains.read` |
| POST | `/api/v1/plugins/domains` | `domains.domains.manage` |
| POST | `/api/v1/plugins/domains/{domain}/link` | `domains.domains.manage` |
| DELETE | `/api/v1/plugins/domains/{domain}` | `domains.domains.manage` |
| GET | `/api/v1/plugins/domains/{domain}/certificate` | `domains.domains.read` |

## Configuration

It reuses `SPANEL_PROXY_*` from the CLI (`scripts/proxy_domain.py`). Its own
variables are `SPANEL_DOMAINS_PROFILE` (default `public`) and
`SPANEL_PROXY_PROBE_HOST` / `SPANEL_PROXY_PROBE_TIMEOUT` for probing.

### Transport (A.SPEC 0029)

`SPANEL_PROXY_TRANSPORT` is `auto` (default), `local`, `ssh` or `docker`.

- `auto` tries in order: configured SSH (`SPANEL_PROXY_SSH_HOST`), Docker with a
  writable Traefik, and finally the local `SPANEL_PROXY_CONFIG_DIR`. If none
  applies it answers `503 domains.proxy_unavailable` detailing each check.
- `docker` detects the Traefik container, reads its static config inside the
  container and writes the `spanel-*.yml` files with `docker exec` into the file
  provider's bind mount. There is no need to mount the directory or configure
  SSH.
- An explicit mode that fails does not silently fall back to another one.

Docker transport overrides:

| Variable | Effect |
|---|---|
| `SPANEL_PROXY_TRAEFIK_CONTAINER` | exact Traefik container name |
| `SPANEL_PROXY_CONFIG_DIR` | directory **inside Traefik** (override) |
| `SPANEL_PROXY_PROBE_HOST` | probe host when there is no shared network |
| `SPANEL_PROXY_CERT_RESOLVER` | wins over the detected resolver |

`doctor` reports `transport: docker|local|ssh (reason)`. Without a viable
transport, operations answer `503 domains.proxy_unavailable`.

## CLI

`python3 scripts/proxy_domain.py` keeps the `doctor`, `list`, `add`, `status` and
`remove` commands. The implementation lives in `backend/proxy/`; the script is a
shim.
