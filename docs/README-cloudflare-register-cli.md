# Cloudflare OAuth: CLI registration and repair

**English** | [Español](README-cloudflare-register-cli.es.md)

This guide explains how to register or repair the private Cloudflare OAuth
client used by Spanel. The procedure can be used to add the `Zone Write`
permission required for automatic zone provisioning.

> The dry run has been verified in production. Never paste tokens into command
> arguments, logs, tickets, or conversations.

## What it changes

With `--apply`, the command:

1. retrieves the IDs of the required OAuth scopes;
2. updates the existing private OAuth client with `PATCH`;
3. preserves the existing `client_secret`;
4. creates a replacement and encrypts its secret if the remote client was
   deleted;
5. stores metadata and a secret-free audit entry.

Configured scopes:

```text
Zone Read
Zone Write
DNS Read
DNS Write
offline_access
```

It does not create zones, modify DNS records, change nameservers, or
automatically renew tenant authorizations.

## Requirements

- Run commands from the deployment root, normally `/opt/spanel`.
- The `app` container must be current and healthy.
- Cloudflare Account ID.
- Public HTTPS URL of Spanel.
- A temporary Cloudflare API token with:

```text
Account → OAuth Clients → Edit
Account resources → the account being registered
```

The bootstrap API token is used once and is never persisted by Spanel.

## 1. Capture the token without exposing it

```bash
umask 077
read -rsp "Cloudflare API token: " CF_TOKEN
echo
printf '%s' "$CF_TOKEN" > /root/cloudflare-bootstrap-token
unset CF_TOKEN
chmod 600 /root/cloudflare-bootstrap-token
```

Copy it temporarily into the container:

```bash
docker compose exec -T app sh -c \
  'umask 077; cat > /tmp/cloudflare-bootstrap-token' \
  < /root/cloudflare-bootstrap-token
```

## 2. Validate the plan without calling Cloudflare

Replace the values enclosed in `<...>`:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare oauth register \
  --account-id <CLOUDFLARE_ACCOUNT_ID> \
  --bootstrap-token-file /tmp/cloudflare-bootstrap-token \
  --redirect-uri https://<SPANEL_HOST>/api/v1/plugins/domains/dns/cloudflare/oauth/callback \
  --json
```

The result should contain:

```json
{
  "applied": false,
  "client_secret": "<redacted>",
  "scopes": ["Zone Read", "Zone Write", "DNS Read", "DNS Write"]
}
```

This mode does not read the token, call Cloudflare, or modify SQL or DNS.
Therefore, a successful dry run does not prove that the token is valid.

## 3. Apply the repair

Repeat the previous command with `--apply`:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare oauth register \
  --account-id <CLOUDFLARE_ACCOUNT_ID> \
  --bootstrap-token-file /tmp/cloudflare-bootstrap-token \
  --redirect-uri https://<SPANEL_HOST>/api/v1/plugins/domains/dns/cloudflare/oauth/callback \
  --apply \
  --json
```

Continue only if it returns `"applied": true` and includes `Zone Write`.

## 4. Remove the temporary token

After a successful apply:

```bash
docker compose exec -T app rm -f /tmp/cloudflare-bootstrap-token
rm -f /root/cloudflare-bootstrap-token
```

You may also revoke the token in Cloudflare if it will not be reused for another
administrative repair.

## 5. Reconnect each tenant

Repairing the global client does not expand previously issued tenant tokens.
Reconnect each tenant by slug:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare connect \
  --tenant <TENANT_SLUG> \
  --wait-seconds 0
```

Open the resulting URL and complete consent. The previous connection remains
available if the new callback fails.

Verify the result:

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare status \
  --tenant <TENANT_SLUG> \
  --json
```

The `scopes` list must include `Zone Write`.

## 6. Test provisioning without modifying DNS

```bash
docker compose exec -T app python -c \
  'from plugins.domains.backend.dns.cli import main; raise SystemExit(main())' \
  cloudflare provision plan <DOMAIN> \
  --tenant <TENANT_SLUG> \
  --ipv4 <SERVER_PUBLIC_IPV4> \
  --www \
  --json
```

Exit code `3` means that pending drift exists; this is normal for a plan. Do not
use `--apply` or `--replace` until the JSON has been reviewed.

## Common problems

### `No module named 'fastapi'`

The host Python environment does not contain the backend dependencies. Run the
module inside the `app` container as shown in this guide.

### `the bootstrap token file is missing`

The file was deleted or was not copied into the container. Repeat steps 1 and 2.

### `Cloudflare rejected the authorization`

The API token is incorrect, expired, belongs to another account, or does not
have `Account → OAuth Clients → Edit`.

### `the Cloudflare OAuth client must be repaired before authorization`

The global client still lacks one or more required scopes. Complete step 3
before reconnecting the tenant.

### `Cloudflare authorization must be renewed with Zone Write`

The global client may already be repaired, but the tenant still has an older
token. Complete step 5.
