# A.SPEC 0043 — Mutaciones instantáneas en modales de Hosting (cerrar y continuar)

> `risk: normal` — 100% frontend del plugin hosting. Sin contrato HTTP, sin
> backend, sin migración, sin datos. Rollback: revert del commit.

## WHY

En Hosting, "Crear sitio" mantiene el modal abierto hasta que el POST del
backend resuelve — y ese POST bloquea hasta que el contenedor provisiona
(WordPress puede tardar demasiado). El usuario percibe el panel como lento
aunque el trabajo sea necesario.

En Correos la experiencia ya es la correcta: cerrar/reaccionar de una y el
resultado llega después (A.SPEC 0038/0039: alta optimista + sin refetch
bloqueante; toast inmediato de contraseña). El efecto deseado aquí es el
mismo feeling, pero logrado **sin tocar nada del backend**: el patrón

```
Click "Crear"  →  el modal se cierra EN el submit
              →  toast persistente arriba a la derecha: "Creando sitio…"
              →  el POST sigue en background (react-query)
              →  resuelve → toast final ("Sitio creado" / error)
              →  la lista se refresca detrás del usuario
```

La infraestructura ya existe: `sonner` (usado por `@systutor/shell/ui/toast`,
posición `top-right`) trae `toast.promise(promise, {loading, success,
error})`, que cubre el estado pending (spinner), el éxito y el fallo con un
lugar canonico para cada uno. No se necesita componente nuevo ni hook
complejo: cambiar el flujo de cada modal para llamar a `toast.promise` con la
promesa de la mutación.

## WHAT (solo frontend del plugin hosting)

1. **Crear sitio** (`HostingPage.tsx` / `CreateSiteDialog.tsx`): el submit
   cierra el modal y llama `toast.promise(createMutation.mutateAsync(payload),
   {loading: "Creando sitio…", success: "Sitio creado", error:
   "No se pudo crear el sitio"})`. La invalidación de `hostingKeys.sites`
   ocurre en `onSuccess` como hoy (detrás del usuario). El dialog de
   contraseña/URL de WordPress se muestra igual cuando la respuesta la trae.
   El mensaje final de éxito incluirá el dominio cuando se conozca.
2. **Perfiles** (`ProfilesDialog.tsx`):
   - Crear/editar: cierra el formulario (no el gestor) en el submit y
     `toast.promise` con `"Guardando perfil…"` → `"Perfil guardado"` /
     error con el mensaje de la API (422 incluido).
   - Eliminar: tras la confirmación por id, cierra el dialog y
     `"Eliminando perfil…"` → `"Perfil eliminado"` / error (409 in-use o
     builtin, el mensaje real del backend).
   - El gestor (listado) sigue refrescando por invalidación al resolver.
3. **Operaciones** (`HostingPage.tsx` dialog start/stop/restart): cierra el
   dialog de confirmación en submit y `"Aplicando <acción>…"` → `"Sitio
   <acción> exec"` / error. Invalidez refresca el listado igual que hoy.
4. **Errores**: SIEMPRE toast de error con el mensaje del backend tal como
   llega (`error.message`), nunca silencios. El usuario no pierde el motivo
   al cerrar el modal, porque el toast es global (Toaster en top-right).
5. **Nada más cambia**: tipos de api.ts intactos, backend intacto, usuarios
   y roles se quedan como están (explícitamente out of scope), Correos
   intacto.

## SCOPE

Entra:
- `vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx`
- `vendor/systutor-core/plugins/hosting/frontend/components/CreateSiteDialog.tsx`
- `vendor/systutor-core/plugins/hosting/frontend/components/ProfilesDialog.tsx`
- `aspecs/0043-instant-modal-mutations-hosting.md`

No entra:
- Cualquier backend (`plugins/hosting/backend/**`), contratos, migraciones.
- Correos (ya tiene el patrón), users, roles, branches, settings pages.
- Placeholder optimista en tarjetas (fase 2 futura si se pide).
- Plataforma shell (`vendor/systutor-shell/**`): `sonner`/`toast.promise`
  ya disponible; no se modifica la Toaster.
- Tests de UI: la verificación es `tsc --noEmit` + suites backend verdes sin
  cambios (mismo resultado preexistente).

## CONTRACT

Precondiciones:
- A.SPEC 0041/0042 UI vigente (diálogos con createMutation y operating
  diálogos ya funcionando contra la API actual).

Postcondiciones:
1. En cualquier pantalla de la lista definida arriba, hacer submit de breve
   o larga duración NUNCA deja el usuario mirando un modal bloqueado más
   allá del instante del clic (feeling de respuesta instantánea).
2. El toast pending se muestra de inmediato con `toast.promise`.
3. Éxito/fallo arbritan el único mensaje final; error conserva el mensaje
   real del servidor.
4. La UI (listado) muestra el resultado final tras el resolve (invalidación),
   sin refetch bloqueante en pantalla.
5. Doble submit se Evita porque el botón se deshabilita en pending igual
   que hoy (React Query `isPending`).

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El backend no cambia; los contratos HTTP y códigos son los de 0041/0042.
    proof: diff sin archivos de backend; suites pytest de hosting verdes sin modificar tests.
  - id: I2
    statement: Un fallo de red o 4xx tras cerrar el modal se muestra en toast, nunca silencio.
    proof: revisión de onError de cada mutante + toast.promise error branch.
  - id: I3
    statement: La invalidación de queries existentes no desaparece; el listado sigue siendo reflejo del backend.
    proof: onSuccess de las mutaciones mantiene invalidateQueries como hoy.
  - id: I4
    statement: Correos, users y roles no cambian de comportamiento.
    proof: fuera del change_surface; sin tocar esos archivos.
```

## VERIFICATION

- `cd apps/web && node node_modules/typescript/bin/tsc --noEmit`
- `PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q` — mismas pruebas, mismo resultado (I1).
- Revisión manual del flujo del tal: crear sitio → cerrar instantáneo → toast pending → toast final.

## ROLLBACK

`git revert <sha>`. Sin estado persistente, sin migraciones.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/frontend/components/CreateSiteDialog.tsx
    - vendor/systutor-core/plugins/hosting/frontend/components/ProfilesDialog.tsx
    - aspecs/0043-instant-modal-mutations-hosting.md
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/**
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-shell/**
    - vendor/systutor-core/plugins/mail/**
    - apps/web/src/features/settings/**   # users/roles quedan como están
    - docker-compose.yml
    - scripts/**
```

## Traceability

- Requirement: que crear un sitio se sienta tan instantáneo como crear un
  buzón en Correos, sin tocar el backend, y que el patrón sea repetible para
  cualquier modal futuro de cambios (convención: cerrar en submit +
  `toast.promise`).
- owner: luc444s
- Deployment: rebuild de la imagen web (bundle de plugins se compila junto).

## Definition of Done

- [ ] Crear sitio cierra el modal en el submit y muestra toast pending/final.
- [ ] Perfil crear/editar/eliminar con el mismo flujo y error visible.
- [ ] Iniciar/detener/reiniciar vía dialog con el mismo flujo.
- [ ] Error siempre con el mensaje del backend en toast.
- [ ] `tsc --noEmit` verde y suites del backend hosting inalteradas (verdes).
