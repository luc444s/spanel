# A.SPEC 0062 — English-first con alternativa en español

> `risk: medium` — toca todo el texto visible del frontend, la documentación de
> operador, los mensajes de los scripts y el contrato de errores de la API.
> No cambia modelos, permisos, endpoints ni lógica de negocio.

## WHY

El panel, su documentación y sus herramientas de operador están escritos
principalmente en español, con textos en inglés mezclados. Eso impide que un
operador o cliente que no lee español use SPanel y obliga a traducir de forma
informal cada nueva pantalla.

El objetivo es que **el inglés sea el idioma fuente** y que el español siga
disponible como alternativa completa, sin mantener dos copias del código.

## SCOPE

### 1. Frontend — inglés por defecto, español opcional

- Idioma por defecto: `en`. No se detecta el navegador; el operador cambia a
  español desde el selector del sidebar.
- La preferencia se guarda en `localStorage["spanel.locale"]` y sobrevive a
  recargas.
- Sin rutas `/en` ni `/es` y sin cambios de URL: es un panel administrativo, no
  necesita SEO.
- Todo texto visible se traduce: títulos, navegación, labels, placeholders,
  botones, diálogos, tablas, estados vacíos, errores, toasts y `aria-label`.
- Los textos de los frontends de plugins también se traducen. Los plugins se
  compilan dentro del bundle, así que comparten el mismo proveedor.

### 2. Catálogo

El inglés es la clave. `t("Save changes")` devuelve la clave tal cual cuando el
idioma es `en` y busca la traducción en el catálogo español:

```ts
// apps/web/src/i18n/es.ts
export default {
  "Save changes": "Guardar cambios",
  "Delete {count} users": "Eliminar {count} usuarios",
};
```

Reglas:

- Claves en inglés, frases completas y estables.
- Interpolación con `{name}`; sin pluralización automática. Cuando el número
  cambie la frase, se usa una clave distinta por variante.
- Si falta una traducción, se muestra el inglés. Nunca se rompe la UI.
- El catálogo se divide por dominio (`es/core.ts`, `es/hosting.ts`, …) para que
  varios cambios no toquen el mismo archivo.

### 3. Errores de la API

El kernel ya responde:

```json
{"error": {"code": "hosting_backup_runtime_unavailable", "message": "..."}}
```

- `ApiError` conserva `code`.
- El frontend registra un localizador de errores: `code` → mensaje inglés →
  catálogo español.
- Si el `code` no está mapeado, se muestra el `message` del servidor como
  fallback.
- Los literales del backend se normalizan a inglés; no se mantiene un catálogo
  Python paralelo.

### 4. Documentación

- `README.md` y `scripts/README.md` en inglés.
- Equivalentes `README.es.md` y `scripts/README.es.md` en español.
- `docs/` en inglés con equivalentes `*.es.md`.
- `aspecs/` **no se traduce**: es registro histórico interno y se conserva tal
  cual.
- Comentarios y docstrings de código **no se traducen** como parte de esta spec.

### 5. Scripts

- `description=`, `help=` y mensajes visibles de CLI en inglés.
- Sin `gettext` ni selector de idioma en runtime: los scripts son herramientas
  de operador y su documentación sí es bilingüe.
- Los identificadores, flags y códigos de salida no cambian.

## DEPENDENCY

No crear:

- una librería i18n externa;
- rutas localizadas ni middleware de locale;
- catálogos duplicados en Python;
- un segundo sistema de errores;
- traducciones de aspecs, comentarios o docstrings.

## VALIDATION

- `npm run build` (incluye `tsc --noEmit`) y `npm test` en `apps/web`.
- `pytest` de los plugins tocados.
- `ruff check` sobre los archivos Python modificados.
- Prueba manual: cargar con `en`, cambiar a `es`, recargar y verificar que se
  conserva; los errores de API cambian de idioma sin recargar datos.
- `rg` final: no queda texto visible en español fuera del catálogo `es/`.

## OUT OF SCOPE

- Traducir `aspecs/`.
- Traducir comentarios, docstrings, nombres de variables o mensajes de log.
- Localización en servidor por `Accept-Language` para clientes que no son la
  web. Queda como deuda si se necesita.
- Contenido generado por usuarios o tenants.
