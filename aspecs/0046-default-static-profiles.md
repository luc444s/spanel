# A.SPEC 0046 — Catálogo default de perfiles estáticos: react, boot y astro (evidencia de lab)

> `risk: normal` — solo migración `004` de datos con INSERT condicionales
> idempotentes + un guard de DELETE en downgrade. Sin schema, sin backend, sin
> front. Los perfiles vienen del lab verificado 24/24 (evidencia en 0042).

## WHY

Hoja del gestor: solo existe el builtin `nginx` (creado por 003). El lab de la
0042 validó 4 perfiles reales de punta a punta (container, health por marker,
HTTPS por Traefik): `plain` (=nginx), `react` (Caddy SPA por CDN), `boot`
(Apache + Bootstrap) y `astro` (Node build+preview). Son los valores default
del catálogo cerrado del plugin — sembrarlos deja el panel útil desde el
primer deploy, sin trabajo del admin (que igual puede añadir, editar o borrar
los cuatro por la UI si quiere).

## WHAT

1. **`plugins/hosting/migrations/004_seed_default_static_profiles.py`**
   (nuevo, estilo del 003):
   - INSERT de 3 filas en `hosting_static_profiles` **solo si id no existe**:
     | id | name | image | web_root | port | health_mode | command |
     |---|---|---|---|---|---|---|
     | `react` | React SPA (Caddy) | `caddy:2-alpine` | `/usr/share/caddy` | 80 | marker | NULL |
     | `boot` | Bootstrap (Apache) | `httpd:alpine` | `/usr/local/apache2/htdocs` | 80 | marker | NULL |
     | `astro` | Astro (build + preview) | `node:24-alpine` | `/site` | 80 | `http_200` | ver abajo |
   - El `command` de astro (adaptado sin workdir, cd explícito):
     ```
     cd /site; i=0; while [ ! -f src/pages/index.astro ]; do i=$((i+1)); [ $i -gt 300 ] && exit 1; sleep 1; done; npm install --no-fund --no-audit && npx astro build && npx astro preview --host 0.0.0.0 --port 80
     ```
     (espera el ÚLTIMO archivo del seed — hallazgo #2 del lab — antes de
     compilar; timeout 300 s).
   - `health_mode="http_200"` para astro: el preview sirve contenido
     desconocido y el seed deホーミン no existe en el runtime del plugin
     (el marker del perfil solo existe en el seed de la verificación; el sitio del panel sirve contenido del tenant, así que `http_200` en el puerto del perfil es la garantía correcta).
   - Idempotente: upgrade sobre una DB que ya tiene las filas → cero writes.
   - `downgrade()`: DELETE solo si **ningún `hosting_sites.profile_id`**
     referencia el id (los perfiles en uso NO se borran; el guard del
     servicio `hosting_profile_in_use` mantiene la integridad igual).
2. **UI cero cambios**: el gestor ya lista por `GET /static-profiles`; el
   selector de Crear sitio los pinta al refrescar.
3. **Deploy**: el upgrade corre en el arranque del plugin (mismos mecanismos
   que 003); para dev local basta reiniciar uvicorn o correr el upgrade del
   plugin.

## SCOPE

Entra:
- `vendor/systutor-core/plugins/hosting/migrations/004_seed_default_static_profiles.py` (nuevo)
- `vendor/systutor-core/plugins/hosting/backend/tests/test_static_profiles_migration.py`
  (extender: upgrade/downgrade de 004, idempotencia, guard de perfil en uso)
- `vendor/systutor-core/plugins/hosting/README.md` (nota del catálogo default)
- `aspecs/0046-default-static-profiles.md`

No entra:
- Nuevos perfiles NO validados en lab (hugo, python http.server, vite, etc.)
  — cada uno requiere evidencia como la de 0042 antes de sembrar; el admin
  ya puede crearlos por la UI y correrlos (o quedan para specs propias con lab).
- Backend/frontend del plugin.
- Cambio del schema (003 intocada).

## CONTRACT

Precondiciones:
- Migración 003 aplicada (tabla existe; builtin nginx presente).

Postcondiciones:
1. Tras upgrade: `react`, `boot`, `astro` existen además de `nginx`.
2. Upgrade repetido no duplica ni pisa overrides del admin (si el admin
   editó una fila, el INSERT condicional no la toca).
3. Downgrade con perfiles en uso: filas intactas (guard), downgrade sin error.
4. Crear sitio `static` con cada perfil: argv de run con imagen/mount del
   perfil y health correspondiente (marker para react/boot según 0031,
   http_200 para astro); astro arranca por `sh -c <command>` con cd inline.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Las filas default describen exactamente los perfiles validados en el lab de 0042 (imagen, web_root, puerto, health, command).
    proof: test compara la fila insertada con las constantes del prototipo scripts/spanel_profiles/profiles.py (build_profiles()).
  - id: I2
    statement: El upgrade no toca filas editadas por el admin ni filas ya existentes.
    proof: test de upgrade sobre DB con fila pre-modificada; la imagen editada por el admin queda intacta.
  - id: I3
    statement: El downgrade no elimina perfiles en uso.
    proof: test downgrade con hosting_sites.profile_id apoyado en react → fila viva.
  - id: I4
    statement: El builtin nginx (003) no se ve afectado por 004.
    proof: test de upgrade tras 003；(hosting_static_profiles con 4 filas incluida nginx).
  - id: I5
    statement: Sin estado nuevo en el backend; todo comparte el código del estático parametrizado ya ahí (static.py/profiles.py).
    proof: diff sin archivos de backend; suites existentes verdes.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin && PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest vendor/systutor-core/plugins/hosting/backend/tests -q
cd /data/data/com.termux/files/home/Proyectos/mailadmin/apps/web && node node_modules/typescript/bin/tsc --noEmit
```

Tests nuevos en `test_static_profiles_migration.py` (SQLite): up/down/up + idempotencia + guard en uso + I4; y un test de contenido que compara el command de astro con el esperado (evita "me lo cambiaron a mano").

## ROLLBACK

`downgrade()` de 004 (respeta el guard) + `git revert`. Sin estado fuera de DB.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/hosting/migrations/004_seed_default_static_profiles.py
    - vendor/systutor-core/plugins/hosting/backend/tests/**
    - vendor/systutor-core/plugins/hosting/README.md
    - aspecs/0046-default-static-profiles.md
  prohibited:
    # (el backend raíz queda intocado; solo se extiende backend/tests/)
    - vendor/systutor-core/src/systutor/**
    - apps/**
    - scripts/**
    - docker-compose.yml
```
*(aclaración: los archivos perms del backend raíz quedan intocados; solo se extiende `backend/tests/`).*

## Traceability

- Requirement: catálogo default curado con la evidencia del lab de la 0042,
  para que el admin no parta de un catálogo casi vacío y el selector de
  "Crear sitio" ofrezca opciones reales desde el primer deploy.
- owner: luc444s
- Deployment: migration 004 en el arranque del plugin (mismo mecanismo que 003).

## Definition of Done

- [ ] Migración 004 con react/boot/astro exactos al lab (I1).
- [ ] Idempotencia + guard de downgrade (I2, I3) y nginx intacto (I4).
- [ ] Suites verde (V0) y tsc sin cambios.
- [ ] El gestor del admin lista los 4 tras refrescar.
