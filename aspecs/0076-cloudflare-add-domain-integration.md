# A.SPEC 0076 — Integrar provisión Cloudflare en “Add domain” (modal anidado) con fallback PowerDNS

> `risk: high` — invoca provisión de DNS público en Cloudflare desde la UI de alta de dominios.
> Human approver: `luc444s`.
> Depende de: A.SPEC 0075 (capacidad `provision plan/apply` en CLI/backend).

## WHY

A.SPEC 0075 dejó lista la provisión Cloudflare (zona/registros DNS-only, `plan/apply`) en CLI y FastAPI, pero su uso desde la interfaz “Add domain” quedó fuera de alcance. El operador necesita poder agregar un dominio y, **solo si Cloudflare está activo para el tenant/dominio**, configurar y aplicar la provisión DNS en Cloudflare desde la UI sin duplicar política; cuando no aplique, el flujo debe caer al comportamiento actual (gestión local/PowerDNS según corresponda) manteniendo el diálogo limpio.

## WHAT

Integrar la capacidad de provisión de 0075 en el flujo “Add domain” de Domains:

1. **Detección y fallback**
   - “Cloudflare activo/elegible” (para ofrecer provisión): el tenant tiene conexión Cloudflare activa (`/dns/cloudflare/status`), con permisos de provisión disponibles (incl. `Zone Write`, sin `needs_repair` que impida provisión). Si no es elegible, no se ofrece provisión automática y el alta usa el flujo actual (fallback PowerDNS/local).
   - Si está conectado pero requiere reparación/reconexión para provisión (p.ej. falta `Zone Write` / `needs_repair`), se muestra estado claro y acceso a conectar/reparar; no se permite `apply`.

2. **UI “Add domain” (limpia) + configuración en modal anidado**
   - El modal principal de “Add domain” mantiene el formulario base (dominio, destino, `www`) y muestra un bloque resumen de Cloudflare.
   - Cuando Cloudflare está activo/elegible, el bloque incluye el botón **“Configurar Cloudflare”** que abre un **modal anidado** (z-index superior, cierre independiente). El plan/detalle largo vive en el modal anidado, no en el principal.
   - El modal anidado captura/permite editar la **IPv4 origen pública explícita** (no inferida), respeta `www`, ejecuta `plan` (solo lectura) y `apply` (mutación explícita) contra los endpoints de 0075, y muestra resumen/detalle, conflictos, `replace` explícito y nameservers cuando se crea zona.
   - Tras aplicar (o cerrar), el modal principal refleja el estado (p.ej. “DNS aplicado en Cloudflare” / “pendiente de aplicar” / error) sin recargarlo de detalle.

3. **Semántica de provisión (sin duplicar política)**
   - Usar exclusivamente `POST /api/v1/plugins/domains/dns/cloudflare/provision/plan` y `.../apply` (0075). `proxied=false` siempre, `ttl` auto, conflictos bloqueados salvo `replace=true` explícito, plan recalculado en apply, idempotente.
   - IPv4 provista explícitamente por el usuario (puede prellenarse desde configuración conocida si existe, pero siempre visible/editable y enviada).
   - No inferir IP, no cambiar delegación/registrar, no tocar PowerDNS/Traefik/certificados/local domain reservation como parte de la provisión CF.

4. **Orden y consistencia con el alta local**
   - El alta local (`POST /api/v1/plugins/domains`, reserva/publicación Traefik) y la provisión CF son operaciones distintas. La UI debe orquestarlas con estados claros:
     - flujo recomendado: **plan CF → apply CF explícito (en modal anidado) → alta local “Add”** (la provisión CF puede quedar aplicada antes del alta local);
     - reintentos seguros: provisión CF idempotente (0075) y alta local tolerante a reintento según comportamiento existente; ante fallo parcial mostrar mensaje explícito (p.ej. DNS en CF aplicado pero falló alta local, reintentar alta).
   - No se reclama atomicidad cross-system que no exista.

5. **Fallback PowerDNS/actual**
   - Si Cloudflare no está activo/elegible para el dominio/tenant, no se muestra “Configurar Cloudflare” habilitado para provisión; se indica que no hay provisión CF y se usa la gestión DNS local/PowerDNS cuando corresponda. El alta del dominio sigue el comportamiento actual.

## DIAGRAMAS (confirmados)

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Dominios y DNS                                                [ + Add domain ]│
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                         ┌──────────────────────────┐
                         │  ¿Cloudflare activo/     │
                         │   elegible para provisión│
                         └────────────┬─────────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 │ SÍ                                      │ NO
                 ▼                                         ▼

CASO A — Cloudflare activo (flujo limpio: modal principal + modal anidado)

┌─ Add domain ─────────────────────────────────────────────── [x] ┐
│  Dominio / Destino / [x] www                                   │
│  ┌─ Cloudflare ─────────────────────────────────────────────┐  │
│  │ ● Activo · dominio elegible                              │  │
│  │ DNS: sin configurar en este alta (opcional)              │  │
│  │                                    [ Configurar Cloudflare ]│
│  └──────────────────────────────────────────────────────────┘  │
│                                      [ Cancelar ]     [ Add ]  │
└────────────────────────────────────────────────────────────────┘
        │ clic "Configurar Cloudflare"
        ▼
        ┌─ Configurar Cloudflare (modal anidado, z superior) ────────── [x] ┐
        │  IPv4 origen (explícita)        [x] www                           │
        │  [ 203.0.113.10 ]                [ Ver plan ]                     │
        │  ┌─ Resumen / Plan (compacto) ────────────────────────────────┐   │
        │  │ Zona: reutilizar/crear · registros A[/CNAME] · drift/conf. │   │
        │  │ DNS only (proxied=false) · [ Ver detalle ▾ ]               │   │
        │  │ (si zona nueva) Nameservers: ns1, ns2 → delegar registrar  │   │
        │  └────────────────────────────────────────────────────────────┘   │
        │  [ ] Reemplazar en conflicto (replace, explícito)                  │
        │                              [ Cerrar ]  [ Aplicar DNS en CF ]    │
        └───────────────────────────────────────────────────────────────────┘
        │ aplicar OK / cerrar
        ▼
┌─ Add domain ─────────────────────────────────────────────── [x] ┐
│  Cloudflare: ✓ DNS aplicado (o pendiente) [ Configurar Cloudflare ]│
│                                      [ Cancelar ]     [ Add ]  │
└────────────────────────────────────────────────────────────────┘

CASO B — Sin Cloudflare activo (fallback PowerDNS / flujo actual)

┌─ Add domain ─────────────────────────────────────────────── [x] ┐
│  Dominio / Destino / [x] www                                   │
│  ┌─ DNS ────────────────────────────────────────────────────┐  │
│  │ Cloudflare no activo/elegible para este dominio          │  │
│  │ → gestión DNS local/PowerDNS según corresponda           │  │
│  │        [ Conectar Cloudflare ] (si aplica/permiso)       │  │
│  └──────────────────────────────────────────────────────────┘  │
│   (sin provisión CF)                 [ Cancelar ]     [ Add ]  │
└────────────────────────────────────────────────────────────────┘
```

Layout: en escritorio el modal principal puede ensancharse y, si se requiere, usar 2 columnas; el detalle del plan es compacto/plegable dentro del modal anidado para evitar altura excesiva. En móvil se apila en 1 columna.

## SCOPE

- Frontend Domains plugin: `DomainsPage` (Add domain) + bloque Cloudflare y modal anidado “Configurar Cloudflare”, cliente API/tipos para `provision/plan|apply`, estado de elegibilidad (status/client/zonas según sea necesario), i18n de nuevas cadenas.
- Reutilizar endpoints 0075 existentes; sin duplicar política de provisión en frontend.
- Ajustes backend mínimos solo si faltan datos estrictamente necesarios para elegibilidad/UX ya no expuestos (preferir reutilizar contratos existentes). Sin cambios a política 0075.
- Pruebas: unit/integración frontend (lógica/estado del flujo), pruebas backend/router/provisioning existentes deben seguir pasando, y **test e2e** del flujo de alta de dominio con rama Cloudflare activa (plan/apply mockeado o contra entorno controlado) y fallback sin Cloudflare.

## OUT OF SCOPE

- Cambios en política de provisión 0075 (reglas de conflicto/compensación/proxied/registrar) o en `scripts/spanel_dns.py` salvo adaptación trivial necesaria.
- Inferir IPv4 automáticamente, IPv6, proxy Cloudflare, registrar/NS automation, reconciliación/background.
- Modificar PowerDNS, Traefik, emisión de certificados o reserva local más allá de la orquestación/UI ya descrita.
- Refactors no relacionados en Cloudflare service/dns legacy.

## CONTRACT

### Preconditions
1. 0075 desplegado: endpoints `provision/plan|apply` operativos y cliente OAuth con scopes requeridos (repair+reconnect realizados cuando aplique).
2. Para aplicar provisión: conexión CF del tenant activa y con `Zone Write`; IPv4 explícita proporcionada por el usuario.

### Postconditions
1. Si CF no activo/elegible: “Add domain” se comporta como hoy (fallback), sin mutaciones en Cloudflare.
2. Si CF activo/elegible y el usuario aplica: se invoca 0075 con `{domain, ipv4, www, replace}` correcto, se muestran zona/registros/acciones/nameservers según plan/apply, y el estado queda reflejado en el modal principal.
3. `replace=true` solo por acción explícita del usuario ante conflicto.
4. La UI no envía `proxied=true` ni infiere IP ni delegación.
5. Fallos parciales (CF vs alta local) se reportan claramente; reintentos son seguros donde 0075/alta local lo garantizan.

## INVARIANTS

```yaml
invariants:
  - Frontend no duplica política de provisión: plan/apply/drift/conflictos los decide 0075.
  - Toda provisión CF pasa por endpoints 0075; proxied siempre false.
  - IPv4 es explícita en la solicitud de provisión; nunca inferida en UI.
  - replace solo con confirmación explícita ante conflicto.
  - Sin Cloudflare activo/elegible no hay mutación CF; fallback al flujo actual.
  - CloudflareClient sigue siendo el único transporte CF (sin cambios de transporte en UI).
  - No se alteran RBAC/tenancy ni contratos de PowerDNS/Traefik/certificados/reserva local.
```

## VERIFICATION

Desde la raíz del repo (ajustar paths de e2e según runner elegido):

```bash
# Frontend (dominios/cloudflare)
cd apps/web && node node_modules/vitest/vitest.mjs run src/plugins/domains-cloudflare.logic.test.ts 2>&1 | tail -40
cd apps/web && node node_modules/typescript/bin/tsc --noEmit 2>&1 | tail -40

# Backend Domains/Cloudflare (no regresión 0075)
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_cloudflare_client.py plugins/domains/backend/tests/test_cloudflare_service.py plugins/domains/backend/tests/test_cloudflare_provisioning.py plugins/domains/backend/tests/test_cloudflare_router.py plugins/domains/backend/tests/test_cloudflare_cli.py -q
cd vendor/systutor-core && ../../.venv/bin/python -m pytest plugins/domains/backend/tests/test_dns_service.py plugins/domains/backend/tests/test_dns_router.py -q

# E2E (flujo Add domain: CF activo -> plan/apply vía mock/controlado; fallback sin CF)
# Implementar/extender script o suite e2e de dominios; ejecutar en entorno de test con
# backend y dependencias mockeadas/controladas para Cloudflare (sin mutar DNS público real).
python3 -m pytest scripts/tests/test_*domains*e2e* -q 2>&1 | tail -60 || true

cd vendor/systutor-core && ../../.venv/bin/python -m ruff check plugins/domains/backend/cloudflare plugins/domains/backend/cloudflare_router.py plugins/domains/backend/cloudflare_schemas.py plugins/domains/backend/tests/test_cloudflare_*.py
git diff --check
```

> E2E de aplicación real contra Cloudflare público es prueba de aceptación del operador (muta DNS); los e2e automatizados deben usar transporte/control doble o entorno aislado.

## ROLLBACK

Desactivar/ocultar el bloque y modal de provisión CF en “Add domain” (o revertir integración frontend) y volver al flujo actual; endpoints 0075 permanecen disponibles por CLI/API. Registros/zonas ya aplicados en Cloudflare no se revierten automáticamente (usar CRUD existente o procedimiento manual). Reconexiones/repair de OAuth no se revierten por rollback de código.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0076-cloudflare-add-domain-integration.md
    - apps/web/src/plugins/domains-cloudflare.logic.test.ts
    - vendor/systutor-core/plugins/domains/frontend/**
    - vendor/systutor-core/plugins/domains/README.md
    - vendor/systutor-core/plugins/domains/README.es.md
    - scripts/tests/test_*domains*e2e*  # e2e de integración del flujo (si se añade)
    - scripts/e2e_*domains*  # runner e2e (si se añade)
  allowed_if_strictly_required:
    - vendor/systutor-core/plugins/domains/backend/cloudflare_schemas.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare_router.py
  prohibited:
    - vendor/systutor-core/plugins/domains/backend/cloudflare/client.py
    - vendor/systutor-core/plugins/domains/backend/cloudflare/provisioning.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli.py
    - vendor/systutor-core/plugins/domains/backend/dns/cli_commands.py
    - installer/**
    - docker-compose.yml
    - docker-compose.powerdns.yml
    - .env*
    - vendor/systutor-core/src/systutor/**
    - vendor/systutor-themes/**
```

## Traceability

- Requirement: integrar provisión Cloudflare de 0075 en “Add domain” con modal anidado, plan/apply explícito y fallback PowerDNS/actual.
- Owner: Domains plugin
- Approver: luc444s
- Builds on: A.SPEC 0075 (083809d, 650d759)
- Status: draft → implementación en curso

## Definition of Done

- [ ] Objetivo y alcance satisfechos (UI integrada, fallback correcto)
- [ ] Contrato e invariantes respetados (sin inferir IP, proxied false, replace explícito, sin duplicar política)
- [ ] Plan/apply de 0075 usados desde UI con estados claros (éxito, conflicto, zona nueva/nameservers, needs_repair)
- [ ] Pruebas frontend y no regresión backend/0075 en verde
- [ ] Test e2e del flujo implementado y en verde (entorno controlado/mock, sin DNS público real)
- [ ] Verificación pasada, rollback honesto, sin cambios no relacionados
- [ ] Traceability establecida

## Discusión abierta (protocolo)

- Fuente de prellenado de IPv4 por defecto (config/setting) si se desea UX más rápida, manteniendo edición explícita.
- Punto exacto de orquestación apply-CF vs alta local en el submit (aplicar antes de “Add” vs como parte atómica de UX) y mensajes de fallo parcial.
- Criterio fino de “dominio elegible” para mostrar provisión (p.ej. match con zona listada/creable) además de conexión activa.
