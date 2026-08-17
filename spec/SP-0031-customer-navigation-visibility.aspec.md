# A.SPEC SP-0031 — Customer navigation + route visibility

## WHY

Shell actual siempre redirige a `/plugins`, siempre muestra links core
fijos, y arma navegación de plugins consultando `/api/v1/core/plugins`.
Eso sirve para admin, pero customer no debe tener `core.plugin.*` ni ver
`roles`, `users`, `branches`, `infra`; hoy eso termina en 403, pantallas
vacías o callejones sin salida.

## WHAT

Transición observable: navegación, redirects, rutas y acciones visibles se
filtran por `user.permissions` de `/api/v1/auth/me`. Customer aterriza en
primer módulo permitido, no ve secciones prohibidas, y un acceso directo a
ruta no autorizada muestra estado explícito o redirect seguro, nunca una
pantalla en blanco.

## SCOPE

### Shell permission-aware (`apps/web/src/App.tsx`)

- Introducir mapa central de rutas del shell con `requiredAllPermissions`,
  `requiredAnyPermissions` y orden estable para resolver landing page.
- `LoginScreen` y ruta `/` dejan de hardcodear `/plugins`; usan primer
  destino visible para usuario actual.
- Links core fijos se muestran solo si usuario cumple permisos mínimos:
  - `/plugins` → `core.plugin.runtime.read` o `core.plugin.manage`
  - `/roles` → `core.roles.manage` o `core.permission.manage`
  - `/users` → `core.users.read` y ademas `core.roles.read` o
    `core.roles.manage`
  - `/branches` → `core.branches.read` o `core.branches.manage`
- Si usuario no tiene ningun destino visible, renderizar estado explícito
  `Sin modulos habilitados para este usuario` dentro del layout.
- Agregar guard para rutas protegidas y catch-all `*` bajo layout para
  evitar render nulo cuando URL no coincide o no es visible.

### Registro de plugins en frontend (`apps/web/src/plugins.tsx`)

- `usePluginRegistry()` deja de depender de `GET /api/v1/core/plugins`.
- Host importa plugins locales conocidos (`hosting`, `proxy`, `mail`,
  `docker_infra`) y filtra `navigation` + `routes` segun metadata de
  permisos declarada por cada `register.tsx`.
- `PluginNav` y `PluginRoute` agregan `requiredAnyPermissions: string[]`.
- Orden visible del nav plugin se mantiene igual al registro actual:
  `Sitios`, `Dominios`, `Mail`, `Infra`.

### Metadata por plugin (`plugins/*/frontend/register.tsx`)

- `plugins/hosting/frontend/register.tsx`
  - nav `Sitios` requiere alguno de `hosting.sites.read`
  - route `sites` requiere alguno de `hosting.sites.read`
  - route `sites/new` requiere alguno de `hosting.sites.provision`
  - route `sites/:id` requiere alguno de `hosting.sites.read`
- `plugins/proxy/frontend/register.tsx`
  - nav/ruta `Dominios` requiere alguno de `proxy.domains.read`
- `plugins/mail/frontend/register.tsx`
  - nav/ruta `Mail` requiere al menos una de:
    `mail.server.read`, `mail.domains.read`, `mail.mailboxes.read`
- `plugins/docker_infra/frontend/register.tsx`
  - nav/ruta `Infra` requiere alguno de `docker_infra.containers.read`

### Visibilidad de acciones dentro de vistas

- `plugins/hosting/frontend/SitesView.tsx`
  - ocultar `Adoptar` sin `hosting.sites.adopt`
  - ocultar `Nuevo sitio` sin `hosting.sites.provision`
- `plugins/hosting/frontend/SiteDetailView.tsx`
  - ocultar lifecycle sin `hosting.runtime.manage`
  - ocultar logs sin `hosting.runtime.read`
  - ocultar access logs sin `hosting.access.read`
  - ocultar backups create/list segun `hosting.backups.create/read`
  - ocultar file manager sin `hosting.files.manage`
  - ocultar SSO sin `hosting.sso.create`
- `plugins/proxy/frontend/DomainsView.tsx`
  - tabla visible con `proxy.domains.read`
  - botones add/edit/delete segun `proxy.domains.create/update/delete`
- `plugins/mail/frontend/MailView.tsx`
  - status server visible con `mail.server.read`
  - boton provisionar visible con `mail.server.provision`
  - dominios visibles con `mail.domains.read`
  - buzones visibles con `mail.mailboxes.read`
  - acciones add/delete segun permiso mutante correspondiente

### Estados de error no vacios

- `ApiError.status === 403` renderiza estado `No autorizado` con link al
  primer destino visible.
- `ApiError.status === 404` en vistas de plugin renderiza estado
  `Modulo no disponible` para cubrir backend/plugin deshabilitado sin dejar
  pantalla vacía.
- No se usan heurísticas por email, category o nombre de rol; solo permisos
  efectivos del usuario.

## OUT OF SCOPE

- `require_permission(...)` en backend (SP-0030).
- Seed/assignment del rol customer (SP-0032).
- Rediseño visual general del shell.

## CONTRACT

- PRE: `/api/v1/auth/me` retorna `permissions` correctos para usuario
  autenticado.
- POST: customer no ve links a `Plugins`, `Roles`, `Usuarios`, `Branches`
  ni `Infra`; login abre primer modulo permitido.
- POST: si usuario tiene permiso de lectura pero no mutacion, la vista se
  comporta en modo read-only; botones mutantes no aparecen.
- POST: abrir manualmente una URL prohibida produce redirect seguro o estado
  explícito, nunca contenido vacío.

## INVARIANTS

```yaml
invariants:
  - Visibilidad MUST derivarse de `user.permissions`, no de `category` ni de email.
  - Ningun item de nav MUST aparecer si su ruta no puede renderizarse para mismo usuario.
  - Landing page customer MUST NOT depender de `core.plugin.*`.
  - Error 403/404 MUST renderizar estado visible, no `null` ni crash.
  - `docker_infra` MUST seguir invisible para customer.
```

## VERIFICATION

```bash
cd apps/web && npm run build

# UI manual
# 1. login como customer -> entra a primer modulo permitido, no a /plugins
# 2. nav no muestra Plugins/Roles/Usuarios/Branches/Infra
# 3. abrir /plugins manualmente -> redirect seguro o estado "No autorizado"
# 4. abrir /p/docker_infra/containers manualmente -> estado "No autorizado"
# 5. user read-only en domains/mail/sites -> ve listas pero no botones mutantes
```

## ROLLBACK

Revertir guards del shell, metadata `requiredPermissions` y ocultamiento de
acciones. El shell vuelve a navegación fija y carga previa.

## Change Surface

```yaml
change_surface:
  allowed:
    - apps/web/src/**
    - plugins/hosting/frontend/**
    - plugins/proxy/frontend/**
    - plugins/mail/frontend/**
    - plugins/docker_infra/frontend/**
  prohibited:
    - plugins/**/backend/**
    - vendor/systutor-core/**
    - .postgres/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - web.shell.nav
    - web.route_guards
    - plugin.frontend.action_visibility
  indirect:
    - auth.me.permissions_contract
    - customer.first_login_flow
  must_not_affect:
    - backend.permission_enforcement
    - db.spanel
    - plugin.manifest.permissions
```

## Traceability

- Requirement: customer navigation visibility por permisos.
- Inputs: `apps/web/src/App.tsx`, `apps/web/src/plugins.tsx`,
  `plugins/*/frontend/register.tsx`.
- Commit: (pending)

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
