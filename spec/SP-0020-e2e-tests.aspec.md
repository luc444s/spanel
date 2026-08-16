# A.SPEC SP-0020 — End-to-end acceptance suite (hosting + mail)

## WHY

El producto necesita validación de punta a punta sobre el docker remoto
real: provisionar, detectar, operar, SSO y mail. Sin esto, cada spec
queda verificada aislada y el flujo completo puede fallar en la
integración. Esta spec es la suite de aceptación: no implementa features,
verifica que el conjunto entregado por SP-0008..0019 funciona junto.

## WHAT

Transición observable: un operador ejecuta 5 escenarios sobre
infraestructura real (docker remoto Tailscale) y los 5 pasan:

1. **Crear container docker con WP mínimo** — provision externo (wp
   mínimo, no necesariamente el provision de SP-0012).
2. **Spanel detecta el WP** — discovery lo ve como candidato; adopt lo
   registra como Site wordpress.
3. **Crear docker-mailserver y detectarlo con correos de prueba** —
   mailserver operativo, Spanel lo adopta, se envían/reciben correos de
   prueba entre buzones.
4. **Entrar a wp-admin desde Spanel sin credenciales** — SSO magic link
   (SP-0017) loguea sin password.
5. **Crear buzones desde Spanel** — buzones creados desde el panel
   (API/UI) quedan operativos en el mailserver.

## SCOPE

- Scripts de fixture en `tests/e2e/` (bash/python + docker remoto):
  `provision-wp.sh` (escenario 1), `provision-mail.sh` (escenario 3).
- Checklist ejecutable `tests/e2e/run.sh` que corre escenarios 1-5 contra
  API 8001 y docker remoto, con asserts por escenario.
- Documentación del run en esta spec (VERIFICATION).

## OUT OF SCOPE

- Tests unitarios de cada plugin (cada spec los trae).
- CI/CD, entornos repetibles desechables (los fixtures usan nombres
  `spanel-test-*` y se limpian al final).

## CONTRACT

- PRE: SP-0008, SP-0009, SP-0011 (routing), SP-0013, SP-0017, SP-0019
  implementadas; docker remoto con imágenes wordpress/mariadb/
  docker-mailserver disponibles.
- POST: los 5 escenarios terminan verdes; fixtures `spanel-test-*`
  removidos (cleanup); sin filas huérfanas en DB `spanel`.

## INVARIANTS

```yaml
invariants:
  - Fixtures MUST usar prefijo spanel-test-* y removerse al final.
  - Containers del usuario (vroom, osrm, postgres) MUST NOT tocarse.
  - Kernel MUST NOT ser modificado.
  - Credenciales de prueba MUST NO ir a logs ni commits.
```

## VERIFICATION

```bash
cd tests/e2e
./provision-wp.sh      # escenario 1: wp minimo spanel-test-wp
./provision-mail.sh    # escenario 3: mailserver spanel-test-mail
./run.sh               # 2, 3b, 4, 5 contra API 8001
```

Criterios por escenario:

1. `docker ps` remoto muestra `spanel-test-wp` (wordpress + mariadb).
2. `GET /api/v1/plugins/docker-infra/containers` lo lista; adopt lo
   registra con stack=wordpress; `GET /hosting/sites` lo muestra.
3. `spanel-test-mail` operativo; Spanel lo adopta; correo
   `test1@test.<ts>.ts.net` → `test2@...` llega (IMAP/POP3 o log).
4. `POST /hosting/sites/{id}/sso` → navegador loguea en wp-admin sin
   pedir password (curl -L → 200 wp-admin con cookie).
5. `POST /mail/mailboxes` crea `nuevo@...`; SMTP autentica con las
   credenciales devueltas y entrega.

## ROLLBACK

`docker rm` fixtures + drop de filas test en DB + `git checkout`.

## Change Surface

```yaml
change_surface:
  allowed:
    - tests/e2e/**
  prohibited:
    - vendor/**
    - plugins/**
    - apps/web/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - docker.remote (fixtures spanel-test-*)
  indirect:
    - db.spanel (filas de prueba)
  must_not_affect:
    - containers del usuario
    - kernel.auth
    - prod
```

## Traceability

- Requirement: arquitectura-base.md §1 (capacidades núcleo) — escenarios
  pedidos por el usuario.
- Commit: root d565c6f
- Deployment: suite manual/cron sobre stack dev.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
