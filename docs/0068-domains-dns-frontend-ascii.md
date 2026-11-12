# A.SPEC 0068 — DNS frontend ASCII reference

> Visual annex for `aspecs/0068-powerdns-domains-frontend.md`.
> The UI is English-first; Spanish comes from
> `apps/web/src/i18n/es/domains.ts`. Labels shown here are the English source
> strings and must match the implemented keys.

## 1. Domains page

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  Domains                                 [ Nameservers ]    [ Add domain ]   │
│  Double-click a domain to access its DNS zone.                              │
├──────────────────┬─────────┬───────────┬────────────┬────────────┬──────────┤
│ Domain           │ Tenant  │ Status    │ Owner      │ Certificate│ Actions  │
├──────────────────┼─────────┼───────────┼────────────┼────────────┼──────────┤
│ ejemplo.com      │ —       │ Published │ Hosting 🔒 │ Valid      │          │
│ tienda.com       │ —       │ Published │ Extra      │ Pending    │ Link  ✕  │
│ nuevo.com        │ —       │ Reserved  │ Domain     │ —          │ Link  ✕  │
│ otro.com         │ —       │ Published │ Extra      │ No DNS     │ Link  ✕  │
└──────────────────┴─────────┴───────────┴────────────┴────────────┴──────────┘
        ▲
        └── double-click or Enter opens the DNS zone for an owned row
```

There is **no** per-row `DNS` button. The DNS column described below is the
only persistent DNS affordance besides the row interaction.

## 2. DNS status column

```text
┌──────────────────┬──────────────────────────────────────────────┐
│ Domain           │ DNS                                          │
├──────────────────┼──────────────────────────────────────────────┤
│ ejemplo.com      │ ● Managed          owned zone matches        │
│ www.ejemplo.com  │ ● Managed          subdomain of owned zone   │
│ tienda.com       │ ○ External         no owned zone matches     │
│ nuevo.com        │ ◌ No zone          external, activation ready│
│ roto.com         │ ⚠ Error            zone list unavailable     │
└──────────────────┴──────────────────────────────────────────────┘

● Managed    green dot    opens the managed-zone dialog
○ External   muted dot    opens the unmanaged-zone dialog
◌ No zone    amber dot    opens the unmanaged-zone dialog with activation
⚠ Error      amber warn   opens the dialog with a retry action
```

One `GET /dns/zones` request feeds every row. The longest owned suffix decides
the state; no per-row request is made.

## 3. Row interaction

```text
┌─────────────────────────────────────────────────────────────────────┐
│  ejemplo.com                                    [ Link ]  [ ✕ ]     │
│  └─ double click anywhere except the buttons ──► opens DNS dialog   │
│  └─ focus row + Enter ─────────────────────────► opens DNS dialog   │
│                                                                     │
│  tienda.com                                     [ Link ]  [ ✕ ]     │
│  └─ platform user viewing another tenant ──────► no dialog          │
└─────────────────────────────────────────────────────────────────────┘
```

Rules implemented by the interaction helper:

```text
event target is button / a / input / select / textarea  → ignore
row belongs to another tenant                           → ignore
row is owned by the active tenant                       → open
```

## 4. Unmanaged zone dialog

```text
┌───────────────────────────────────────────────────────────────────┐
│  tienda.com                                                    ✕  │
│  DNS · zone not managed by PanelTutor                             │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  This domain is not authoritative here. Its records are managed    │
│  by an external DNS provider, for example Cloudflare.              │
│                                                                   │
│  Activating the zone creates SOA, NS and local glue records.       │
│  It does not change registrar delegation, DNSSEC or Cloudflare.    │
│                                                                   │
│                                        [ Activate DNS zone ]       │
└───────────────────────────────────────────────────────────────────┘
```

`Activate DNS zone` is hidden without `domains.dns.zones`. A confirmation
dialog names the zone and restates what will be created.

## 5. Activation blocked by missing nameservers

```text
┌───────────────────────────────────────────────────────────────────┐
│  tienda.com                                                    ✕  │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ⚠ Public nameservers are not configured.                          │
│                                                                   │
│  A platform administrator must configure ns1 and ns2 before the    │
│  first zone can be activated.                                      │
│                                                                   │
│                                             [ Close ]              │
└───────────────────────────────────────────────────────────────────┘
```

Triggered by the backend `422` for missing public settings; never retried
automatically and never shown as a success.

## 6. Managed zone dialog — Records tab

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  ejemplo.com   zone ejemplo.com   version 3f9a1c                        ✕ │
│  ┌──────────────┬──────────────┐                                          │
│  │   Records    │     Zone     │                                          │
│  └──────────────┴──────────────┘                                          │
├──────────────────────────────────────────────────────────────────────────┤
│  [ Search name, type or value… ]   Type [ All ▾ ]       [ + New RRset ]   │
│                                                                          │
│  Name        Type   TTL    Values                            Actions     │
│  ──────────────────────────────────────────────────────────────────────  │
│  @           SOA    300    ns1.ejemplo.com hostmaster…          🔒       │
│  @           NS     300    ns1.ejemplo.com                      🔒       │
│  @           NS     300    ns2.ejemplo.com                      🔒       │
│  @           A      300    203.0.113.10                       Edit Del   │
│  www         CNAME  300    ejemplo.com                        Edit Del   │
│  @           MX     300    10 mail.ejemplo.com                 Edit Del   │
│  @           TXT    300    2 values                            Edit Del   │
│  _dmarc      TXT    300    1 value                             Edit Del   │
│                                                                          │
│  8 RRsets · version 3f9a1c                                  [ Refresh ]   │
└──────────────────────────────────────────────────────────────────────────┘
```

Apex `SOA` and apex `NS` are locked. Search and type filters are client-side
over the already loaded zone.

## 7. RRset editor with mandatory plan

```text
┌───────────────────────────────────────────────────────────────────┐
│  Edit RRset — www                                              ✕  │
├───────────────────────────────────────────────────────────────────┤
│  Name   [ www                          ]                          │
│  Type   [ CNAME ▾ ]     TTL [ 300 ]                               │
│                                                                   │
│  Values                                                           │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ ejemplo.com                                                 │  │
│  │                                                             │  │
│  └─────────────────────────────────────────────────────────────┘  │
│  [ + Add value ]                                                  │
│                                                                   │
│  ── Plan preview ───────────────────────────────────────────────  │
│  ~ www CNAME 300  ejemplo.com                                     │
│                                                                   │
│                              [ Cancel ]   [ Plan ]                │
│                              [ Apply changes ]  (needs a plan)    │
└───────────────────────────────────────────────────────────────────┘
```

Lifecycle:

```text
draft ──Plan──► POST /rrsets/plan ──► preview + version
                                        │
                                        └──Apply──► POST /rrsets/apply
                                                    expected_version = plan.version
```

`Apply changes` stays disabled until the current draft has a successful plan.
Editing a field invalidates the previous plan.

## 8. Version conflict

```text
┌───────────────────────────────────────────────────────────────────┐
│  Zone changed since you opened it. Nothing was written.        ✕  │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Version you saw   3f9a1c                                         │
│  Current version   7c21d0                                         │
│                                                                   │
│  Reload the zone and plan the change again.                        │
│                                                                   │
│                              [ Dismiss ]   [ Reload zone ]        │
└───────────────────────────────────────────────────────────────────┘
```

Triggered by `409`. There is no automatic retry and no optimistic UI update.

## 9. Managed zone dialog — Zone tab

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  ejemplo.com   zone ejemplo.com   version 3f9a1c                        ✕ │
│  ┌──────────────┬──────────────┐                                          │
│  │   Records    │     Zone     │                                          │
│  └──────────────┴──────────────┘                                          │
├──────────────────────────────────────────────────────────────────────────┤
│  Export                                                                   │
│  Download the deterministic zone file.              [ Download .zone ]    │
│                                                                          │
│  Import                                                                   │
│  [ Choose file ]  or paste zone-file content                              │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ ejemplo.com. 3600 IN SOA ns1.ejemplo.com. hostmaster.ejemplo.com.  │  │
│  │ …                                                                  │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                        [ Import zone ]                   │
│                                                                          │
│  ⚠ Delete zone                                                           │
│  Type the zone name to confirm: [                              ]         │
│                                        [ Delete zone ]                   │
└──────────────────────────────────────────────────────────────────────────┘
```

- Export is an authenticated `text/plain` fetch saved as `ejemplo.com.zone`.
- Import posts the pasted text with the current `expected_version`.
- Delete requires the exact zone name and sends `expected_version` as a query
  parameter.

## 10. Nameservers dialog — current state

```text
┌────────────────────────────────────────────────────────────────────────┐
│  Public nameservers                                                 ✕  │
├────────────────────────────────────────────────────────────────────────┤
│  ns1   ns1.panel.example     A 203.0.113.10    AAAA 2001:db8::10       │
│  ns2   ns2.panel.example     A 203.0.113.10    AAAA 2001:db8::10       │
│  SOA   hostmaster@example.com                                          │
│  TTL   300                                         settings version 4  │
│                                                                        │
│  ⚠ ns1 and ns2 share one address. This is not physical redundancy.     │
│                                                                        │
│  [ Verify UDP/TCP ]        [ Configure ]  (platform administrator)      │
└────────────────────────────────────────────────────────────────────────┘
```

`Configure` requires `domains.dns.nameservers` **and** `core.platform.admin`.
`Verify UDP/TCP` only requires `domains.dns.read`.

## 11. Nameservers dialog — verification

```text
┌────────────────────────────────────────────────────────────────────────┐
│  Verify nameservers                                                 ✕  │
├────────────────────────────────────────────────────────────────────────┤
│  Zone      [ ejemplo.com ▾ ]                                           │
│  Resolver  [ 127.0.0.1              ]   Port [ 5300 ]                  │
│  Timeout   [ 5 ] seconds                                               │
│                                                                        │
│  ── UDP ──────────────────────────── authoritative ✔ ────────────────  │
│  SOA  ns1.ejemplo.com. hostmaster.ejemplo.com. 2026111201 …            │
│  NS   ns1.ejemplo.com.                                                 │
│  NS   ns2.ejemplo.com.                                                 │
│                                                                        │
│  ── TCP ──────────────────────────── authoritative ✔ ────────────────  │
│  SOA  ns1.ejemplo.com. hostmaster.ejemplo.com. 2026111201 …            │
│  NS   ns1.ejemplo.com.                                                 │
│  NS   ns2.ejemplo.com.                                                 │
│                                                                        │
│                                              [ Close ]                 │
└────────────────────────────────────────────────────────────────────────┘
```

A failed transport renders an error state for that transport and never a
partial success banner.

## 12. Nameservers dialog — plan and apply

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Configure nameservers                                                ✕  │
├──────────────────────────────────────────────────────────────────────────┤
│  ns1 [ ns1.panel.example ]   IPv4 [ 203.0.113.10 ]   IPv6 [ 2001:db8::10 ]│
│  ns2 [ ns2.panel.example ]   IPv4 [ 203.0.113.10 ]   IPv6 [ 2001:db8::10 ]│
│  SOA [ hostmaster@example.com ]          TTL [ 300 ]                     │
│                                                                          │
│  [ Plan ]                                                                │
├──────────────────────────────────────────────────────────────────────────┤
│  Plan result                                                             │
│  Settings changes: default_ttl                                          │
│                                                                          │
│  ☑ ejemplo.com      version 3f9a1c      SOA/NS change: yes               │
│  ☐ tienda.com       version 91bcc2      SOA/NS change: no                │
│  ☑ nuevo.com        version 44de07      SOA/NS change: yes               │
│                                                                          │
│  2 zones approved. Registrar delegation and Cloudflare are not touched.  │
│                                                                          │
│                                   [ Cancel ]   [ Apply plan ]            │
└──────────────────────────────────────────────────────────────────────────┘
```

Bootstrap with no zones:

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Plan result                                                             │
│  No owned zones yet. The settings will be saved without reconciling any  │
│  zone. This is not "apply to all zones".                                 │
│                                                                          │
│                                              [ Apply settings ]          │
└──────────────────────────────────────────────────────────────────────────┘
```

Apply payload shape produced by the UI:

```text
{
  "settings": { "ns1": "...", "ns2": "...", ... },
  "zones": ["ejemplo.com", "nuevo.com"],
  "zone_versions": { "ejemplo.com": "3f9a1c", "nuevo.com": "44de07" },
  "expected_version": 4
}
```

`zones` is always explicit. An empty approved set is only valid for first-time
settings bootstrap.

## 13. Error and permission states

```text
┌───────────────────────────────────────────────────────────────────┐
│  ⚠ PowerDNS is unavailable.                                       │
│  No change was applied.                              [ Retry ]    │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│  🔒 Read-only                                                     │
│  Your role can view DNS records but cannot change them.           │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│  ⚠ The zone changed since you opened it. Nothing was written.     │
│                                       [ Reload zone ]             │
└───────────────────────────────────────────────────────────────────┘
```

Mapping:

```text
403  read-only banner, mutation controls hidden
409  version conflict panel, no retry
422  field-level validation next to the editor
503  unavailable banner with retry
504  timeout banner with retry
```

## 14. Permission visibility matrix

```text
                                   read  zones  records  nameservers+platform
DNS column and hint                 ✔
Open dialog / read zone             ✔
Activate zone                             ✔
Delete zone                               ✔
Plan / apply / delete RRsets                     ✔
Import / export                                  ✔
Nameservers current state           ✔
Verify UDP/TCP                      ✔
Configure / apply nameservers                                ✔
```

## 15. Component map

```text
DomainsPage.tsx
│
├── header hint                      i18n key
├── Nameservers button ────────────► NameserversDialog.tsx
│                                     ├── current state
│                                     ├── verify form
│                                     └── plan/apply form
│
└── table row
    ├── DnsStatusCell.tsx ─────────► status from one zones query
    └── double click / Enter ──────► DnsZoneDialog.tsx
                                      ├── unmanaged state
                                      │   └── Activate DNS zone
                                      ├── RecordsTab.tsx
                                      │   ├── search + type filter
                                      │   ├── RrsetEditor.tsx
                                      │   │   ├── draft validation
                                      │   │   ├── plan preview
                                      │   │   └── apply with expected_version
                                      │   └── delete confirmation
                                      └── ZoneTab.tsx
                                          ├── export (text fetch)
                                          ├── import (explicit version)
                                          └── delete (exact-name confirm)

Shared helpers
├── dns/logic.ts   pure: status, suffix match, permissions, plan gating,
│                   draft validation, error mapping, payload builders
└── dns/api.ts     endpoints + react-query keys
```
