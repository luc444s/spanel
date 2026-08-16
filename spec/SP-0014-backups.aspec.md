# A.SPEC SP-0014 — WordPress backups

## WHY

Un gestor de sitios sin backups es un gestor de desastres. WordPress
necesita snapshot de archivos (wp-content) + dump de DB, guardados fuera
del container, recuperables.

## WHAT

Una transición observable: admin dispara un backup de un Site wordpress;
Spanel genera tar de wp-content + dump de mariadb, los deposita en un
volumen/almacenamiento nombrado, y el backup queda listado con estado.

## SCOPE

- `POST /api/v1/plugins/hosting/sites/{id}/backups` → tar wp-content
  (docker exec/tar) + `mysqldump` del db container.
- Destino: volumen docker `spanel-backups` (o path del remoto vía env).
- Tabla `backup` (site_id, kind, path, size, status, created_at).
- `GET /api/v1/plugins/hosting/sites/{id}/backups` lista.
- Restauración queda EXPLÍCITAMENTE fuera (primera iteración solo
  respaldar; restaurar = spec siguiente, manual hasta entonces).

## OUT OF SCOPE

- Restauración automática, cron de backups, retención/rotación,
  backups incrementales, offsite (S3/rsync).
- Backups de stacks no-wordpress (php/static — spec futura).

## CONTRACT

- PRE: site wordpress adoptado/provisionado con db asociada.
- POST: backup listado con size>0 y status ok; archivos verificables en
  destino (tar test); fallo de dump → status failed y sin archivos
  parciales visibles; backups de sites de otros tenants inaccesibles.

## INVARIANTS

```yaml
invariants:
  - Backup MUST NO detener el sitio (dump con --single-transaction).
  - Datos de otros tenants MUST NO mezclarse en el destino.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/backups -H "Authorization: Bearer $TOKEN"
sshpass -p "$PW" ssh lucas@100.67.5.50 'docker run --rm -v spanel-backups:/b busybox ls -la /b'
```

## ROLLBACK

Borrar entradas `backup` y archivos del destino (todo nombrado por
`<site>-<timestamp>`).

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker-infra/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.backup
  indirect:
    - docker.remote (volumen spanel-backups)
  must_not_affect:
    - kernel.auth
    - sitio backup objetivo (uptime)
    - containers no adoptados
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
