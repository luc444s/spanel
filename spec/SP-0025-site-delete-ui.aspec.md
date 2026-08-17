# A.SPEC SP-0025 — Site delete UI

## WHY

Spanel ya adopta y provisiona Sites, pero no permite sacarlos del panel.
Sin una acción de eliminación segura, lista y detalle se llenan de registros
obsoletos y el admin vuelve a SQL/curl para limpiar estado administrativo.

## WHAT

Transición observable: admin elimina un Site desde la UI de hosting con
confirmación explícita; el Site desaparece de Spanel y de la navegación,
sin tocar containers remotos ni volúmenes del Docker remoto.

## SCOPE

### Backend (`plugins/hosting/backend/plugin.py`)

- `DELETE /api/v1/plugins/hosting/sites/{id}`:
  - elimina solo el registro `hosting_site` del tenant actual.
  - rechaza eliminación si el Site todavía tiene dominios asociados en
    `domains_json` → `409` con mensaje claro: primero eliminar dominios.
  - no ejecuta `docker rm`, `docker stop`, `docker volume rm` ni acciones
    destructivas sobre infraestructura remota.

### Frontend (`plugins/hosting/frontend/`)

- `SitesView.tsx`:
  - acción `Eliminar` por fila.
  - confirmación obligatoria mostrando nombre del Site y aviso: `solo lo
    quita de Spanel; no borra containers remotos`.
  - al éxito, quitar fila localmente o reconsultar lista.
- `SiteDetailView.tsx`:
  - acción `Eliminar de Spanel` en toolbar/header.
  - tras éxito, navegar a `/p/hosting/sites` con feedback visible.

## OUT OF SCOPE

- Destruir stacks provisionados (containers, redes, volúmenes).
- Eliminación automática de dominios proxy o buzones mail.
- Soft delete, papelera, restore.

## CONTRACT

- PRE: Site existe y pertenece al tenant actual.
- POST: Site deja de aparecer en `GET /sites`, detalle responde `404`, UI
  vuelve a la lista sin refresh global.
- Site con dominios todavía vinculados → `409`; la UI debe explicitar que
  primero se limpian dominios en el plugin proxy.
- Container remoto sigue intacto; si luego vuelve a descubrirse, puede
  adoptarse otra vez como Site nuevo.

## INVARIANTS

```yaml
invariants:
  - Delete administrativo MUST NOT tocar Docker remoto.
  - Tenant isolation MUST permanecer intacta.
  - `orquestador_ardi_postgres` MUST NOT recibir ninguna accion destructiva.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -X DELETE http://127.0.0.1:8001/api/v1/plugins/hosting/sites/<id> \
  -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/sites -H "Authorization: Bearer $TOKEN" | jq
# UI: eliminar desde lista y detalle remueve Site de Spanel sin tocar container remoto
```

## ROLLBACK

Revertir `DELETE /sites/{id}` y acciones de UI. Los containers remotos no
requieren rollback porque nunca se tocaron.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/frontend/**
    - plugins/hosting/backend/plugin.py
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/docker_infra/**
    - plugins/proxy/**
    - plugins/mail/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.delete.ui
    - hosting.site.registry
  indirect:
    - docker.discovery.re_adoptable
  must_not_affect:
    - docker.remote
    - kernel.auth
    - proxy.domains
    - mail plugin
```

## Traceability

- Requirement: arquitectura-base.md §1, §5, §9 (Spanel como espejo registrado del Docker remoto).
- Commit: (pending)
- Deployment: `npm run frontend` + `npm run services:no-reload`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
