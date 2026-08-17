# A.SPEC SP-0028 — Production deploy pass

## WHY

Spanel ya tiene base Docker deploy (`Dockerfile`, `docker-compose.yml`,
`nginx.conf`, `.env.example`), pero todavía mezcla flujos de desarrollo y
producción, depende de artefactos host para `web-prod`, y no deja un
runbook corto y confiable para levantar VPS público con `SPANEL_MODE=production`.

## WHAT

Transición observable: un operador toma repo limpio, configura `.env`,
levanta perfil producción sobre Docker Compose y obtiene frontend estático
servido por nginx + API FastAPI + Postgres + Redis con checks y runbook
claros, sin depender del dev server de Vite.

## SCOPE

### Compose / imágenes

- Separar claramente servicios dev y prod en `docker-compose.yml`:
  - `web` queda sólo para desarrollo Vite.
  - `web-prod` queda sólo para producción.
  - `docker compose --profile production up -d` no debe dejar también el
    dev server expuesto.
- `web-prod` debe construirse desde repo, no montar `apps/web/dist` como
  prerequisito manual del host.
  - Aceptable: `Dockerfile.web`, target multi-stage o build dedicado.
  - El resultado final sirve `apps/web/dist` desde nginx en contenedor
    inmutable.
- `api` mantiene puerto interno `8001`. Esta spec NO toca puerto `8000`.

### Hardening mínimo

- Validar y documentar secretos obligatorios no default en producción:
  `DB_PASSWORD`, `JWT_SECRET`, `SSO_SECRET`, `SPANEL_DOCKER_SSH_*`.
- Agregar checks de salud útiles en servicios que hoy no los tienen cuando
  sea viable con artefactos existentes del repo.
- `nginx.conf` de producción debe preservar proxy correcto hacia `api:8001`
  y fallback SPA `index.html`.

### Runbook / docs

- README actualizado con flujo explícito:
  1. clonar con submodules
  2. crear `.env`
  3. build/launch producción
  4. smoke checks
  5. logs / rollback básicos
- Incluir comandos concretos de validación (`docker compose ps`, `curl`,
  `docker compose logs api`, `docker compose logs web-prod`).

## OUT OF SCOPE

- TLS termination dentro del compose local de Spanel.
- Cambios en Traefik remoto del dominio de los Sites gestionados.
- Migrar API fuera de FastAPI/uvicorn o reescribir arquitectura Docker.
- Cambiar puerto 8000 o tocar `orquestador_ardi_postgres`.

## CONTRACT

- PRE: Docker Engine y Compose disponibles en host de despliegue; repo con
  submodules presentes.
- POST: producción corre sin Vite, con frontend estático y API accesible;
  documentación alcanza para desplegar desde cero sin pasos implícitos.
- Perfil producción no debe depender de build manual previo en host.
- Secretos por defecto inseguros no quedan presentados como válidos para
  producción real.

## INVARIANTS

```yaml
invariants:
  - API interna MUST permanecer en `8001`.
  - Puerto `8000` MUST NOT tocarse.
  - DB operativa remota `orquestador_ardi_postgres` MUST NOT tocarse.
  - `SPANEL_MODE=production` MUST seguir soportado sobre base actual de Docker Compose.
  - Kernel/submodule `vendor/systutor-core` MUST seguir siendo dependencia, no fork local.
```

## VERIFICATION

```bash
docker compose config
docker compose --profile production build
docker compose --profile production up -d
docker compose ps
curl -sI http://127.0.0.1/
curl -s http://127.0.0.1/api/v1/core/plugins
docker compose logs api
docker compose logs web-prod
```

## ROLLBACK

Revertir archivos de deploy (`Dockerfile*`, `docker-compose.yml`,
`nginx.conf`, `README.md`, `.env.example`) y volver al flujo previo.
Rollback operacional: `docker compose down` y relanzar versión anterior.

## Change Surface

```yaml
change_surface:
  allowed:
    - Dockerfile
    - Dockerfile.*
    - docker-compose.yml
    - nginx.conf
    - .env.example
    - README.md
    - apps/web/**
  prohibited:
    - vendor/**
    - plugins/**
    - tests/e2e/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - deploy.compose.production
    - deploy.web.nginx
    - deploy.docs
  indirect:
    - local.dev.compose_profiles
  must_not_affect:
    - kernel.auth
    - remote.site.traffic
    - docker.remote.operational_db
```

## Traceability

- Requirement: README.md (deploy actual), arquitectura-base.md §1, §3, §9.
- Commit: (pending)
- Deployment: `docker compose --profile production up -d`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
