# A.SPEC 0002 — Make the ruff gate green

> `risk: low` — reversible, sin runtime, sin esquema, sin migración. Cambia config de linter,
> orden de imports y 3 imports muertos. Proofs deterministas. Mode: `mechanical`.

## WHY

`ruff check .` falla con **51 errores** en un árbol sin modificar. El gate de lint está rojo
desde el día uno, y un gate siempre rojo entrena a todos a ignorarlo: está muerto.

La causa raíz es que **63% del ruido es un path mal escrito**:

| Regla | N | Qué es |
|---|---|---|
| B008 | 32 | `Depends()` en defaults — el idioma de FastAPI |
| I001 | 12 | imports sin ordenar |
| F401 | 3 | imports sin usar |
| E501 | 3 | líneas de 101 chars (límite 100) |

`pyproject.toml` declara `"../../plugins/*/backend/router.py" = ["B008"]`, pero los patrones
de `per-file-ignores` se resuelven relativos a la carpeta del config, que es
`vendor/systutor-core/`. `../../` apunta a `mailadmin/plugins/`, que no existe:

```
$ ls -d ../../plugins
ls: cannot access '../../plugins': No such file or directory
```

El patrón no matchea nada, así que la exención nunca aplicó. El autor quiso eximir el idioma
correcto de FastAPI y la config dejó de hacerlo en silencio.

Consecuencia medida: los 3 imports muertos y las 3 líneas largas conviven con 32 falsos
positivos **porque nadie estaba mirando**. El ruido protegen al bug real de la vista.

## WHAT

`ruff check .` reporta **0 errores** en el árbol.

## SCOPE

1. `pyproject.toml`: borrar el patrón `per-file-ignores` roto; agregar el correcto para
   `plugins/tenant/backend/router.py`; agregar `[tool.ruff.lint.flake8-bugbear]
   extend-immutable-calls`.
2. `ruff check . --fix` para los 15 errores auto-corregibles (12 I001 + 3 F401).
3. Envolver manualmente las 3 líneas de 101 chars en
   `migrations/versions/20260708_0008_core_documents_signatures.py`.

## OUT OF SCOPE

- Reordenar el resto del repo por gusto, o formatear con `ruff format`.
- Agregar `plugins` al `include` de pyright. Es otro bug de cobertura — pyright no type-checkea
  `plugins/**` — pero arreglarlo puede exponer errores de tipos nuevos. A.SPEC aparte.
- Cambiar `line-length` de 100 para hacer callar los E501.
- Agregar reglas nuevas a `select`.

## CONTRACT

**Precondición**: el árbol está en `90a0a46` (A.SPEC 0001 commiteada), lint en 51.

**Postcondición**: `ruff check .` reporta `All checks passed!`, y `pytest` sigue en 85 passed.

**Verdad nueva, falsable**: `ruff check .` devuelve 0, y la suite completa no cambió de
conteo. Cualquiera de las dos que falle es FAIL.

## Por qué esta configuración y no `ignore`

Cada grupo necesita una herramienta distinta. Tapar todo sería el error:

- **B008 (32)** → `extend-immutable-calls`. No es "no mires este archivo", es "esta llamada en
  un default es segura". Sobrevive a que el archivo se mueva y no tapa otros B008 reales.
  Cubre 25 de 32 (`Depends`, `Query`, `Path`, `Body` y `require_permission`).
- **7 × `require_superadmin`** → `per-file-ignores`. **No entra por `extend-immutable-calls`**:
  está definido en el mismo archivo que lo usa (`plugins/tenant/backend/router.py:37`), no
  importado, y ruff no resuelve el qualified name. Verificado: con el nombre desnudo y con
  `plugins.tenant.backend.router.require_superadmin` el conteo queda en 51, o sea no matchea.
- **I001 (12) y F401 (3)** → `ruff check . --fix`. No es una decisión de config, es código.
- **E501 (3)** → envolver. Subir `line-length` para callarlas sería cambiar la política del
  proyecto para tapar 3 líneas.

El patrón roto se **borra** en vez de arreglarse: con `extend-immutable-calls` ya no hace
falta, y una línea de config muerta que aparenta hacer algo es peor que no tenerla.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: >
      Cero cambios funcionales. Solo orden de imports, eliminación de 3 imports no usados y
      envoltura de 3 líneas. La suite completa debe seguir en 85 passed.
    proof: pytest tests plugins/mail/backend/tests -q == 85 passed

  - id: I2
    statement: >
      Los 3 imports eliminados están realmente sin uso; ninguno es un re-export intencional.
      app/main.py:3 StaticFiles, conftest.py:4 MagicMock, test_schemas.py:6 MailService.
    proof: grep del símbolo en cada archivo, sin resultados fuera del import

  - id: I3
    statement: >
      No se agrega ninguna supresión nueva. Se borra 1 patrón per-file-ignores y se agregan
      1 per-file-ignores y 5 immutable-calls. `select` no cambia.
    proof: diff de pyproject.toml, sin líneas agregadas bajo [tool.ruff.lint] select

  - id: I4
    statement: >
      src/ (el kernel) queda limpio. `ruff check src/` reporta All checks passed.
    proof: ruff check src/

  - id: I5
    statement: >
      La config no-ruff de pyproject.toml queda intacta: pytest testpaths, pyright include y
      dependencies no se tocan.
    proof: diff de pyproject.toml limitado a [tool.ruff.lint.*]

  - id: I6
    statement: >
      El trabajo de 0001 no se altera. service.py, test_service.py, test_router.py y
      MailAccountsPage.tsx no aparecen en el diff de esta A.SPEC.
    proof: git diff --stat, esos 4 archivos ausentes
```

## VERIFICATION

```bash
cd vendor/systutor-core
ruff check .                                              # la verdad: All checks passed!
ruff check src/                                           # I4
python -m pytest tests plugins/mail/backend/tests -q      # I1: 85 passed
git diff --stat -- pyproject.toml                         # I3, I5
git diff --stat                                           # I6
```

**Proof de falsabilidad**: la verdad es `ruff check .` == 0. Antes de este cambio era 51.
Si alguien revierte solo el bloque `[tool.ruff.lint.flake8-bugbear]`, el conteo vuelve a 51
y la A.SPEC es FAIL.

## ROLLBACK

`git revert <sha>`. Sin esquema, sin datos, sin estado en runtime. La config de lint no tiene
efectos secundarios fuera de la ejecución del linter.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/pyproject.toml
    - vendor/systutor-core/app/main.py
    - vendor/systutor-core/migrations/env.py
    - vendor/systutor-core/migrations/versions/*.py
    - vendor/systutor-core/plugins/mail/backend/register.py
    - vendor/systutor-core/plugins/mail/backend/settings.py
    - vendor/systutor-core/plugins/mail/backend/tests/conftest.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_schemas.py
  prohibited:
    - vendor/systutor-core/plugins/mail/backend/service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_service.py
    - vendor/systutor-core/plugins/mail/backend/tests/test_router.py
    - vendor/systutor-core/plugins/mail/backend/provider.py
    - vendor/systutor-core/plugins/mail/backend/router.py
    - vendor/systutor-core/plugins/mail/backend/schemas.py
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/tenant/**
    - apps/web/**
    - docker-compose.yml
```

Los 4 archivos de 0001 están prohibidos explícitamente: esta A.SPEC no debe tocar el trabajo
de la anterior.

## Blast Radius

```yaml
blast_radius:
  direct:
    - 13 archivos Python (imports) + pyproject.toml
    - El gate de lint de todo el repo
  indirect:
    - Futuros agentes y CI que confíen en `ruff check .`; un gate verde se vuelve exigible
    - plugins/tenant/backend/router.py recibe un per-file-ignores nuevo
  must_not_affect:
    - surface: mail_cache_behaviour
      surfaces: [plugins/mail/backend/service.py]
      invariant: I6
    - surface: kernel_runtime
      surfaces: [src/**]
      invariant: I4
    - surface: pytest_and_pyright_config
      surfaces: [pyproject.toml]
      invariant: I5
```

## Composition

```yaml
composition:
  requires_aspecs: []
  must_compose_with: []
  systemic_invariants:
    - El kernel y los plugins siguen compiliendo y ejecutando igual; esto es config y formato.
  composition_checks:
    - python3 -m pytest tests plugins/mail/backend/tests -q
    - ruff check .
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: one coherent responsibility and one main reason to change
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations: []
```

Sin lógica nueva. No aplica.

## Traceability

- **Requirement**: el gate de lint debe ser confiable, o no sirve
- **A.SPEC**: este documento
- **Code**: `pyproject.toml` + 13 archivos (imports/formato)
- **Migration**: ninguna
- **Test**: suite existente (sin tests nuevos; no hay comportamiento nuevo que probar)
- **Commit**: `TBD`
- **Deployment**: ninguno. El linter no corre en runtime.

- **owner**: `TBD`
- **approver**: `TBD`

> Igual que 0001: sin owner y approver esto es un GAP de integración (§10.2).

## Definition of Done

- [x] Objective satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [ ] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [ ] Traceability established
