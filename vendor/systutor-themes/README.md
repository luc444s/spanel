# systutor-themes

Sistema de temas de SYSTUTOR: tokens atómicos por tema (TS puro), generador
de CSS custom properties en runtime y store zustand para seleccionar el tema.

Un archivo por tema (`light`, `dark`, `retro`, `catpuccin_mocha`, `nord`,
`nord_dark`); editar un tema no toca los otros. Contrato completo en
`SPEC-ADD/ui/UI-THEMES-002.md` del repo principal.

## Uso

```ts
import { themes, THEME_NAMES, useThemeStore } from "@systutor/themes";
```

El host aplica la clase del tema en `document.documentElement` y mapea los
colores semánticos Tailwind a las CSS variables inyectadas.

## Licencia

MIT — ver [LICENSE](./LICENSE).
