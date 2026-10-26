# A.SPEC 0032 — Create-site UI with type and validated domain

> `risk: normal` — solo cambia la UI de Hosting y consume contratos ya
> entregados. No crea ni borra recursos por sí misma. Owner y approver
> declarados; sin migración.
>
> Fase 3 de `docs/proposals/0030-crear-sitio-tipos-y-dominios.md`. Consume
> `kind` y el check de dominio de las A.SPECs 0030/0031.

## WHY

La API ya crea WordPress y estáticos, pero la pantalla sigue diciendo “Crear
sitio WordPress”, pide email y no valida el dominio contra el sufijo del
tenant. Tampoco distingue los tipos en el listado ni oculta acciones de
WordPress sobre un estático.

## WHAT

La página de Hosting expone un único flujo **Crear sitio** con exactamente tres
campos y trata ambos tipos como ciudadanos de primera:

1. El botón y el diálogo dicen **Crear sitio**.
2. Campos: **Tipo** (`WordPress`/`Estático`), **Nombre**, **Dominio**. No hay
   email ni contraseña.
3. El dominio se valida en vivo con `GET /domains/check` (debounce), mostrando
   disponibilidad, motivo o error. Crear se habilita solo con nombre y dominio
   disponible.
4. Al crear WordPress se muestra la contraseña una sola vez; al crear un
   estático se muestra su URL.
5. El listado muestra un badge de tipo y el detalle indica el tipo.
6. En un estático no se ofrecen Usuarios, Base de datos, Configuración WP,
   Salts ni phpMyAdmin. Sí se ofrecen Logs, inicio/parada/reinicio, Gestor de
   archivos y Eliminar.
7. El estado vacío deja de decir “sitios WordPress”.

## SCOPE

- Tipos y cliente HTTP de `hosting/frontend/api.ts`.
- Página `HostingPage.tsx`: diálogo de creación, check de dominio, éxito por
  tipo, badges y acciones por tipo.
- `SiteCard.tsx`: badge de tipo.
- Prueba de tipos/build.

## OUT OF SCOPE

- Backend de Hosting, migraciones o provisioning (A.SPEC 0031).
- Cambiar el endpoint `domains/check` o el namespace (A.SPEC 0030).
- Editar contenido estático desde la UI.
- `www`, DNS, certificados o Cloudflare.
- Rediseñar la navegación o el shell.

## CONTRACT

Precondiciones:

- A.SPEC 0030 y 0031 desplegadas en el backend.
- El usuario tiene `hosting.sites.create` para ver el botón.
- `GET /domains/check` responde `{available, reason, suffix}`.

Postcondiciones:

1. `HostingSite` incluye `kind: "wordpress" | "static"`.
2. `createHostingSite` envía `{kind, name, domain}` y ya no `admin_email`.
3. El diálogo tiene exactamente tres controles de datos: tipo, nombre, dominio.
4. El check se dispara con debounce de ~350 ms y solo para dominios de 4+
   caracteres; muestra:
   - validando;
   - disponible (con sufijo esperado);
   - no disponible con el `reason` del backend;
   - error de red/API.
5. El botón Crear está deshabilitado si falta nombre, el dominio no está
   disponible o hay una creación en curso.
6. WordPress: al cerrar el diálogo se abre el diálogo existente de contraseña
   generada. Estático: se abre un diálogo con `https://<dominio>` y un botón
   para abrirlo.
7. El listado muestra “WordPress” o “Estático” por sitio.
8. En un sitio estático, los botones WordPress-only no se renderizan; el resto
   sigue disponible.
9. Cerrar el diálogo limpia tipo, nombre, dominio y el estado del check.
10. No se envía `admin_email` ni se muestra un campo de email.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: El diálogo de creación sigue teniendo solo tipo, nombre y dominio.
    proof: inspección del JSX + tsc; no existe input de email en crear.
  - id: I2
    statement: Crear solo se habilita con un dominio disponible para el tenant.
    proof: lógica de disabled + prueba manual con dominio fuera del sufijo.
  - id: I3
    statement: WordPress conserva su diálogo de contraseña de una sola vez.
    proof: revisión del onSuccess y prueba manual de creación WordPress.
  - id: I4
    statement: Un estático no muestra acciones WordPress-only.
    proof: condición isStatic en el detalle; tsc.
  - id: I5
    statement: El listado y el detalle muestran el tipo correcto.
    proof: badge en SiteCard y campo Tipo en el detalle; tsc.
  - id: I6
    statement: El frontend no inventa el sufijo; lo toma del check del backend.
    proof: el texto de sufijo usa check.suffix; no hay constante de dominio.
  - id: I7
    statement: Cerrar o crear limpia el formulario y no deja un check viejo.
    proof: reset de estados en onSuccess/onClose + debounce reiniciado.
```

## VERIFICATION

### V0 — Tipos y build

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin/apps/web
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

### V1 — Contrato del diálogo

- El título es “Crear sitio”, no “Crear sitio WordPress”.
- Hay un select de tipo con WordPress y Estático.
- No hay `<Input type="email">` ni `admin_email` en el payload.
- El botón Crear está deshabilitado con nombre o dominio vacío.

### V2 — Check de dominio

- Escribir `paginadelucas.spanel` con sufijo `spanel` muestra disponible.
- Escribir `otro.com` muestra el motivo del backend.
- Un error 503/red se muestra como error, no como disponible.
- El texto menciona el `suffix` devuelto por el backend.

### V3 — Resultado por tipo

- WordPress: aparece el diálogo “Contraseña generada” con usuario `admin`.
- Estático: aparece el diálogo de sitio creado con `https://<dominio>`.
- En ambos casos el listado se refresca y el diálogo se cierra.

### V4 — Listado y detalle

- Un sitio WordPress muestra badge WordPress y todas sus acciones.
- Un sitio estático muestra badge Estático y solo Logs, operaciones,
  filemanager y Eliminar.
- El detalle muestra “Tipo”.
- El estado vacío no menciona WordPress.

### V5 — Regresión

- `tsc` y `vite build` verdes.
- El resto de las páginas no cambia.

## ROLLBACK

- Revertir el commit de la UI vuelve al diálogo anterior. El backend sigue
  aceptando `kind` y el default `wordpress`; no hay migración ni dato que
  deshacer.
- Los sitios estáticos ya creados siguen funcionando y se pueden borrar por
  API/CLI aunque la UI vieja no los distinga.

## Change Surface

```yaml
change_surface:
  allowed:
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/frontend/components/SiteCard.tsx
    - aspecs/0032-create-site-ui.md
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/**
    - vendor/systutor-core/plugins/domains/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/src/**
    - apps/web/**
    - docker-compose.yml
    - scripts/**
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - dialogo de creacion de sitios
    - tarjetas y detalle del listado de Hosting
  indirect:
    - llamada a GET /domains/check
    - refresh del listado tras crear
  must_not_affect:
    - backend, migraciones y provisioning
    - acciones de WordPress existentes
    - otras paginas del shell
    - contratos de tenant, domains y mail
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0030
    - 0031
  must_compose_with:
    - plugin domains (check)
    - plugin hosting (create/list/delete)
  systemic_invariants:
    - aislamiento por tenant
    - un dueno por Host
  composition_checks:
    - cd apps/web && node node_modules/typescript/bin/tsc --noEmit
    - cd apps/web && node node_modules/vite/bin/vite.js build
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la pagina sigue siendo el unico orquestador de la UI; no se agrega estado global
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/frontend/pages/HostingPage.tsx
    - vendor/systutor-core/plugins/hosting/frontend/api.ts
```

## Traceability

- Requirement: el panel crea sitios WordPress o estáticos con tipo, nombre y
  dominio validado, sin email, y distingue el ciclo de vida de cada tipo.
- owner: luc444s
- approver: luc444s
- Commit: pendiente (al ejecutar)
- Deployment: frontend local/Vite; producción con el build de la imagen.

## Definition of Done

- [ ] Objetivo: diálogo de 3 campos y tipos distinguidos.
- [ ] Alcance respetado: sin backend ni otras páginas.
- [ ] Contrato: check, éxito por tipo y acciones por tipo.
- [ ] Invariantes I1–I7 verificados.
- [ ] V0–V5 ejecutados.
- [ ] Sin archivos fuera de `change_surface`.
- [ ] Trazabilidad completa.
