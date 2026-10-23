# A.SPEC 0016 — Edit a site's configuration and rotate its salts from the panel

> `risk: normal` — this is UI over an already-tested backend (A.SPEC 0015). It does not add a
> destructive path: the only writes are the existing `PATCH /config` and `POST /secrets/salts`.
> The risk is showing or sending the wrong thing, not deleting data.

## WHY

A.SPEC 0015 gave the backend a whitelisted config route, a stable secret file and a salts rotation.
Without a UI, a tenant still needs `curl` or SSH to use any of it. Three concrete gaps:

- There is no way to see which environment variables a site has, or which secrets exist.
- Changing `PHP_MEMORY_LIMIT` for an incident means calling the API by hand.
- Rotating the WordPress salts, which is the safe answer to a suspected session compromise, is
  invisible to the person who needs it.

The screen must make the safe path the easy one: read-only by default, a tiny editable surface,
secret **names** only, and a confirmation before a rotation that logs every WordPress user out.

## WHAT

**A user who can read a site can open a `Configuración` dialog and see its editable environment and
the names of its secrets. A user with `hosting.sites.manage` can change whitelisted values and rotate
the authentication salts, with a confirmation that warns the rotation closes every WordPress
session.**

Falsable today, and today **fails**: the hosting site dialog has no `Configuración` action.

## SCOPE

1. `plugins/hosting/frontend/api.ts`:
   - `HostingSiteConfig` (`site_id`, `container_name`, `editable_env`, `secret_names`,
     `editable_keys`) and `SecretRotationResponse`;
   - `hostingKeys.config(siteId)`;
   - `getHostingSiteConfig`, `updateHostingSiteConfig`, `rotateHostingSiteSalts`.
2. `plugins/hosting/frontend/components/SiteConfigDialog.tsx` (new):
   - `useQuery` enabled by the open site, keyed by `hostingKeys.config`;
   - one input per key in `editable_keys` (a `Select` for the boolean and core-update keys, an
     `Input` for the rest); the value is prefilled from `editable_env`;
   - a secrets section that lists `secret_names` with a lock and “valor oculto”, never a value;
   - `Guardar` sends only the changed, non-empty values; `Rotar sales` opens a confirmation;
   - read-only rendering when the user lacks `hosting.sites.manage` or the site is not managed;
   - after a save, invalidate `hostingKeys.config` and `hostingKeys.sites`.
3. `plugins/hosting/frontend/pages/HostingPage.tsx`:
   - a `Configuración` button in the site detail dialog, disabled when the site is `missing`;
   - `SiteConfigDialog` mounted next to the users and logs dialogs.
4. This document.

## OUT OF SCOPE

- **Backend changes.** A.SPEC 0015 owns the routes, the whitelist and the stable file. The only
  backend change here is the `spanel.managed=false` guard on config writes, which belongs to 0015.
- **Displaying a secret value.** There is no route that returns one, by design.
- **Unsetting a variable.** Empty means "leave it"; removing a key needs a future endpoint.
- **The compose adapter.** The UI talks to the adapter contract and does not care which one runs.
- **New permissions.** `hosting.sites.read` opens the dialog; `hosting.sites.manage` edits it.
- **A general YAML/compose editor.** Full compose stays platform-only and out of reach here.
- **Frontend component tests.** There is no frontend test harness today; the gate is the build.

## CONTRACT

**Preconditions**

- The site is an owned `hosting_sites` row and, for editing, `spanel.managed` is not false.
- The caller has `hosting.sites.read`; editing also needs `hosting.sites.manage`.

**Postconditions**

- Opening the dialog issues one `GET /config` for the selected site and renders the whitelist the
  server returned. Secret values are absent from the DOM and from the network response.
- Without `hosting.sites.manage` (or on a non-managed site) every field is read-only and there is no
  `Guardar` or `Rotar sales`.
- `Guardar` sends only keys whose trimmed value is non-empty and differs from the loaded value. A
  field cleared to empty is not sent, so the server keeps the current value.
- On success, the config and the site list are invalidated; the toast says the change was saved.
- `Rotar sales` requires an explicit confirmation that states every WordPress session closes; the
  request is sent only after the user confirms.
- A 422 from the whitelist (a key the client should not have sent) surfaces as an error toast; no
  field is reset.

**New falsifiable truth**

Open a lab site's detail dialog, click `Configuración`, change `PHP_MEMORY_LIMIT`, save, and see the
new value after the dialog refetches, with the secret names still hidden. Log in as a user without
`hosting.sites.manage`: the same dialog is readable and has no save or rotate controls. Click
`Rotar sales`: the confirmation appears and no request is sent until it is accepted.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      A secret value never reaches the DOM or a network response from this screen.
    proof: >
      The component renders `secret_names` only; the API type has no secret-value field. The backend
      live test `test_get_config_no_devuelve_el_secreto` proves the response has no value.

  - id: I2
    statement: >
      Without `hosting.sites.manage`, or on a site with `spanel.managed=false`, the dialog is
      read-only and offers no write action.
    proof: >
      `editable = canManage && site.managed` gates the inputs and the two write buttons.

  - id: I3
    statement: >
      An empty field is never sent; the client sends only changed, non-empty values.
    proof: >
      `changedEnv` skips empty and unchanged entries (exported pure function).

  - id: I4
    statement: >
      Rotating salts always passes through a confirmation that warns about the logout.
    proof: >
      `saltsOpen` gates the mutation; the confirmation text names the closed sessions.

  - id: I5
    statement: >
      The form is driven by the server's whitelist, not a hardcoded key list.
    proof: >
      The inputs map over `config.editable_keys`; adding a key server-side needs no client change.

  - id: I6
    statement: >
      After saving, the dialog and the list refetch, because the container was recreated.
    proof: >
      Both `hostingKeys.config` and `hostingKeys.sites` are invalidated in `onSuccess`.
```

## VERIFICATION

```bash
npm --prefix apps/web run build
```

The build runs `tsc --noEmit` first, so a broken type fails the gate.

Manual, against the lab:

1. Open a lab site, click `Configuración`: the dialog lists the editable keys and the secret names.
2. Change `PHP_MEMORY_LIMIT`, save: toast “Configuración guardada”, the value is still there after
   the refetch, and the green status returns.
3. Open `Rotar sales`: the confirmation appears; cancel sends nothing.
4. Log in as a tenant user without `hosting.sites.manage`: the dialog is read-only.

| Check | Invariant | What it falsifies |
|---|---|---|
| build | — | the component does not compile |
| manual 1 | I1, I5 | a secret value or a hardcoded list leaks |
| manual 2 | I3, I6 | an empty value is sent, or the list goes stale |
| manual 3 | I4 | a rotation happens without confirmation |
| manual 4 | I2 | a read-only user can write |

## ROLLBACK

`git revert` of the frontend commit. The backend routes stay; removing this UI only hides them. No
data or configuration is affected.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0016-hosting-config-ui.md
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/components/SiteConfigDialog.tsx  # new
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/README.md
  prohibited:
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/hosting/backend/**
    - vendor/systutor-core/plugins/hosting/migrations/**
    - apps/web/src/**
    - docker-compose.yml
    - .env
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - the hosting site detail dialog
    - the new configuration dialog
  indirect:
    - the site list, which refetches after a save
    - the users and logs dialogs, which share the same site detail
  must_not_affect:
    - surface: backend_contratos
      surfaces: [routes, permissions, whitelist]
      invariant: A.SPEC 0015 stays green
    - surface: secretos
      surfaces: [DOM, respuestas]
      invariant: I1
    - surface: separacion_tenant
      surfaces: [apertura del dialogo]
      invariant: the site still comes from the owned row
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0015   # the config and salts routes this UI calls
    - 0013   # the site list and detail dialog
  must_compose_with:
    - 0014   # the site must exist and be managed
  systemic_invariants:
    - A secret value is never rendered.
    - Write actions require both the permission and a managed site.
    - The site identity comes from the owned row, never from an input.
  composition_checks:
    - npm --prefix apps/web run build
    - SPANEL_LIVE_TESTS=1 python3 -m pytest scripts/tests/test_hosting_config_live.py -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one responsibility and one reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/frontend/components/SiteConfigDialog.tsx
```

`HostingPage` stays the dialog hub. `SiteConfigDialog` owns the form and its queries. `api.ts` owns
the transport. No component decides the whitelist; the server does.

## Traceability

- **Requirement**: a tenant can see and change its site's safe configuration and rotate its salts
  from the panel
- **A.SPEC**: this document
- **Code**: `frontend/api.ts`, `frontend/components/SiteConfigDialog.tsx`,
  `frontend/pages/HostingPage.tsx`
- **Migration**: none
- **Test**: `npm --prefix apps/web run build`; manual lab steps; backend live suite from 0015
- **Commit**: `TBD`
- **Deployment**: rebuild the web image; no backend change beyond 0015
- **owner**: `TBD`
- **approver**: `luc444s`

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
