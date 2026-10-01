# A.SPEC 0010 — Que el navegador no autorrellene los formularios de credenciales nuevas

> `risk: low` — cinco archivos de frontend, sin contrato de API, sin migración, sin estado en el
> servidor. El rollback es un revert y el peor caso es que un formulario vuelva a autocompletarse.
> Mode: `extreme-poverty`.

## WHY

Al abrir el modal **"Crear correo"** (`MailAccountsPage.tsx`), el navegador rellena el campo
"Usuario" con el email del administrador y los dos campos de contraseña con la contraseña del
administrador. No es un defecto cosmético.

El modal se montaba así:

```tsx
// MailAccountsPage.tsx:183-193 (antes)
<Input type="text"     value={createUser}     ... placeholder="ventas" autoFocus />
<Input type="password" value={createPass}     ... />
<Input type="password" value={createConfirm}  ... />
```

Para el navegador, un `type="text"` seguido de `type="password"` **es un login**. La heurística de
Chromium no necesita `name` ni `id`: mira la forma del formulario. Y como el shell sí tiene
credenciales guardadas para ese origen —`LoginPage.tsx:86,99` declara `autoComplete="email"` y
`"current-password"`, o sea "este soy yo, acá van mis credenciales"—, el autofill ofrece la cuenta
del administrador dentro de un formulario cuyo propósito es crear una credencial que **no** es del
administrador.

Lo grave no es el ruido. El autofill de Chromium **no siempre dispara el `onChange` de React**, así
que el valor visible en el DOM puede no coincidir con `createUser` / `createPass`. El administrador
ve una contraseña escrita, aprieta "Crear", y se manda la del admin como contraseña del buzón nuevo
—o se manda la vacía y el error parece de validación, que es peor de diagnosticar.

El mismo defecto estaba en **cinco formularios más**, todos de alta o cambio de credenciales:

| Archivo | Campos |
|---|---|
| `plugins/mail/.../MailAccountsPage.tsx:183` | usuario |
| `plugins/mail/.../MailAccountsPage.tsx:189,193` | contraseña, confirmar |
| `plugins/mail/.../MailAccountsPage.tsx:249,253` | contraseña, confirmar (cambio) |
| `plugins/tenant/.../TenantsPage.tsx:189,193` | email, contraseña (crear tenant) |
| `plugins/tenant/.../TenantsPage.tsx:276,280` | email, contraseña (crear usuario) |
| `apps/web/src/features/settings/UsersPage.tsx:303,312` | email, contraseña |
| `shell/src/admin/users.tsx:248,258` | email, contraseña |

Son el mismo bug con distinta etiqueta, y son **formularios gemelos**: el "Cambiar contraseña" de
una casilla tenía exactamente el riesgo del "Crear correo", y salía sin tocar.

### Por qué `autoComplete="off"` no alcanza

Chromium **ignora `off`** en campos `password` dentro de un formulario con forma de login. Es el
caso exacto de los seis formularios de acá. La única señal declarativa que sí respeta es
`new-password`, que le dice "esto se está creando" en vez de "buscá una que ya exista".

Los gestores de contraseñas (1Password, LastPass, Bitwarden) ignoran `autoComplete` por completo y
usan sus propios atributos `data-*`.

### Por qué no se usó el truco de `readOnly`

La primera versión de esta A.SPEC montaba los campos como `readOnly` y les sacaba el atributo en el
frame siguiente. **Se descartó.** Todos estos campos viven dentro de un `Dialog`, y `Dialog` hace
`if (!open) return null` y después `createPortal(...)`: el input **no existe en el DOM** hasta que
el modal abre. El autofill ocurre sobre el nodo recién insertado, cuando `readOnly` ya no está.
El guard sería estado que se suma sin propósito, y `readOnly` además rompe el pegado con el mouse
en algunos navegadores.

## WHAT

Dos objetos de props en `shell/src/ui/input.tsx`, opt-in:

- **`newCredentialProps`** — `autoComplete="new-password"` + `data-1p-ignore`,
  `data-lpignore="true"`, `data-form-type="other"`. Para los campos de contraseña nueva.
- **`noAutofillProps`** — `autoComplete="off"` + los mismos `data-*`. Para el campo de texto que
  acompaña a una contraseña. `off` alcanza acá porque la ignorancia de Chromium ante `off` está
  limitada a los campos `password`.

Se aplican a los seis formularios de la tabla. `Input` ya hace `{...props}`, así que no hubo que
tocar el componente.

Se saca el `autoFocus` del campo password en `MailAccountsPage.tsx:249`: en Chromium dispara la
sugerencia inline en el instante del montaje, que es justo el síntoma reportado, y en iOS además
hace zoom sobre el campo. Se mantiene en los campos de texto, donde solo sugiere y no hay un
password cerca.

En `shell/src/admin/users.tsx` el campo password ya declaraba `autoComplete="new-password"` a mano;
se unifica con el helper para que también lleve los `data-*`.

### Los dos logins quedan fuera, a propósito

`apps/web/src/features/auth/LoginPage.tsx` y `shell/src/admin/login.tsx` **no se tocan**. Ahí el
autocompletado es exactamente lo que se quiere: es un login de verdad, para una persona real, con su
propia credencial. Aplicar el helper ahí rompería el guardado de contraseñas del shell.

Por eso el helper es un objeto que se **importa**, y no un default en `Input`. Un default sería
correcto en cinco de los siete formularios y disastroso en los dos restantes, y esa distinción
debe quedar visible en el sitio donde se decide, no en un default invisible.

La misma regla aplica al futuro: `newCredentialProps` **no** corresponde a un formulario de "cambiar
mi propia contraseña de administrador". Ahí el autocompletado sí es lo que se busca.

## OUT

- Los `input type="text"` sueltos sin password cerca (`TenantsPage:174,178,182,260,284`,
  `MailAccountsPage:183` en su parte de texto) no llevan el helper más allá de lo necesario: sin un
  campo `password` en el formulario, la heurística de login no se dispara. El campo "Usuario" del
  modal de mail sí lo lleva, porque comparte formulario con dos passwords.
- `ProductSearchDialog`, `BranchesPage`, `RolesPage` y el resto de los formularios de búsqueda:
  sin password cerca, sin riesgo.
- Los `Dialog` de dominio y de reasignación en `TenantsPage`: piden un texto, no una credencial.

## VERIFICATION

`tsc --noEmit` en `apps/web` pasa limpio. Los tests de `shell` (`vitest`, 4 tests en
`neofetch.test.ts`) pasan; `confirm.test.ts` falla por resolución de `react` y **ya fallaba antes
de este cambio** — verificado con `git stash`, idéntico.

Verificación manual, en Chromium:

1. Entrar al shell con credenciales guardadas.
2. Abrir cada uno de los seis formularios:
   - Correos → "Agregar correo"
   - Correos → click en una casilla → "Cambiar contraseña"
   - Tenants → "Crear tenant"
   - Tenants → "Usuarios" → "Crear usuario"
   - Usuarios → "Crear usuario" / "Editar usuario"
   - Shell admin → usuarios → alta
3. En todos: campos vacíos, sin sugerencia inline, sin fondo amarillo.
4. Escribir a mano y confirmar que sigue funcionando (el `autocomplete` está desde el primer render,
   no hay frame en el que el campo esté protegido).
5. Regresión del login: entrar al shell y verificar que el navegador **sí** sigue ofreciendo las
   credenciales guardadas.
