# A.SPEC 0005 — Close the mail dialogs on submit and report through toasts

> `risk: low` — un archivo de frontend, sin contrato de API, sin migración, sin estado en el
> servidor. El rollback es un revert y el peor caso es volver al modal que espera la respuesta.
> Mode: `extreme-poverty`.

## WHY

Los dos modales de `MailAccountsPage` comparten el mismo defecto, y lo tienen **por el mismo
motivo**: esperan la respuesta del servidor para cerrarse, y esconden el error dentro del
modal que se acaba de cerrar.

```ts
// MailAccountsPage.tsx:73-98 (antes)
const changeMutation = useMutation({
  mutationFn: ({ email, password }) => changeMailAccountPassword(email, { password }),
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: mailKeys.accounts });  // ← SSH al mail server
    setSelected(null);                                                    // ← el modal cierra AQUÍ
    ...
    toast.success("Contraseña actualizada");
  },
  onError: (e: Error) => setError(e.message),                             // ← el error vive en el modal
});

const createMutation = useMutation({
  mutationFn: createMailAccount,
  onSuccess: async () => {
    await queryClient.invalidateQueries({ queryKey: mailKeys.accounts });  // ← igual
    setShowCreate(false);                                                 // ← y acá también
    ...
  },
  onError: (e: Error) => setCreateError(e.message),                       // ← y el error también
});
```

Cuatro consecuencias, todas observadas en producción:

1. **El modal se queda abierto durante toda la ida y vuelta.** El proveedor es
   `DockerMailServerProvider`, que habla por **SSH con el contenedor DMS**
   (`settings.py`, `MAIL_USE_SSH=true`). Son segundos en los que el usuario mira un modal
   abierto con un botón "..." que no comunica nada.
2. **El error no se puede leer sin volver a abrir el modal.** Un 403 o un 404 del mail server
   se pintaba en el `<Alert>` de la línea 221, dentro del modal que — por el punto 1 — seguía
   abierto. Cuando por fin se cerraba, el mensaje se iba con él. El usuario solo veía:
   "Error / HTTP 403 al consultar la API", sin contexto y sin poder reintentar sin reescribir
   las dos contraseñas.
3. **El toast de éxito miente sobre el costo.** En ambos modales dice "listo" después de un
   `await invalidateQueries`. En el de contraseña ese refetch no aporta nada: el test
   `test_change_password_does_not_invalidate_cache` (A.SPEC 0001, I6) ya demuestra que cambiar
   una contraseña **no altera la lista de cuentas**. Es un viaje por SSH pagándose en el
   camino crítico del toast, para obtener exactamente los mismos datos.
4. **El resultado no es persistente.** El toast vive 4 segundos; el error no deja rastro
   alguno. Con el modal cerrado, el toast es el único canal — y tiene que ser el canal
   correcto.

El patrón tampoco es único de esta página: `TenantsPage.tsx` tiene la misma división
(`toast.success` para el éxito, `<Alert>` dentro del modal para el error, en `createError`,
`editError`, `newUserError`). El éxito viaja por toast y el error no. Esta A.SPEC arregla los
dos modales de mail, que son los que el usuario reportó; `TenantsPage` queda afuera (ver OUT
OF SCOPE) y merece su propia A.SPEC con su propio criterio.

## WHAT

**Al tocar Guardar o Crear, el modal se cierra en el mismo tick del clic y el resultado
completo — pendiente, éxito y error — se reporta por toast, en un solo toast que se reemplaza
en el lugar.**

Es una verdad, falsable hoy, y hoy **falla**: los dos modales siguen abiertos tras el clic y
un error de servidor muere con el modal.

## SCOPE

1. `plugins/mail/frontend/pages/MailAccountsPage.tsx`:
   - dos constantes de módulo, `CREATE_TOAST` y `PASSWORD_TOAST`, como ids estables de toast;
   - `closePasswordDialog()` y `closeCreateDialog()`: concentrating el cierre y la limpieza de
     campos, hoy repartidos entre `onSuccess` y los tres `onClose` de cada diálogo;
   - ambas mutaciones: `onSuccess` pasa a `toast.success(id, ...)` e `onError` a
     `toast.error(id, e.message)`. Se elimina el `await` del `invalidateQueries` de contraseña
     (no hay nada que invalidar) y el de creación (no se espera al refetch para confirmar);
   - `handleSubmitPassword` y `handleSubmitCreate`: tras las validaciones de cliente,
     `close*Dialog()` + `toast.loading(id, ...)` **antes** de `mutate`, capturando antes los
     valores que el cierre borra;
   - los dos diálogos: `onClose` y el botón Cancelar pasan a usar `close*Dialog()`, y se
     quitan `disabled={isPending}` y el texto `"..."` de los botones de submit, que quedan
     muertos porque el componente se desmonta al enviar.
2. `plugins/mail/frontend/pages/MailAccountsPage.tsx`: import de `toast` desde
   `@systutor/shell/ui/toast` en lugar de `"sonner"`.

## OUT OF SCOPE

- **`TenantsPage.tsx` y el resto de diálogos de la app.** Tienen el mismo patrón y deserve
  su propia A.SPEC: cada uno tiene sus propias validaciones y sus propias preguntas de
  alcance. Acá solo los dos diálogos de mail.
- **No se agrega infraestructura de tests de frontend.** No hay un solo `.test.tsx` en el
  repo, no hay `jsdom` ni `@testing-library/react`, y `apps/web` no tiene config de vitest.
  El testing de esta A.SPEC es **manual** y está escrito como pasos de reproducción, no como
  una suite. Levantar ese harness para cubrir dos `onError` sería desproporcionado; la
  sección VERIFICATION declara qué se cubre y cómo.
- No se cambia el contrato de `POST /accounts` ni de `PUT /accounts/{email}/password`, ni sus
  responses ni sus status codes.
- No se toca `provider.py` ni ningún archivo de backend, ni los tests de A.SPEC 0001.
- No se toca `vendor/systutor-shell`: el `Toaster` de `apps/web/src/app/providers.tsx:8` y el
  re-export de `toast` de `vendor/systutor-shell/src/ui/toast.tsx:26` ya existen. Esta A.SPEC
  los consume, no los construye.
- No se agregan timeouts a las mutaciones. Un toast que queda en "cargando" cuando el servidor
  no contesta es comportamiento heredado de `useMutation` sin `retry` configurado, no algo que
  esta A.SPEC introduzca.

## Restricción de diseño: un solo toast, no dos

El pedido admite dos lecturas: "un toast pending y después otro de éxito", o "un toast que
cambia de estado". Se elige la segunda, con el patrón de id de sonner:

```ts
toast.loading("Cambiando contraseña...", { id: PASSWORD_TOAST });
// ... la mutación resuelve ...
toast.success("Contraseña modificada", { id: PASSWORD_TOAST });   // reemplaza en el lugar
toast.error(e.message, { id: PASSWORD_TOAST });                    // reemplaza en el lugar
```

Motivo: con dos toasts apilados, el de éxito ocupa la fila de arriba y empuja al de error, y el
usuario que llegó tarde ve un éxito sin contexto o un error sin operación. Con un id estable
el toast está en la misma posición de la pantalla durante toda la operación, y el cambio de
estado es perceptible sin leer nada.

Los ids son **constantes de módulo**, no `Math.random()` por clic: dos submits del mismo
flujo no pueden dejar dos toasts huérfanos, y un toast de "cargando" de una operación
anterior no puede ser reemplazado por el de otra.

Hay dos ids y no uno compartido, para que crear un buzón y cambiar una contraseña no se
pisen entre sí: son operaciones distintas, con mensajes distintos, y pueden dispararse desde
páginas distintas de la misma sesión.

### El id va en el objeto `data`, no como primer argumento

Este detalle hizo fallar la primera implementación de esta A.SPEC, y queda escrito porque no
es intuitivo. La firma de sonner es:

```ts
success: (message: titleT | React.ReactNode, data?: ExternalToast) => string | number
```

**No existe el overload `(id, message)`.** El id se lee de `data.id`
(`getToastId(data) = data.id ?? toastsCounter++`). Escribir
`toast.loading(PASSWORD_TOAST, "Cambiando contraseña...")` produce:

- `message = "mail:change-password"` → **el id se muestra como texto del toast**;
- `data = "Cambiando contraseña..."` → se hace `{..."string"}`, o sea claves numéricas
  `0:'C', 1:'r', ...` y **ningún `id`**, así que `getToastId` devuelve un contador nuevo;
- el toast nunca se empareja con su reemplazo: quedan **dos toasts**, ambos mostrando el id
  como texto, y el de tipo `loading` con `duration: Infinity` **dando vueltas para siempre**,
  porque nada lo va a cerrar.

Ese es el síntoma reportado: un toast con el texto `mail:create-account` girando
indefinidamente y, debajo, otro con el mismo texto. Las tres llamadas llevan el id en `data`.

Con el id correcto, `create()` encuentra el toast existente (`alreadyExists`) y fusiona en el
lugar. Y como el cierre automático se decide por tipo
(`if (duration === Infinity || toast.type === 'loading') return;`), al pasar de `loading` a
`success`/`error` el toast vuelve a cerrarse solo a los 4 segundos del Toaster.

## Restricción de diseño: las validaciones de cliente NO cierran el modal

El pedido dice "el modal se cierra apenas tocando el botón". Se aplica **desde el momento en
que la petición se despacha**, no desde el clic.

Los dos handlers tienen guardas que hacen `return` **antes** de `mutate`:

```ts
// cambio de contraseña
if (newPass !== confirmPass) { setError("Las contraseñas no coinciden"); return; }
if (newPass.length < 8)      { setError("Mínimo 8 caracteres"); return; }
// alta de buzón
if (!username)               { setCreateError("Usuario requerido"); return; }
```

Ahí no hubo petición: no hay nada pendiente en el servidor, y el error es de tipeo del propio
usuario. Cerrar el modal en ese caso destruye las dos contraseñas que ya escribió — con un
`<Alert>` dentro del modal los ve en el instante y reintenta; con un toast de 4 segundos tiene
que reabrir y reescribir a mano. El error de validación pertenece al contexto del formulario;
el error de servidor pertenece al canal global.

Consecuencia asumida: `<Alert>` y `toast.error` nunca coexisten en el mismo escenario. Los dos
`<Alert>` que quedan en el archivo pasan a ser exclusivamente para las guardas de cliente.

## Restricción de diseño: el `invalidateQueries` se dispara, no se espera

Los dos refetches quedan, pero ninguno bloquea al usuario:

- **Contraseña**: se elimina. La lista no cambia, ya está demostrado por un test de A.SPEC
  0001. Un refetch por SSH para obtener los mismos datos es trabajo puro.
- **Creación**: se conserva, porque la cuenta nueva tiene que aparecer. Pero deja de hacer
  `await`, así que el toast de éxito no espera al viaje de ida y vuelta al mail server. La
  `accountsQuery` sigue con su `staleTime` de 60s y el refetch en segundo plano no pisa nada.

## CONTRACT

**Precondiciones**

- El usuario tiene `mail.account.password` y/o `mail.accounts.create`. Los
  `require_permission` del router no cambian.
- El modal se abrió con un destinatario válido: en el de contraseña, `selected` no es `null`
  (viene del botón de la fila); en el de creación, `domain` viene de la query.

**Postcondiciones**

- Tras un submit que pasa las validaciones: el modal está cerrado antes del siguiente render,
  hay exactamente un toast con el id del flujo, y la petición se despachó con los valores
  capturados **antes** del cierre.
- Tras un submit que **no** pasa las validaciones: el modal sigue abierto, el `<Alert>` muestra
  el motivo, no hay toast, y no hubo petición.
- Tras éxito: el toast con ese id dice "Contraseña modificada" o "Cuenta creada", y en el caso
  de la creación la lista se refresca sin bloquear la confirmación.
- Tras error de servidor: el toast con ese id dice el `message` del `ApiError` (el `detail` de
  la API), el mismo string que antes se pintaba en el modal.
- Cerrar un modal sin enviar (botón Cerrar, Cancelar, o clic fuera) limpia sus campos: el
  modal nunca se reabre con datos de un intento anterior.

**Verdad nueva, falsable**: hacer clic en Guardar o Crear con datos válidos cierra el modal en
el mismo gesto, y la resolución aparece en un toast arriba a la derecha. Antes: el modal
seguía abierto durante el viaje al servidor y el error se perdía con él.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      Las validaciones de cliente no cierran el modal, no disparan toast y no generan
      petición, en ninguno de los dos flujos. "No coinciden", "Mínimo 8 caracteres" y
      "Usuario requerido" se siguen viendo dentro del modal.
    proof: >
      Las guardas de handleSubmitPassword y handleSubmitCreate conservan su return antes de
      close*Dialog() y antes de mutate(), y los dos <Alert> se mantienen.

  - id: I2
    statement: >
      Una vez despachada la petición, el modal se cierra sin esperar la respuesta: no hay
      ningún await entre el submit y close*Dialog().
    proof: >
      Los dos handlers son síncronos hasta mutate(). El cierre se movió desde onSuccess —donde
      estaba detrás de un await— al submit. Grep: close*Dialog() aparece en los dos handlers
      y no dentro de ningún onSuccess.

  - id: I3
    statement: >
      Hay exactamente un toast por submit, y se reemplaza en el lugar. Para que eso ocurra el
      id viaja en el objeto data de las tres llamadas —loading, success y error— y nunca como
      primer argumento. Los ids son constantes de módulo distintas por flujo, no ids nuevos
      por click ni un id compartido entre crear y cambiar contraseña.
    proof: >
      grep -nE "toast\.(loading|success|error)\((CREATE_TOAST|PASSWORD_TOAST)" debe
      devolver vacío: esa es exactamente la forma que produce dos toasts, uno de ellos girando
      para siempre. En su lugar, cada llamada es toast.x(<mensaje>, { id: <ID> }). No hay
      ninguna llamada a toast sin data en el archivo.

  - id: I4
    statement: >
      La contraseña nunca aparece en un toast, ni pendiente, ni de éxito, ni de error, en
      ninguno de los dos flujos.
    proof: >
      Los cuatro mensajes son literales constantes o e.message, que es el detail de la API.
      Ningún detail de estos dos endpoints incluye la contraseña.

  - id: I5
    statement: >
      La creación de una cuenta sigue refrescando la lista. El refetch se conserva porque la
      cuenta nueva tiene que aparecer; lo que cambia es que ya no bloquea al toast de éxito.
    proof: >
      createMutation sigue llamando invalidateQueries({ queryKey: mailKeys.accounts }). El
      refetch se dispara con void, sin await, y la accountsQuery conserva su staleTime.

  - id: I6
    statement: >
      Un cambio de contraseña NO refetchea la lista de cuentas. El refetch por SSH se quita
      del camino crítico del toast.
    proof: >
      changeMutation no contiene invalidateQueries. El porque ya estaba demostrado en A.SPEC
      0001 I6 con test_change_password_does_not_invalidate_cache: la lista que devuelve el
      proveedor no cambia al cambiar una contraseña.

  - id: I7
    statement: >
      Ningún cambio de backend. POST /accounts y PUT /accounts/{email}/password quedan como
      estaban, incluido el 403 domain_mismatch y el 422 de formato.
    proof: >
      change_surface no incluye archivos bajo backend/. La suite del kernel corre igual:
      95 passed, 30 passed en plugins/mail.

  - id: I8
    statement: >
      No se pierde información: el texto del toast de error es el mismo string que el modal
      mostraba antes (ApiError.message, derivado de detail).
    proof: >
      onError pasa de setError(e.message) / setCreateError(e.message) a toast.error(id,
      e.message). El <Alert> de servidor desaparece porque el modal se cerró, no porque el
      mensaje se degrade.

  - id: I9
    statement: >
      No queda estado muerto: los botones de submit de los dos diálogos no consultan
      isPending, porque el componente se desmonta al enviar.
    proof: >
      grep de isPending en MailAccountsPage.tsx no devuelve resultados. Ningún otro consumidor
      de esas dos mutaciones quedó sin su guarda.

  - id: I10
    statement: >
      Los valores despachados son los que el usuario escribió, no los que dejó el cierre.
      Cerrar el diálogo limpia los campos, así que leer el estado después de close*Dialog()
      mandaría cadenas vacías al backend.
    proof: >
      handleSubmitPassword captura `const email = selected` y handleSubmitCreate captura
      `username` y `password` en locales antes de close*Dialog(), y mutate() usa esas locales.
      No queda ningún `selected!` ni lectura de estado de campo después del cierre.
```

## VERIFICATION

**El testing de esta A.SPEC es manual.** Declarado explícitamente: no se agregan tests
automatizados. El repo no tiene ningún `.test.tsx`, ni `jsdom`, ni `@testing-library/react`;
`apps/web/package.json` declara un script `test` que hoy no encuentra ningún archivo. La
verdad de I1-I3 e I9-I10 se demuestra por inspección estructural (greps, cada uno falsable)
y por la reproducción manual de abajo.

**Gate**

```bash
cd apps/web && npm run build     # ejecuta tsc --noEmit + build de vite
```

**Proofs de falsabilidad por inspección** (cada grep falla si la A.SPEC está a medias)

```bash
cd vendor/systutor-core/plugins/mail/frontend/pages

# I3: un id por flujo, tres estados cada uno
grep -n "CREATE_TOAST\|PASSWORD_TOAST" MailAccountsPage.tsx   # 2 declaraciones + 3 + 3 usos

# I3: la forma prohibida — el id NUNCA es el primer argumento
grep -nE "toast\.(loading|success|error)\((CREATE_TOAST|PASSWORD_TOAST)" MailAccountsPage.tsx
# debe salir vacío. Si no sale, aparecen dos toasts y uno gira para siempre.

# I2: el cierre esta en los submits, no en los onSuccess
grep -n "closePasswordDialog()\|closeCreateDialog()" MailAccountsPage.tsx

# I6 / I5: una mutacion invalida, la otra no
grep -n -A 8 "const changeMutation" MailAccountsPage.tsx   # sin invalidateQueries
grep -n -A 8 "const createMutation" MailAccountsPage.tsx   # con invalidateQueries

# I9: sin estado muerto
grep -n "isPending" MailAccountsPage.tsx                   # vacio

# I10: sin lectura de estado despues del cierre
grep -n "selected!" MailAccountsPage.tsx                   # vacio
```

**Reproducción manual — cambio de contraseña**

Levantar `PORT=8001 npm run services` y `SYSTUTOR_API_PORT=8001 npm run frontend`.

1. **Éxito** — abrir el modal de `t2@spanel-test.local`, escribir dos contraseñas coincidentes
   de 8+ caracteres, Guardar. El modal desaparece en el gesto; arriba a la derecha aparece
   "Cambiando contraseña..." y ese mismo toast se reemplaza por "Contraseña modificada". En
   Network: un `PUT`, y **ningún** `GET` de cuentas después.
2. **Error de servidor** — mismo flujo contra una cuenta que no existe en el DMS (404
   `Account not found: ...`) o con el host del mail server inalcanzable (503 `Mail server
   unavailable`). Toast de error con ese texto, sin modal, y sin toasts colgados en
   "cargando".
3. **Validación** — dos contraseñas distintas. El modal **sigue abierto** con el `<Alert>` "Las
   contraseñas no coinciden", no hay toast, y en Network no hay `PUT`.

**Reproducción manual — alta de buzón**

4. **Éxito** — Abrir "Agregar correo", usuario nuevo y dos contraseñas coincidentes, Crear. El
   modal desaparece en el gesto, "Creando cuenta..." se reemplaza por "Cuenta creada", y el
   buzón nuevo aparece en la grilla cuando el refetch vuelve (no bloquea la confirmación).
5. **Error** — repetir con un usuario que ya existe en el DMS (409 `Account already exists:
   ...`). Toast de error con ese texto, sin modal.
6. **Validación** — usuario vacío, o contraseñas distintas, o menos de 8 caracteres. El modal
   **sigue abierto** con su `<Alert>`, sin toast y sin `POST`.

**Comprobación de que el toast es el mismo, no dos**: en los casos 1 y 4, tapar con el mouse
sobre la esquina superior derecha durante la operación. La fila no se desplaza ni aparece una
segunda fila: el texto pending se sustituye en el mismo sitio. **Si aparece un toast que
muestra el texto `mail:create-account` o `mail:change-password`, o si el spinner no se
detiene, el id está pasando como primer argumento** — es la forma prohibida de I3.

**Lo que esta A.SPEC NO prueba**, declarado explícitamente: no hay medición de latencia
antes/después. El host del DMS no es alcanzable desde el entorno de desarrollo, así que la
mejora de percepción es consecuencia mecánica de I2 (dejar de esperar el SSH para cerrar), I5
y I6 (dejar de pagar refetches inútiles antes del toast); los milisegundos no se afirman aquí.

## ROLLBACK

`git revert <sha>`. Sin migración, sin cambio de esquema, sin estado en el servidor. El
usuario vuelve al modal que espera la respuesta y muestra el error adentro. Nada que deshacer
del lado del mail server: una contraseña cambiada con éxito sigue cambiada, y un buzón creado
sigue creado.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
    - aspecs/0005-optimistic-close-password-modal.md
  prohibited:
    - vendor/systutor-core/plugins/mail/backend/**          # I7
    - vendor/systutor-core/plugins/mail/frontend/api.ts      # I7: contrato intacto
    - vendor/systutor-core/plugins/tenant/frontend/**        # otro flujo, otra A.SPEC
    - vendor/systutor-shell/**                               # toast ya existe y se reusa
    - apps/web/src/**
    - vendor/systutor-core/src/**
```

`vendor/systutor-shell` está prohibido a propósito: el `Toaster` de `providers.tsx:8` y el
re-export de `toast` de `ui/toast.tsx:26` ya existen y esta A.SPEC los consume. Lo único que
cambia del import es dejar de importar `sonner` directo, para que la configuración del toast
tenga un solo dueño.

## Blast Radius

```yaml
blast_radius:
  direct:
    - MailAccountsPage.tsx (un archivo, ~60 lineas touched)
    - Los dos unicos puntos de la app que usaban el modal como canal de error
  indirect:
    - El <Alert> de la linea 221 y su gemelo del dialogo de creacion sobreviven, pero solo
      para validaciones de cliente (I1)
    - Cerrar un modal sin enviar ahora limpia los campos: el modal ya no se reabre con datos
      de un intento anterior (contract, postcondicion 5)
    - La cuenta creada aparece por el refetch en segundo plano, no por el await (I5)
  must_not_affect:
    - surface: mail_api_contract
      surfaces:
        - backend/router.py
        - backend/service.py
        - backend/provider.py
        - frontend/api.ts
      invariant: I7
    - surface: accounts_list_query
      surfaces: [mailKeys.accounts, accountsQuery, ACCOUNTS_STALE_TIME_MS]
      invariant: >
        I5 + I6 — la query conserva su staleTime y la de creación conserva su invalidación.
        Lo único que se quita es el invalidate de una mutación que no cambia la lista.
    - surface: mail_permissions
      surfaces: [backend/router.py require_permission]
      invariant: I7
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with:
    - A.SPEC 0001   # I6 se apoya en su I6: la lista no cambia al cambiar una contraseña
  systemic_invariants:
    - El Toaster de providers.tsx se monta una sola vez; esta A.SPEC no monta ninguno propio.
    - Los toasts del shell se siguen configurando en vendor/systutor-shell/src/ui/toast.tsx.
    - La invalidación de permisos de A.SPEC 0003 no toca esta página.
  composition_checks:
    - cd apps/web && npm run build
    - cd vendor/systutor-core && python3 -m pytest tests plugins/mail/backend/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/mail/frontend/pages/MailAccountsPage.tsx
```

`MailAccountsPage.tsx` queda en ~255 líneas, por debajo del umbral de revisión: no se extrae
componente. La nueva lógica son dos ids constantes y dos funciones de cierre de 5 líneas; no
aparece una capa de abstracción ni un hook nuevo.

## Traceability

- **Requirement**: los modales de buzón no deben bloquear la pantalla durante la ida y vuelta
  al mail server, y el resultado —incluido el error— debe quedar visible después de que el
  modal se cierre
- **A.SPEC**: este documento
- **Code**: `plugins/mail/frontend/pages/MailAccountsPage.tsx` (`changeMutation`,
  `createMutation`, `handleSubmitPassword`, `handleSubmitCreate`, `closePasswordDialog`,
  `closeCreateDialog`, los dos `Dialog`)
- **Migration**: ninguna
- **Test**: ninguno nuevo; el testing es manual (ver VERIFICATION). La cobertura existente de
  A.SPEC 0001 corre sin cambios: 30 passed en `plugins/mail`, 95 passed en el kernel
- **Commit**: `TBD`
- **Deployment**: rebuild de imagen. Sin paso manual, sin migración, sin tocar datos

- **owner**: `TBD`
- **approver**: `TBD`

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established
