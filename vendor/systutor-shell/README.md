# systutor-shell

Frontend core de SYSTUTOR. Componentes UI genericos, consola operativa (Monaco) y clientes de infraestructura HTTP.

**Sin logica de negocio**: todo lo que vive aca es reutilizable por cualquier plugin o aplicacion del ecosistema SYSTUTOR. Reglas de dominio, payload builders y wrappers finos viven en los plugins.

## Estructura

```text
src/
  ui/          componentes genericos (button, dialog, data-table, combobox...)
    map/       componentes de mapa (Leaflet): location-map, location-picker, location-search
    calendar/  resource-calendar: scheduler por recursos con callbacks
    console/   consola operativa: Monaco editor + shell de comandos
  api/         client HTTP, tipos de la API kernel (UserProfile, CoreRoleRead,
               CoreBranchRead, PluginRuntimeRecord...) y helpers de infraestructura
  auth/        token JWT: getToken, setToken, clearToken, initAuth
  admin/       vistas de consola del kernel: login, logout, plugins, roles,
               usuarios, branches (reutilizables por cualquier host)
  confirm.ts   helper de confirmacion de dialogos
  neofetch.ts  fetch de informacion del sistema
```

## Vistas admin

Consola de gestion del kernel SYSTUTOR, generica (cero logica de negocio).
El host compone rutas, menu y branding; las vistas se importan del barrel:

```ts
import { Login, LogoutButton, PluginsView, RolesView, UsersView, BranchesView } from "@systutor/shell";
```

| Vista | Rutas API kernel que consume |
|-------|------------------------------|
| `Login` | `POST /api/v1/auth/login` (persiste JWT via `setToken`) |
| `LogoutButton` | cliente-side: `clearToken()` + callback |
| `PluginsView` | `GET /core/plugins`, `POST /{id}/install\|enable\|disable\|migrate\|uninstall` |
| `RolesView` | `GET/POST/PATCH /core/roles`, `POST /{id}/enable\|disable`, `GET /core/permissions` |
| `UsersView` | `GET/POST/PATCH /core/users`, `GET /users/categories`, `POST /{id}/enable\|disable` |
| `BranchesView` | `GET/POST/PATCH /core/branches`, `POST /{id}/enable\|disable` |

Auth helpers (`systutor.auth`):

```ts
import { initAuth, setToken, clearToken, getToken } from "@systutor/shell";

initAuth();                       // restaura token de localStorage al client
setToken(accessToken);            // persiste + actualiza setTokenProvider
clearToken();                     // logout cliente-side (JWT stateless)
```

`Login` ya hace `setToken` internamente; el host solo decide que renderiza
despues de `onLogin(user)`.

## Consumo

Via submodule + alias del bundler (patron del repo principal):

```ts
// vite.config.ts
resolve: { alias: { "@systutor/shell": path.resolve(__dirname, "vendor/systutor-shell/src") } }

// tsconfig.json
"paths": { "@systutor/shell/*": ["./vendor/systutor-shell/src/*"] }

// tailwind.config.ts — obligatorio: sin esto, Tailwind purga las clases
// que solo viven en el shell (dialog, data-table, bordes) y la UI se rompe.
content: ["./src/**/*.{ts,tsx}", "./vendor/systutor-shell/src/**/*.{ts,tsx}"],
```

Tailwind v4: misma regla via `@source` en el CSS del host (path relativo al
archivo CSS):

```css
@import "tailwindcss";
@source "../vendor/systutor-shell/src";
```

```ts
import { Button } from "@systutor/shell/ui/button";
import { apiRequest } from "@systutor/shell/api/client";
```

## Requisitos de componentes

1. Generico primero: si un componente sirve solo a un plugin, no va aca.
2. Testeable sin dependencias pesadas (evitar Tooltip crudo si rompe `renderToStaticMarkup`; preferir atributos nativos).
3. Cero logica de negocio: nada de reglas de dominio, permisos ni flujos de un plugin.

## Licencia

MIT
