# A.SPEC SP-0026 — Site adopt + provision UI

## WHY

SP-0009 y SP-0012 dejaron adopción y provisioning operativos en backend,
pero todavía no existe flujo UI para usarlos. El producto promete panel
web sobre Docker remoto; hoy creación/adopción de sitios sigue atada a curl.

## WHAT

Transición observable: desde hosting, admin puede crear un Site sin salir
del panel, eligiendo entre adoptar un container existente o provisionar un
WordPress nuevo. Al terminar, Spanel navega al Site creado/adoptado.

## SCOPE

### Frontend (`plugins/hosting/frontend/`)

- Nueva ruta `/p/hosting/sites/new`.
- Nuevo componente `SiteCreateView.tsx` con dos flujos independientes:

#### 1. Adoptar container existente

- Leer candidatos desde `GET /api/v1/plugins/docker_infra/containers?all_containers=true`.
- Leer Sites existentes desde `GET /api/v1/plugins/hosting/sites`.
- Mostrar solo containers no adoptados todavía.
- Tabla con: nombre, imagen, estado, sugerencia de stack.
- Campo opcional `Nombre visible` antes de enviar `POST /sites/adopt`.
- `orquestador_ardi_postgres` visible solo como container protegido,
  deshabilitado para adoptar sin aprobación explícita fuera de esta spec.

#### 2. Provisionar WordPress

- Formulario: `name`, `admin_email`, `admin_user?`, `domain?`.
- Submit a `POST /sites/provision/wordpress`.
- Respuesta exitosa muestra dialog/banner con `admin_user` y
  `admin_password` una sola vez antes de navegar al detalle del Site.
- Nota visible: `domain` es opcional; si se carga, se usa el flujo dual de
  SP-0023.

### Navegación

- `SitesView.tsx` agrega CTA visible: `Nuevo sitio`.
- `register.tsx` agrega ruta `sites/new` sin crear item extra de menú.

## OUT OF SCOPE

- Provision de stacks no WordPress.
- Compose multi-container genérico.
- Destruir o editar Sites existentes.
- Discovery reconciliado en background.

## CONTRACT

- PRE: plugin hosting operativo; plugin docker_infra habilitado; docker
  remoto accesible.
- POST: admin puede completar adopción o provisioning desde UI; éxito
  navega al detalle del Site correspondiente.
- Adopt de container ya registrado → `409` visible en formulario.
- Provision exitoso entrega credenciales una sola vez; la UI no las vuelve
  a consultar después de cerrar el dialog.
- Container protegido no debe poder adoptarse desde esta pantalla.

## INVARIANTS

```yaml
invariants:
  - Adopcion MUST seguir sin modificar container remoto.
  - Provision WordPress MUST seguir usando backend existente; UI no replica logica de provision.
  - `orquestador_ardi_postgres` MUST permanecer bloqueado.
  - `domain` en provision MUST seguir siendo opcional.
  - Kernel MUST NOT ser modificado.
```

## VERIFICATION

```bash
cd apps/web && npm run build
curl -s http://127.0.0.1:8001/api/v1/plugins/docker_infra/containers?all_containers=true -H "Authorization: Bearer $TOKEN" | jq '.[0]'
curl -s http://127.0.0.1:8001/api/v1/plugins/hosting/sites -H "Authorization: Bearer $TOKEN" | jq
# UI: /p/hosting/sites/new permite adoptar un container no registrado
# UI: /p/hosting/sites/new permite provisionar WP y muestra credenciales una sola vez
```

## ROLLBACK

Revertir ruta `sites/new`, `SiteCreateView.tsx` y CTA `Nuevo sitio`. Los
endpoints backend de SP-0009/SP-0012 permanecen intactos.

## Change Surface

```yaml
change_surface:
  allowed:
    - plugins/hosting/frontend/**
  prohibited:
    - vendor/**
    - apps/web/**
    - plugins/hosting/backend/**
    - plugins/docker_infra/backend/**
    - plugins/proxy/**
    - plugins/mail/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - hosting.site.create.ui
    - hosting.site.adopt.ui
    - hosting.site.provision.ui
  indirect:
    - docker_infra.containers.read
  must_not_affect:
    - kernel.auth
    - hosting.lifecycle
    - proxy.domains
    - mail plugin
```

## Traceability

- Requirement: arquitectura-base.md §1, §5, §6, §9; extensión de SP-0009, SP-0012 y SP-0023.
- Commit: (pending)
- Deployment: `npm run frontend`.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] No unrelated changes
- [ ] Traceability established
