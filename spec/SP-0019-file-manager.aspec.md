# A.SPEC SP-0019 — File manager via filebrowser sidecar

## WHY

"Administrar los archivos desde el dashboard" (pedido del usuario).
File manager propio = superficie de bugs de seguridad; filebrowser
sidecar + forwardAuth resuelve con UI madura y auth del kernel
(arquitectura-base.md §6).

## WHAT

Una transición observable: para un Site wordpress, admin abre "Archivos"
desde el dashboard y navega/edita/subee wp-content a través de un
filebrowser dedicado del sitio, autenticado por el JWT de Spanel y
auditado.

## SCOPE

- Hosting: `POST /api/v1/plugins/hosting/sites/{id}/files/ensure` —
  crea container filebrowser (imagen oficial) compartiendo el volumen
  wp-content del sitio, solo red interna; idempotente.
- Proxy: ruta traefik `files.<dominio>` (o path interno) con
  `forwardAuth` → valida JWT Spanel (middleware contra
  `/api/v1/auth/me` u endpoint dedicado).
- Dashboard: enlace "Archivos" que abre el filebrowser embebido
  (iframe/proxy) sin pedir credenciales.
- Permisos kernel: `hosting.files.read` / `hosting.files.manage`
  (read: filebrowser en modo read-only).

## OUT OF SCOPE

- File manager propio, edición de archivos del kernel (nunca).
- Filebrowser para stacks no-wordpress (spec futura).
- Cuotas, versionado.

## CONTRACT

- PRE: site wordpress con volumen wp-content; traefik con forwardAuth
  (SP-0011).
- POST: filebrowser accesible SOLO con sesión Spanel válida; sin token →
  401 de forwardAuth; usuario sin permiso → 403; contenedor filebrowser
  removible con el sitio; operaciones auditadas (evento `files.accessed`).

## INVARIANTS

```yaml
invariants:
  - Filebrowser MUST NOT ser accesible sin forwardAuth (nunca puerto
    público directo).
  - Users sin hosting.files.read MUST NOT acceder.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
curl -s -o /dev/null -w "%{http_code}" https://files.<dominio>/   # 401 sin token
# con cookie/JWT de Spanel: 200
curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/files/ensure -H "Authorization: Bearer $TOKEN"
sshpass -p "$PW" ssh lucas@100.67.5.50 'docker ps --format "{{.Names}}"' | grep filebrowser
```

## ROLLBACK

`docker rm` del sidecar + quitar ruta traefik + `git checkout`.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
    - plugins/proxy/**
    - apps/web/src/**
  prohibited:
    - vendor/**
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.files
  indirect:
    - traefik.config (ruta nueva)
    - docker.remote (container sidecar)
  must_not_affect:
    - kernel.auth
    - sitio objetivo (solo lectura de volumen)
    - seguridad perimetral
```

## Traceability

- Requirement: arquitectura-base.md §6 (pedido explícito del usuario).
- Commit:
- Deployment: `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
