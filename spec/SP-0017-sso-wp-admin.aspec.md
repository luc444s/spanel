# A.SPEC SP-0017 — SSO into wp-admin

## WHY

"Puedo entrar a WordPress con credenciales desde el dashboard" (pedido
del usuario). Guardar passwords wp en Spanel es riesgo innecesario;
magic link firmado + plugin en el WP es el camino (arquitectura-base.md
§6).

## WHAT

Una transición observable: admin presiona "Entrar a wp-admin" en el
detalle del Site; Spanel firma un token corto, el navegador llega a
`/wp-json/spanel/v1/sso?token=...`, el plugin `spanel-sso` valida y crea
sesión WP → el usuario queda logueado en wp-admin.

## SCOPE

- Backend hosting: `POST /api/v1/plugins/hosting/sites/{id}/sso` →
  token JWT (HS256, clave compartida por env, exp 60s, email del admin
  del site) + URL firmada.
- Plugin WordPress `spanel-sso` (repo/paquete nuevo, instalable vía
  wp-cli): endpoint REST `spanel/v1/sso` — valida firma/exp, resuelve
  usuario wp por email, `wp_set_auth_cookie`, redirect wp-admin.
- Instalación automática del plugin en adopt (si wp-cli disponible) y en
  provision (SP-0012).
- Site guarda `admin_email` (nunca password).

## OUT OF SCOPE

- SSO inverso (wp → Spanel), 2FA, expiración de sesiones wp.
- Rotación de password wp vía UI (spec futura).

## CONTRACT

- PRE: site wordpress con plugin spanel-sso instalado; clave SSO en env
  (distinta de la del JWT kernel).
- POST: enlace valida y loguea en <2s; token expirado/firma mala →
  401 del plugin wp; email sin usuario wp → error claro; Spanel nunca
  persiste password.

## INVARIANTS

```yaml
invariants:
  - Clave SSO MUST NO ser la misma que el JWT secret del kernel.
  - Token SSO MUST NO servir para llamar la API kernel (claims distintos).
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
TOKEN=$(...login...)
URL=$(curl -s -X POST http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id>/sso -H "Authorization: Bearer $TOKEN" | jq -r .url)
curl -sL "$URL" -o /dev/null -w "%{http_code} %{url_effective}"  # 200 wp-admin
```

## ROLLBACK

Desinstalar plugin spanel-sso (wp-cli) + `git checkout plugins/hosting`.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
    - plugins/spanel-sso-wp/**   # si vive en el repo; o repo aparte
  prohibited:
    - vendor/**
    - apps/web/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.sso
  indirect:
    - wp.site.session (cookie de sesión wp)
  must_not_affect:
    - kernel.auth
    - otros sites
```

## Traceability

- Requirement: arquitectura-base.md §6 (pedido explícito del usuario).
- Commit:
- Deployment: `npm run services:no-reload` + wp-cli plugin install.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
