# A.SPEC SP-0016 — Site detail view

## WHY

El usuario preguntó "¿de qué dirección viene?" — cada Site debe mostrar
su origen completo: URL pública, backend container:puerto, red, IP del
host, estado. Hoy no hay vista de detalle.

## WHAT

Una transición observable: desde la vista de sitios del plugin hosting,
admin abre el detalle de un Site y ve origen completo + estado vivo +
dominios + db asociada (si wp).

## SCOPE

- Backend hosting: `GET /api/v1/plugins/hosting/sites/{id}` enriquecido —
  devuelve Site + `origin` {public_urls[], backend, network, host_ip,
  container_status} (consulta docker-infra en vivo).
- Frontend del plugin (`frontend/register.ts` + vista): ruta
  `/p/hosting/sites/:id` con cards de origen, dominios (SP-0011 cuando
  existan) y acciones lifecycle (SP-0010) cuando estén.

## OUT OF SCOPE

- Gráficas/analytics, accesos (SP-0018), edición de dominios aquí
  (SP-0011 trae su propia vista).

## CONTRACT

- PRE: site adoptado; SP-0015 (runtime frontend) operativo.
- POST: detalle muestra fqdn(s), `container:puerto`, red docker, IP del
  host remoto, estado vivo; site de otro tenant → 404; container caído
  → estado reflejado sin romper el detalle.

## INVARIANTS

```yaml
invariants:
  - Tenant isolation MUST permanecer intacta.
  - Kernel MUST NOT ser modificado.
  - Datos sensibles (env del container) MUST NO exponerse.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id> -H "Authorization: Bearer $TOKEN" | jq .origin
# UI: /p/hosting/sites/<id> muestra origen completo
```

## ROLLBACK

`git checkout plugins/hosting apps/web`.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/**
    - apps/web/src/**
  prohibited:
    - vendor/**
    - apps/web/package.json
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.detail
  indirect:
    - web.shell.nav (nueva ruta)
  must_not_affect:
    - kernel.auth
    - docker.remote
```

## Traceability

- Requirement: arquitectura-base.md §4, §9 (detalle de origen, pedido
  explícito del usuario).
- Commit:
- Deployment: `npm run frontend` + `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
