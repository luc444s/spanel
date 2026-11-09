# A.SPEC 0052A — Extraer el motor compartido de Apps

> `risk: high` — refactoriza la primitiva que audita y despliega Compose. No
> añade API, worker, tablas ni UI, pero un cambio semántico accidental podría
> relajar las guardas validadas por A.SPEC 0051.

## WHY

`scripts/app_deploy.py` demostró la primitiva completa como un monolito. El
futuro plugin Apps y la CLI deben ejecutar exactamente la misma auditoría,
ownership, staging y validación non-root. Copiar funciones dentro del plugin
crearía dos políticas de seguridad divergentes.

Esta primera entrega es deliberadamente un refactor sin comportamiento nuevo.
Deja un motor importable y una CLI delgada antes de introducir persistencia,
OAuth o asincronía.

## WHAT

Extraer la implementación de `scripts/app_deploy.py` bajo:

```text
vendor/systutor-core/plugins/apps/backend/runtime/
  audit.py
  source.py
  transport.py
  deployment.py
  lifecycle.py
  manifests.py
```

La división exacta puede variar para conservar cohesión. El directorio todavía
no incluye `plugin.json`, por lo que no aparece como plugin incompleto.

`scripts/app_deploy.py` queda como adaptador CLI fino sobre APIs Python tipadas.

## SCOPE

### 1. Compatibilidad CLI

Se conservan:

- comandos `inspect`, `deploy`, `status`, `logs` y `down`;
- flags, defaults y validaciones de A.SPEC 0051;
- formato humano y JSON;
- exit 0/2;
- dry-run sin mutaciones;
- transporte local/SSH;
- layout y schema de manifest existente;
- redacción de logs y mensajes públicos seguros.

Los tests pueden migrar imports privados hacia el runtime, pero deben seguir
ejecutando también `main()`/CLI para demostrar compatibilidad externa.

### 2. API interna estructurada

El motor expone objetos tipados para:

- checkout y revisión efectiva;
- `AuditResult` y `Finding`;
- deploy options/result;
- manifest y snapshot de servicios;
- lifecycle status/logs/down;
- errores esperados sin imprimir secretos.

El motor no recibe `argparse.Namespace`, no imprime y no llama `sys.exit`.
Render y parsing CLI permanecen en `scripts/app_deploy.py`.

### 3. Guardas que no cambian

Continúan íntegros los invariantes de A.SPEC 0051:

- descubrimiento Compose no ambiguo;
- build contexts/Dockerfiles confinados al checkout;
- rechazo de privileged, capabilities, devices, host namespaces, socket Docker,
  binds peligrosos, ports y recursos externos del repositorio;
- findings con archivo/línea/remediación;
- env externo regular, no symlink, fuera del checkout y privado;
- `USER` final, `.Config.User` y procesos efectivos non-root;
- override `cap_drop: ALL` + `no-new-privileges`;
- manifests y labels antes de lifecycle;
- cero adopción o cleanup de recursos ajenos;
- secretos ausentes de argv, salida, logs y manifests.

### 4. Dependencias y estructura

- No añadir FastAPI, SQLAlchemy, Redis, Dramatiq ni OAuth al runtime.
- El runtime acepta runners/config explícitos y puede probarse sin daemon.
- Ningún archivo nuevo supera 600 líneas; dividir antes de 400 cuando mezcle
  responsabilidades.
- La CLI puede preparar `sys.path` para checkout, pero producción importa el
  package normalmente desde `/app/plugins`.

## OUT OF SCOPE

- `plugin.json`, permisos, migraciones, modelos, API o frontend Apps.
- Cola, worker, jobs, polling o persistencia de operaciones.
- OAuth GitHub o almacenamiento de tokens.
- Red externa proxy, dominios o puerto de ingress.
- Nuevas reglas de seguridad o soporte de proveedores Git adicionales.
- Redespliegue/rollback multi-release; pertenece a 0052B.

## CONTRACT

Precondición: las entradas que hoy acepta la CLI siguen siendo válidas.

Postcondición: CLI y callers Python observan los mismos findings, mutaciones,
manifests y errores que A.SPEC 0051. El refactor no toca el host durante
inspect/dry-run y no reduce ninguna barrera antes de build/up.

## INVARIANTS

```yaml
invariants:
  - id: A1
    statement: Existe una sola implementación de cada regla de auditoría, non-root, ownership y redacción.
    proof: grep de códigos/constantes y tests importan el mismo runtime desde CLI y API interna.
  - id: A2
    statement: La salida JSON/humana y exit codes de la CLI permanecen compatibles.
    proof: snapshots/casos main() antes-después para inspect limpio, bloqueado, dry-run y error esperado.
  - id: A3
    statement: Los fixtures de A.SPEC 0051 producen exactamente los mismos findings ordenados.
    proof: suite scripts/tests/test_app_deploy.py completa sin relajar asserts.
  - id: A4
    statement: Inspect y deploy --dry-run conservan cero llamadas mutantes.
    proof: runners espía existentes permanecen en cero build/up/staging remoto.
  - id: A5
    statement: Ningún secreto aparece en excepciones, render, argv persistido, logs ni manifest.
    proof: canarios de la suite existente recorren CLI y API estructurada.
  - id: A6
    statement: El package runtime no depende de FastAPI, DB, Redis, Dramatiq ni frontend.
    proof: import graph/grep y tests unitarios cargan runtime aislado.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

.venv/bin/python -m pytest scripts/tests/test_app_deploy.py -q

SPANEL_LIVE_TESTS=0 .venv/bin/python -m pytest \
  scripts/tests/test_app_deploy_live.py -q

.venv/bin/python -m ruff check \
  scripts/app_deploy.py \
  scripts/tests/test_app_deploy.py \
  scripts/tests/test_app_deploy_live.py \
  vendor/systutor-core/plugins/apps/backend/runtime

git diff --check
```

El live test deshabilitado debe reportar skip explícito.

## ROLLBACK

Revertir este commit restaura el script monolítico. No existen tablas, recursos
nuevos ni migraciones. Los deployments previos de A.SPEC 0051 siguen operables
por sus manifests.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0052-async-first-apps-module.md
    - aspecs/0052a-shared-apps-runtime.md
    - scripts/app_deploy.py
    - scripts/tests/test_app_deploy.py
    - scripts/tests/test_app_deploy_live.py
    - vendor/systutor-core/plugins/apps/backend/__init__.py
    - vendor/systutor-core/plugins/apps/backend/runtime/**
  prohibited:
    - vendor/systutor-core/plugins/apps/plugin.json
    - vendor/systutor-core/plugins/apps/migrations/**
    - vendor/systutor-core/plugins/apps/frontend/**
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/domains/**
    - Dockerfile
    - docker-compose.yml
    - .env*
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: un motor compartido sin cambio de comportamiento
  cli_must_stay_thin: true
  runtime_framework_independent: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
```

## Commit obligatorio

```text
refactor(apps): extract shared compose runtime (A.SPEC 0052A)
```

El commit incluye sólo el change surface de 0052A y debe quedar verde antes de
comenzar 0052B.

## Definition of Done

- [ ] CLI compatible y delgada.
- [ ] Motor dividido, importable y framework-independent.
- [ ] 49 tests offline de A.SPEC 0051 siguen verdes.
- [ ] Live deshabilitado produce skip.
- [ ] Una sola definición de findings/ownership/non-root/redacción.
- [ ] Ruff y diff-check verdes.
