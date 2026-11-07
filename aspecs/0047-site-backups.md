# A.SPEC 0047 — Respaldos de sitios: WordPress completo y estáticos, en un .zip

> `risk: normal` — CERO cambios al backend en la fase prototipo; la fase
> feature sólo agregar rutas de panel. Nunca destruye: siempre crea un .zip
> nuevo y verifica su contenido.

## WHY

El caso real (cutover de `tutorabusiness` a astro): para apagar un WordPress
con confianza hace falta un respaldo completo y portable — **un solo .zip**
que viaje al equipo del operador — con DB, archivos y manifiesto. Hoy esto es
comandos sueltos con error humano en el medio.

El flujo del proyecto (efectivo en 0042, 0038) es prototype-first: primero un
**script del operador verificado en el lab**, luego la versión del panel.

## FASE 1 (prototipo, scripts/, sin tocar el panel)

`scripts/site_backup.py` — un script para AMBOS tipos, interesante por el
mismo contracto; la detección es automática:
- **WordPress**: si el inspect del container expone
  `WORDPRESS_DB_HOST/USER/PASSWORD/NAME` → respaldo WP completo (archivos +
  DB). El container de DB se infiere de `WORDPRESS_DB_HOST` (si es el nombre
  de un contenedor corriendo); override `--db-container`, y
  `--db-user/--db-pass/--db-name` si el env del container no lo tiene.
- **Estático** (sin env WP): solo `site/**` — sin mysqldump. El path a
  respaldar se resuelve así: `--path` override > label `spanel.profile` →
  web_root del catálogo default (nginx `/usr/share/nginx/html`,
  caddy `/usr/share/caddy`, astro `/site`, httpd
  `/usr/local/apache2/htdocs`) > fallback `/usr/share/nginx/html`.
- Entrada común: `--container <site-container>`; `--out DIR` (default
  `/root/backups`); `--verify <zip>` para re-verificar un backup previo;
  `--dry-run` para printear plan sin escribir.
- Acciones (todas read-only para el container respaldado):
  1. inspect del container: env + labels (spanel.profile, spanel.kind).
  2. Archivos: `docker cp <wp>:/var/www/html -` → tar → extraer a tmp
     (`site/`).
  3. (Solo WP) DB: `docker exec <db> sh -c 'exec mysqldump -u<user>
     -p<pass> <name>'` (stdout a tmp `database/backup.sql`).
  4. `.zip` final en `DIR/<kind>-<container>-<ts>.zip` con:
     - `manifest.json` (kind, timestamp, container, db?, listas de rutas
       con SHA256, tamaño total);
     - `site/**` (todos los archivos del sitio);
     - `database/backup.sql` (solo WP).
  5. **self-verify**: reabre el zip, verifica el SHA256 de cada entrada
     está del manifest y (WP) que `database/backup.sql` no esté vacío
     (estático: `site/` al menos con un archivo); exit 0/2.
- Salida: ruta impresa del .zip + resumen (N archivos, M MB, SQL grafos).
- El .zip **NO guarda** credenciales; el valor de WORDPRESS_DB_PASSWORD
  jamás entra al manifest (solo "db:<host>").

## FASE 2 (feature del panel — spec posterior con surface del kernel)

- `POST /hosting/sites/{id}/backup` → job async (task system) que llena un
  dir de backups del servidor host y expone el token descarga.
- `GET /hosting/sites/{id}/backups` + link del file manager o download
  (stream por permiso `hosting.sites.manage`).
- Retención: variable `SPANEL_HOSTING_BACKUPS_DIR`, por-sitio, purge manual
  primero (N por defecto las conserves en la DB tables ya existentes).
- Estaticos: mismo contrato (solo `site/**`, sin DB).

## SCOPE (Fase 1)

- `scripts/site_backup.py` (nuevo, stdlib-only; WP y estático en el mismo script)
- `scripts/tests/test_site_backup.py` (nuevo, fakes offline)
- `aspecs/0047-site-backups.md`

## CONTRACT (Fase 1)

Precondiciones:
- docker CLI accesible en el host del backup; contenedor WP corriendo.

Postcondiciones:
1. `python3 scripts/site_backup.py --container legalattorney-clean --db-container legalattorney-clean-db`
   produce `.zip` verificable cuyo contenido se restaura con:
   docker cp files from `/site/**` (`/var/www/html`) y
   `mysqldump < backup.sql`.
2. Un segundo run con el mismo prefix no sobrescribe: timestamp nuevo.
3. Si mysqldump falla (DB caída, pass incorrecto) → falla antes de zip, no
   produce paquete incompleto.
4. El manifest no contiene password.
5. Exit codes: 0 ok (+ "--verify" re-verificada), 2 error de verificación,
   1 error de ejecución.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El proceso es read-only para los containers respaldados.
    proof: subprocess calls únicamente docker cp/inspect/exec mysqldump; test no mute ninguna llamada docker con verbos que no sean (inspect|cp|exec.*mysqldump|logs).
  - id: I2
    statement: El .zip termina completo o no existe (build en tmp + os.replace atómico).
    proof: test de failure del mysqldump no deja zip ni partial.
  - id: I3
    statement: El zip puede restaurar por sí solo (site/ + database/backup.sql + manifest con hashes).
    proof: test de roundtrip zip + sha256 por entrada.
  - id: I4
    statement: No se loguea/persisten credenciales.
    proof: grep del output/token; manifest sin password.
  - id: I5
    statement: stdlib-only, determinístico a los nombres de timestamp.
    proof: formato fijo y ruff; archivo ≤ 400 líneas.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin && .venv/bin/python -m pytest scripts/tests/test_site_backup.py -q
# lab/prod, casos reales del operador:
python3 scripts/site_backup.py --container legalattorney-clean --db-container legalattorney-clean-db   # WP
python3 scripts/site_backup.py --container portafolio                                                   # estático (portafolio)
```

## ROLLBACK

Borrar el script. Los .zip que ya se crearon quedan.

## Change Surface

```yaml
change_surface:
  allowed:
    - scripts/site_backup.py
    - scripts/tests/test_site_backup.py
    - aspecs/0047-site-backups.md
  prohibited:
    - vendor/**
    - apps/**      # (Fase 2 feature del panel: su propia spec con surface)
    - docker-compose.yml
    - .env*
```

## Traceability

- Requirement: respaldo completo portable de un sitio — WordPress (archivos + DB) o estático (solo archivos) — antes de un cutover, de un comando, contenido verificable, empaquetado en el mismo formato .zip.
- owner: luc444s
- Deployment: script en localhost del server (o subido con scp como spanel-repair).

## Definition of Done

- [ ] Script produce .zip completo verificado para WP y para estático (test + lab real).
- [ ] Restaurar el .zip en un WP para demostradores funciona con instrucciones.
- [ ] Sin credenciales filename; manifest sha256.
- [ ] Suite de tests offline verde; ruff verde.
