import { InputHTMLAttributes, forwardRef } from "react";
import { cn } from "./cn";

export { Checkbox } from "./checkbox";
export { Switch } from "./switch";
export { Textarea } from "./textarea";

// Props para campos de CONTRASEÑA NUEVA: alta de casillas, alta de usuarios, alta de tenants.
//
// El problema que resuelve: un dialog con `type="text"` + `type="password"` tiene, para el
// navegador, la FORMA de un login. La heurística de Chromium no necesita `name` ni `id`, solo ve
// la forma, y ofrece las credenciales que el usuario ya tiene guardadas para este origen — o sea
// las del admin — dentro de un formulario cuyo propósito es justamente crear una credencial que
// no es del admin. Lo grave no es el ruido: el autofill de Chromium no siempre dispara el
// onChange de React, asi que el valor visible en el DOM puede no coincidir con el estado, y se
// acaba mandando la contraseña del admin como contraseña del buzón.
//
// `autoComplete="off"` solo NO alcanza: Chromium lo ignora en campos password dentro de
// formularios con forma de login. `new-password` es la unica senal declarativa que si respeta, y
// le dice "esta es una credencial que se esta creando", no "busca una que ya exista".
//
// Los `data-*` son para los gestores de contrasenas (1Password, LastPass, Bitwarden), que ignoran
// `autoComplete` por completo y son igual de molestos.
//
// NO usar en un formulario de "cambiar mi propia contrasena de admin": ahi el autocompletado si
// es lo que se quiere. Por eso esto se aplica importandolo, y no como default en `Input`.
export const newCredentialProps = {
  autoComplete: "new-password",
  "data-1p-ignore": "",
  "data-lpignore": "true",
  "data-form-type": "other",
} as const;

// Props para el campo de texto que acompaña a una contrasena nueva.
//
// No es un email, asi que no corresponde "email" ni "username": `off` alcanza aca, porque la
// ignorancia de Chromium ante `off` esta limitada a los campos password.
export const noAutofillProps = {
  autoComplete: "off",
  "data-1p-ignore": "",
  "data-lpignore": "true",
  "data-form-type": "other",
} as const;

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          "w-full rounded-md border border-input bg-surface px-3 py-2 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring",
          className
        )}
        {...props}
      />
    );
  }
);
