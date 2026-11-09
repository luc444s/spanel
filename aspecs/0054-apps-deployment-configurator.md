# A.SPEC 0054 — Configurador operativo de Apps

> `risk: medium` — edita `.env.docker`, crea una clave privada local y puede
> recrear servicios Docker de SPanel. Debe ser idempotente, fail-closed y nunca
> imprimir secretos.

## WHY

Manifest Flow requiere URL pública HTTPS, una clave AES-256 y reiniciar `app` y
`apps-worker` en orden. Hacerlo manualmente es propenso a rutas incorrectas,
claves versionadas y workers arrancados antes de las migraciones.

## WHAT

Crear `scripts/configure_apps.py` para:

1. detectar el origen desde `--public-url`, una configuración existente o una
   única label Traefik `Host(...)` del Compose, en ese orden;
2. validar origen HTTPS sin path/query/credenciales;
3. generar una clave urlsafe-base64 de 32 bytes en un archivo `0600`, dentro de
   un directorio `0700`, o validar/reutilizar la existente;
4. actualizar atómicamente sólo las variables `SPANEL_APPS_*` en `.env.docker`,
   preservando contenido y creando backup privado;
5. validar `docker compose --env-file .env.docker config`;
6. opcionalmente, con `--restart`, recrear/build `app`, esperar health local y
   después recrear/build `apps-worker`;
7. ofrecer `--dry-run`, `--no-build`, `--env-file`, `--compose-file` y
   `--key-path`.

Defaults:

```text
env:       .env.docker
compose:   docker-compose.yml
key host:  ./secrets/apps_key
key mount: /run/secrets/apps_key
state:     /var/lib/spanel/apps
version:   1
```

El script no usa shell, no imprime valores secretos, no ejecuta `down`, no
recrea PostgreSQL/Redis y no modifica variables ajenas. Una detección ambigua o
HTTP no-loopback aborta y exige `--public-url`.

## INVARIANTS

```yaml
invariants:
  - id: C1
    statement: La clave contiene exactamente 32 bytes aleatorios, archivo 0600 y directorio 0700.
    proof: tests de decode, permisos, reuse e invalid key.
  - id: C2
    statement: Clave y valores preexistentes nunca aparecen en stdout/stderr ni argv Docker.
    proof: runner spy y canarios negativos.
  - id: C3
    statement: Sólo se actualizan las cinco variables Apps declaradas; el resto del env se preserva.
    proof: fixture con comentarios, secretos y duplicados.
  - id: C4
    statement: Restart ordena app saludable antes de apps-worker y nunca ejecuta down.
    proof: secuencia exacta con runner fake.
  - id: C5
    statement: URL pública no se deriva de HTTP Host runtime ni de una label ambigua.
    proof: tests zero/one/multiple labels y override explícito.
```

## VERIFICATION

```bash
.venv/bin/python -m pytest scripts/tests/test_configure_apps.py -q
.venv/bin/ruff check scripts/configure_apps.py scripts/tests/test_configure_apps.py
.venv/bin/python -m pyright scripts/configure_apps.py
git diff --check
```

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - aspecs/0054-apps-deployment-configurator.md
    - scripts/configure_apps.py
    - scripts/tests/test_configure_apps.py
    - README.md
    - .gitignore
  prohibited:
    - .env
    - .env.docker
    - docker-compose.yml
    - vendor/**
    - apps/**
```

## ROLLBACK

Eliminar script/spec/tests/docs no elimina la clave ni restaura automáticamente
el env. El backup generado permite restauración explícita; nunca se borran
volúmenes ni servicios.
