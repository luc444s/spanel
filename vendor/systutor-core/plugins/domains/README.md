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

## Authoritative DNS (A.SPEC 0067)

The plugin operates PowerDNS Authoritative through one shared
`PowerDnsClient`. The CLI and FastAPI use the same client, validation,
versioning and zone-file modules; neither shells out nor implements a second
HTTP path.

Host-owned configuration:

```text
SPANEL_PDNS_API_URL
SPANEL_PDNS_API_KEY_FILE
SPANEL_PDNS_SERVER_ID=localhost
SPANEL_PDNS_TIMEOUT_SECONDS=5
```

The key file must be regular and owner-only. There is no plaintext key
fallback and the URL/key are never returned by the API, written to the
database, logged or included in CLI JSON.

PostgreSQL owns tenant-to-zone ownership (`domains_dns_zones`) and the public
nameserver contract (`domains_dns_settings`). PowerDNS owns SOA, NS and every
RRset. A zone belongs to exactly one tenant. `ns1` and `ns2` may share one
address and the API always reports that this is **not physical redundancy**.

### CLI

```text
python3 scripts/spanel_dns.py doctor
python3 scripts/spanel_dns.py nameservers show|plan|apply|verify
python3 scripts/spanel_dns.py zones list|create|show|delete
python3 scripts/spanel_dns.py rrsets list|plan|apply|delete
python3 scripts/spanel_dns.py zone import|export
```

Mutations are plans unless `--apply` is present. Destructive deletion requires
`--confirm-zone` or `--confirm-name` when stdin is not interactive. Exit codes:
`0` success/no drift, `2` validation, `3` drift/unapplied plan, `4` conflict,
`5` PowerDNS unavailable, `6` ownership/data. `--json` output is sorted and
stable.

`nameservers verify --zone ... --resolver ... --port ...` performs real UDP and
TCP SOA/NS queries.

### API

| Method | Path | Permission |
|---|---|---|
| GET | `/api/v1/plugins/domains/dns/doctor` | `domains.dns.read` |
| GET | `/api/v1/plugins/domains/dns/nameservers` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/nameservers/plan` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/nameservers/apply` | `domains.dns.nameservers` + `core.platform.admin` |
| POST | `/api/v1/plugins/domains/dns/nameservers/verify` | `domains.dns.read` |
| GET/POST | `/api/v1/plugins/domains/dns/zones` | `domains.dns.read` / `domains.dns.zones` |
| GET/DELETE | `/api/v1/plugins/domains/dns/zones/{zone_name}` | `domains.dns.read` / `domains.dns.zones` |
| GET | `/api/v1/plugins/domains/dns/resolve?domain=...` | `domains.dns.read` |
| GET | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets` | `domains.dns.read` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/plan` | `domains.dns.records` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/apply` | `domains.dns.records` |
| POST | `/api/v1/plugins/domains/dns/zones/{zone_name}/rrsets/delete` | `domains.dns.records` |
| POST/GET | `/api/v1/plugins/domains/dns/zones/{zone_name}/import` / `export` | `domains.dns.records` / `domains.dns.read` |

Tenant administrators receive read, records and zones by default. They never
receive nameserver configuration. Every write requires an explicit
`expected_version`; a stale or externally changed zone returns `409`.
TXT values are never copied to audit rows: only owner, type, count and hash.
`nameservers plan` reports one entry per approved zone with its current
fingerprint in `version` (also exposed as `zone_versions`). `nameservers apply`
requires `--zone ZONE --zone-version ZONE=VERSION` for every approved existing
zone and rejects missing/stale versions with conflict before changing
PowerDNS. `--no-zones` explicitly approves an empty set, which is the safe
bootstrap path for persisting the first public settings; omitting the zone
flags is never interpreted as “all zones”.

### Domains DNS interface (A.SPEC 0068)

The Domains page reads the tenant zone list once and shows `Managed`,
`External`, `No zone` or `Error` for every domain. An owned row opens its DNS
zone on double click or `Enter`; there is deliberately no per-row DNS button.
The zone dialog supports RRset search/filter, structured plan/apply with the
plan version, protected apex SOA/NS records, import/export and exact-name zone
deletion. Conflicts are shown and are never retried automatically.

The global **Nameservers** dialog displays and verifies UDP/TCP state.
Configuration requires both `domains.dns.nameservers` and
`core.platform.admin`, plans all explicitly listed owned zones and always
sends an explicit approved set. An empty set is reserved for first-settings
bootstrap. Registrar delegation, DNSSEC and Cloudflare remain outside the UI.

## Cloudflare OAuth and discovery (A.SPEC 0071)

The backend and CLI can connect one tenant to Cloudflare through the
Authorization Code flow with S256 PKCE. This layer is **read-only**: it lists
authorized accounts, zones and redacted DNS records but has no endpoint that
creates, edits or deletes records, zones, nameservers or proxy settings.
A.SPEC 0072 adds a tenant-scoped Cloudflare connection card to the Domains page
for users with `domains.dns.cloudflare`.

Host configuration:

```text
SPANEL_CLOUDFLARE_ENCRYPTION_KEY_FILE=/run/secrets/apps_key
SPANEL_CLOUDFLARE_PUBLIC_URL=https://spanel.lab
SPANEL_CLOUDFLARE_TIMEOUT_SECONDS=10
```

The key file is a regular, owner-only file containing exactly 32 raw bytes or a
base64/base64url encoding of 32 bytes. The default path reuses the same
`apps_key` file the Apps plugin mounts (base64url text). Envelopes use
AES-256-GCM with random nonces and AAD binding the purpose, the tenant/flow
identity and the key version. Client secrets, access/refresh tokens,
authorization codes, PKCE verifiers and bootstrap tokens are never stored or
printed in plaintext. A missing/unsafe key file is a configuration error; there
is no plaintext environment fallback.

### CLI

```text
python3 scripts/spanel_dns.py cloudflare oauth register \
  --account-id ACCOUNT --bootstrap-token-file PATH \
  --redirect-uri https://spanel.lab/api/v1/plugins/domains/dns/cloudflare/oauth/callback
python3 scripts/spanel_dns.py cloudflare connect --tenant TENANT [--wait-seconds 300]
python3 scripts/spanel_dns.py cloudflare status --tenant TENANT
python3 scripts/spanel_dns.py cloudflare disconnect --tenant TENANT [--apply]
python3 scripts/spanel_dns.py cloudflare zones list --tenant TENANT
python3 scripts/spanel_dns.py cloudflare records list ZONE --tenant TENANT
```

`oauth register` is a dry-run unless `--apply` is present; the bootstrap token
file must be owner-only and is never persisted. Re-registration is a conflict;
rotation is out of scope. `connect` prints the Cloudflare authorization URL,
then waits for the backend callback without ever receiving the OAuth token;
`--wait-seconds 0` returns immediately. `disconnect` is a dry-run unless
`--apply`; it revokes best-effort and removes local credentials idempotently.
All commands accept `--json`; record content is always redacted.

### Panel

Users with `domains.dns.cloudflare` see a **Connect Cloudflare** button next to
**Nameservers** on the Domains page. It asks the backend for an authorization
URL, opens it in a popup and polls the authenticated status endpoint every two
seconds until the flow completes or expires; once connected the same button
becomes **Disconnect Cloudflare**. A tenant administrator can connect and
disconnect their own tenant; the OAuth client itself is still registered once
by the platform operator with the CLI. The browser never receives tokens,
client secrets or record content.

Platform administrators also see the client state. When no OAuth client exists,
**Connect Cloudflare** opens a modal to paste a Cloudflare API token with
`Account → OAuth Clients → Edit`. The token is used once, never stored, and the
private client is created with the fixed redirect URI and required scopes. If
the token cannot list a single account, an optional Account ID field appears.
The CLI `cloudflare oauth register` remains available as the operator path.

### API

| Method | Path | Permission |
|---|---|---|
| POST | `/api/v1/plugins/domains/dns/cloudflare/oauth/start` | `domains.dns.cloudflare` |
| GET | `/api/v1/plugins/domains/dns/cloudflare/oauth/client` | `domains.dns.cloudflare` + `core.platform.admin` |
| POST | `/api/v1/plugins/domains/dns/cloudflare/oauth/register` | `domains.dns.cloudflare` + `core.platform.admin` |
| GET | `/api/v1/plugins/domains/dns/cloudflare/oauth/callback` | public, single-use hashed state only |
| GET | `/api/v1/plugins/domains/dns/cloudflare/status` | `domains.dns.cloudflare` |
| POST | `/api/v1/plugins/domains/dns/cloudflare/disconnect` | `domains.dns.cloudflare` |
| GET | `/api/v1/plugins/domains/dns/cloudflare/zones` | `domains.dns.cloudflare` |
| GET | `/api/v1/plugins/domains/dns/cloudflare/zones/{zone_id}/records` | `domains.dns.cloudflare` |

The callback validates a random, hashed, expiring, single-use state and the
stored redirect URI/PKCE transaction. It never accepts a tenant from query
data and reveals no tenant/account existence on replay, expiry or denial.
Errors normalize to `422`, `403`, `404`, `409`, `429`, `502`, `503` and `504`.
