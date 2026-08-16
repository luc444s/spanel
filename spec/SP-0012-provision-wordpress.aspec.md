# A.SPEC SP-0012 — Provision WordPress site

## WHY

Adoptar wp existentes cubre la mitad del problema. Para sitios nuevos
Spanel debe crear el stack completo: wordpress + mariadb + wp-cli,
listo para un dominio (SP-0011) y con el plugin SSO instalado (SP-0017).

## WHAT

Una transición observable: admin pide un sitio WordPress nuevo; Spanel
crea en el docker remoto los containers wordpress y mariadb (red propia),
instala WP con wp-cli, registra el Site y entrega admin listo.

## SCOPE

- `POST /api/v1/plugins/hosting/sites/provision/wordpress`
  {name, db_name?, admin_email, admin_user?} →
- Crea: red `spanel-<site>`, container mariadb (volumen persistente),
  container wordpress (imagen oficial, volumen wp-content), secrets
  generados (db password aleatorio, no reusado).
- Instalacion WP: entrypoint de la imagen oficial auto-instala (core
  install + config). wp-cli disponible via imagen `wordpress:cli`
  one-shot (para SP-0017/SSO plugin).
- Site persistido con stack=wordpress, container_id, db_container_id,
  dominios vacíos (SP-0011 después).
- Audit + evento `site.provisioned`.

## OUT OF SCOPE

- Dominios/SSL (SP-0011), backups (SP-0014), themes/plugins por sitio.
- Staging/clones, multisite, wp multisitio.
- Provision php/astro genéricos (spec futura).

## CONTRACT

- PRE: docker-infra habilitado; imágenes wordpress/mariadb pullables en
  el remoto.
- POST: containers corriendo; wp responde 200 en backend; db accesible
  desde wp; admin credenciales devueltas una sola vez (o rotadas vía
  wp-cli); fallo a mitad → rollback (containers/red borrados, sin filas
  huérfanas).

## INVARIANTS

```yaml
invariants:
  - Secrets (db password) MUST ser aleatorios y no persistirse en claro
    en logs.
  - Provision MUST NO tocar containers/sites existentes.
  - Tenant isolation MUST permanecer intacta.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/provision/wordpress -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name":"demo","admin_email":"admin@example.com"}'
sshpass -p "$PW" ssh lucas@100.67.5.50 'docker ps --format "{{.Names}} {{.Status}}" | grep demo'
```

## ROLLBACK

`docker rm` de containers creados + `docker network rm` + borrar fila
site (todo generado por esta spec está aislado por nombre `spanel-<site>`).

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
    - plugins/proxy/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.provision
  indirect:
    - docker.remote (containers nuevos)
  must_not_affect:
    - kernel.auth
    - sites existentes
    - docker-infra.containers
```

## Traceability

- Requirement: arquitectura-base.md §6.
- Commit: root (pendiente)
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
