# A.SPEC 0061 — Consola frontend por servicio y E2E real

> `risk: high` — expone lifecycle, dominios y secretos administrados desde la
> interfaz. Depende de A.SPEC 0060 y no autoriza cambios de API, modelos, worker,
> runtime o Dominios.

## WHY

La vista de Apps muestra servicios dinámicos, pero concentra overview,
deployments, operaciones, logs y lifecycle dentro del modal de aplicación. El
tenant necesita abrir cada servicio, consultar su estado, operar únicamente ese
servicio, vincular dominios registrados mediante Dominios y revelar de forma
controlada sus secretos administrados.

La jerarquía visual debe dejar claro qué acciones afectan a todo el proyecto
Compose y cuáles afectan sólo a un servicio.

## DEPENDENCY

Esta spec comienza únicamente cuando A.SPEC 0060 está implementada y verde.
Frontend consume sus contratos exactos y la API frontend existente de Dominios.

No crear:

- endpoints o modelos backend;
- un cliente alternativo de Dominios;
- otro editor de rutas;
- otro sistema de polling;
- otro store global;
- componentes genéricos si shell ya ofrece Dialog, Button, Alert o toast;
- nombres fijos frontend/backend/database en la lógica productiva.

## SCOPE

### 1. Modal de aplicación

El modal de aplicación conserva únicamente contexto y acciones globales:

- Resumen;
- Auditoría;
- Deployments;
- Operaciones globales;
- Inspeccionar;
- Desplegar;
- Actualizar todo;
- Down;
- Eliminar aplicación.

Las tarjetas se generan desde `active_deployment.service_details`. Doble clic,
botón **Abrir servicio**, Enter y Espacio abren el mismo modal de servicio.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores                                      ● Running    ✕ │
│ luc444s/software-de-gestion-para-editores · main                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Resumen] [Auditoría] [Deployments] [Operaciones]                          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Fuente            GitHub · conectado                                        │
│ Compose           docker-compose.yml                                         │
│ Commit activo     8d742bc                                                    │
│ Proyecto runtime  app-14aaf4b59a094c76ade26cd6                              │
│ Último deploy     9 nov 2026 · 18:55                                         │
│                                                                              │
│ SERVICIOS                                                                    │
│                                                                              │
│ ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐         │
│ │ frontend        ●  │ │ backend         ●  │ │ database        ●  │         │
│ │ nginx:alpine       │ │ api:latest         │ │ postgres:16         │         │
│ │ Healthy            │ │ Healthy            │ │ Healthy             │         │
│ │ Puertos: 80        │ │ Puertos: 8000      │ │ Puertos: 5432       │         │
│ │ 1 dominio          │ │ 1 dominio          │ │ Sin dominios        │         │
│ │                    │ │                    │ │                     │         │
│ │ [Abrir servicio]   │ │ [Abrir servicio]   │ │ [Abrir servicio]    │         │
│ └────────────────────┘ └────────────────────┘ └────────────────────┘         │
│                                                                              │
│ Doble clic sobre una tarjeta para administrarla.                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Inspeccionar] [Desplegar] [Actualizar todo]    [Down] [Eliminar aplicación]│
└──────────────────────────────────────────────────────────────────────────────┘
```

No mover Inspeccionar, Auditoría, Deployments, Desplegar, Down o Eliminar al
servicio. Esas acciones afectan al Compose completo.

### 2. Modal de servicio — Overview

Tabs exactos:

```text
Overview | Variables | Dominios | Logs | Operaciones
```

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores / backend                            ● Healthy     ✕ │
│ Servicio Compose: backend                                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Variables] [Dominios] [Logs] [Operaciones]                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Estado             Running · Healthy                                        │
│ Imagen             spanel-app-backend:latest                                │
│ Creado             9 nov 2026 · 18:55                                       │
│ Reinicios          0                                                         │
│ Puertos internos   8000/tcp                                                 │
│ Última revisión    hace 8 segundos                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Actualizar] [Solicitar logs] [Reiniciar] [Detener]                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

No mostrar IP, Docker ID, comandos, red editable ni nombre efímero del
contenedor. Si el servicio está detenido, mostrar **Iniciar** en vez de
**Detener**. Toda mutación usa idempotency key, cierra/actualiza optimistamente
según patrones existentes y sigue la operación mediante polling existente.

### 3. Variables y secretos

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores / backend                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Variables] [Dominios] [Logs] [Operaciones]                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Buscar variable...  [________________________________]  [Solo secretos ▾]   │
│                                                                              │
│ VARIABLE              VALOR                     ORIGEN             ACCIONES   │
│ DATABASE_URL          •••••••••••••••••••       Administrada       [Ver]     │
│ JWT_SECRET_KEY        •••••••••••••••••••       Administrada       [Ver]     │
│ API_KEY               •••••••••••••••••••       Tenant             [Ver]     │
│ CORS_ORIGINS          Configurada                Tenant             —         │
│ RUN_DB_MIGRATIONS     Configurada                Tenant             —         │
│ PATH                  Protegida por plataforma   Imagen             —         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Esta spec no añade edición de variables ni botón **Desplegar cambios**. Sólo
lista metadata existente y revela valores permitidos por A.SPEC 0060.

La columna **ORIGEN** usa exactamente `service_details[].environment[].source`:
`managed` → **Administrada**, `tenant` → **Tenant**, `image` → **Imagen**.
**Ver** sólo aparece cuando `revealable=true`; `secret` sólo controla el
enmascarado inicial. No se infiere ninguna de las dos cosas en el frontend.

Confirmación exacta:

```text
┌───────────────────────────────────────────────────────────┐
│ Revelar JWT_SECRET_KEY                                ✕   │
├───────────────────────────────────────────────────────────┤
│ Esta acción quedará registrada en la auditoría.           │
│                                                           │
│ El valor podría permitir acceso a datos de la aplicación. │
│ No lo compartas ni lo incluyas en logs.                    │
│                                                           │
│ Motivo *                                                   │
│ [Depuración del deployment____________________________]   │
│                                                           │
│                         [Cancelar] [Revelar durante 30 s]  │
└───────────────────────────────────────────────────────────┘
```

Después de revelar:

```text
│ JWT_SECRET_KEY   eyJhbGciOiJIUzI1Ni...   [Copiar] [Ocultar]  00:27 │
```

Reglas frontend:

- valor sólo en estado local del componente;
- nunca React Query cache, URL, toast, storage o analytics;
- ocultamiento automático máximo 30 segundos;
- limpiar al cambiar tab, cerrar modal, cambiar aplicación o desmontar;
- no imprimir valor en errores;
- no ofrecer reveal sin `apps.secrets.read`;
- copiar exige acción explícita.

### 4. Dominios

La pestaña importa y reutiliza la API, tipos, queries y mutaciones existentes de
Dominios. Apps no implementa validación de ownership, certificados, conflictos,
middlewares o entrypoints.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores / backend                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Variables] [Dominios] [Logs] [Operaciones]                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ RUTAS PUBLICADAS                                                             │
│                                                                              │
│ HOST                   PATH    DESTINO          HTTPS   ESTADO     ACCIONES  │
│ api.midominio.com      /       backend:8000     Sí      ● Activa   [Editar]  │
│ midominio.com          /api    backend:8000     Sí      ● Activa   [Editar]  │
│                                                                              │
│ Certificado de api.midominio.com                                             │
│ Let's Encrypt · válido hasta 8 feb 2027 · Verificado ✓                       │
│                                                                              │
│ [+ Asignar dominio registrado]                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

El selector muestra únicamente dominios registrados devueltos por Dominios.
No existe campo libre para inventar host dentro de Apps. Un link secundario
**Administrar dominios ↗** navega a `/app/domains`.

Formulario exacto:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Asignar dominio a backend                                                ✕ │
├──────────────────────────────────────────────────────────────────────────────┤
│ Dominio registrado *                                                        │
│ [ api.midominio.com                                                   ▾ ]    │
│                                               [Administrar dominios ↗]       │
│                                                                              │
│ Path externo                                                                │
│ [ /api                                                                  ]   │
│                                                                              │
│ Internal Path                                                               │
│ [ /                                                                      ]   │
│                                                                              │
│ [✓] Strip Path                                                              │
│ Elimina `/api` antes de reenviar la solicitud.                               │
│                                                                              │
│ Container Port *                                                            │
│ [ 8000                                                                  ]   │
│ Puertos detectados: [8000]                                                   │
│                                                                              │
│ Custom Entrypoints                                                          │
│ [✓ web]  [✓ websecure]                                                       │
│                                                                              │
│ HTTPS                                                                       │
│ [✓] Provisionar certificado automáticamente                                 │
│                                                                              │
│ Middlewares                                                                 │
│ [ rate-limit@file                                             ▾ ] [Añadir]  │
│ Aplicados: [rate-limit@file ×]                                               │
│                                                                              │
│ PREVIEW                                                                     │
│ https://api.midominio.com/api/*                                              │
│                  ↓ Strip /api                                                │
│ backend:8000/*                                                              │
│                                                                              │
│ Nota: con Path externo `/` no se puede quitar la raíz. Si Internal Path      │
│ es `/v1`, la preview muestra `backend/* → /v1/*`; Strip Path no cambia eso.  │
│                                                                              │
│ VALIDACIÓN                                                                  │
│ ✓ El dominio pertenece al tenant                                             │
│ ✓ No existe conflicto para host + path                                       │
│ ✓ El puerto está declarado por backend                                       │
│ ✓ Dominios puede alcanzar el servicio                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                             [Cancelar] [Probar ruta] [Guardar y publicar]    │
└──────────────────────────────────────────────────────────────────────────────┘
```

Las opciones disponibles y mensajes de validación proceden de Dominios. Apps no
crea middlewares, entrypoints o certificados. Los selectores de entrypoints y
middlewares se llenan exclusivamente con `entrypoints` y `middlewares` de
`GET /domains/available`; no se aceptan valores libres. Un 422 de Dominios se
muestra sin convertirlo en éxito. **Probar ruta** usa el probe existente.
**Guardar y publicar** usa la mutación existente. Editar y eliminar usan
PATCH/DELETE existentes y requieren confirmación cuando corresponda.

Conflicto:

```text
┌──────────────────────────────────────────────────────────────┐
│ No se puede publicar la ruta                             ✕   │
├──────────────────────────────────────────────────────────────┤
│ api.midominio.com/api ya está asignado.                       │
│                                                              │
│ Cambia el dominio o el path antes de continuar.               │
│                                                     [Cerrar] │
└──────────────────────────────────────────────────────────────┘
```

No revelar nombre, tenant o aplicación ajena en un conflicto cross-tenant.

### 5. Logs

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores / backend                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Variables] [Dominios] [Logs] [Operaciones]                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ Líneas [200 ▾]  [Buscar________________] [Solicitar logs]                    │
│                                                                              │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ 18:55:03 INFO  Starting API server                                       │ │
│ │ 18:55:04 INFO  Database connection established                           │ │
│ │ 18:55:04 INFO  Listening on 0.0.0.0:8000                                 │ │
│ │ 18:56:22 INFO  GET /health 200                                            │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ Los secretos conocidos se muestran redactados automáticamente.               │
└──────────────────────────────────────────────────────────────────────────────┘
```

No implementar streaming, descarga ni follow en esta spec. **Solicitar logs**
crea la operación async existente limitada al servicio y presenta el último
resultado redactado.

### 6. Operaciones

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Software para Editores / backend                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Variables] [Dominios] [Logs] [Operaciones]                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ OPERACIÓN       ESTADO       FASE          INICIO             DURACIÓN       │
│ restart         ✓ Exitosa    complete      18:58:10           1.8 s          │
│ logs            ✓ Exitosa    complete      18:57:43           0.2 s          │
│ stop            ✓ Exitosa    complete      18:50:01           0.9 s          │
│ start           ✓ Exitosa    health        18:50:05           18.4 s         │
│                                                                              │
│ ┌ Detalle: start ──────────────────────────────────────────────────────────┐ │
│ │ Servicio:    backend                                                     │ │
│ │ Resultado:   servicio iniciado y healthcheck aprobado                   │ │
│ │ Operation:   11db2ab5-5540-4fde-a10e-cc4656a94cc0                       │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

Sólo aparecen operaciones globales aplicables o cuyo subject corresponde al
servicio. Nunca se presenta un secreto dentro de `result`, error o detalle.

## COMPONENTES

Crear únicamente componentes que representan responsabilidades visuales nuevas:

```text
components/ServiceCard.tsx
components/ServiceDialog.tsx
components/ServiceOverview.tsx
components/ServiceEnvironment.tsx
components/SecretRevealDialog.tsx
components/ServiceDomains.tsx
components/ServiceLogs.tsx
components/ServiceOperations.tsx
```

No crear un hook, store, cliente o tipo duplicado si el archivo actual puede
importarse directamente. `ApplicationDetail.tsx` conserva la orquestación de la
aplicación y delega las responsabilidades de servicio.

## RBAC

```text
ver aplicación/servicios     apps.applications.read
lifecycle/logs por servicio  apps.applications.manage
revelar secreto              apps.secrets.read
listar dominios              domains.domains.read
asignar/editar/quitar ruta   domains.domains.manage + apps.applications.manage
deploy                       regla existente de Apps + plataforma
delete                       regla existente de Apps + plataforma
```

Ocultar controles no sustituye la autorización backend. Un 403/404 se presenta
con error público seguro y no cambia estado local a éxito.

## E2E REAL

Extender `scripts/e2e_apps_api.py`; no crear otro runner que duplique login,
GitHub, polling, deployment y cleanup existentes.

Fixture real:

```text
luc444s/software-de-gestion-para-editores
```

Flujo obligatorio sin mocks:

1. Login real por HTTPS y validación de permisos.
2. Instalación y repositorio GitHub reales.
3. Crear aplicación temporal.
4. Inspect real.
5. Deploy real y health de todos los servicios dinámicos.
6. Elegir un servicio HTTP desde puertos detectados, sin asumir su nombre.
7. Elegir un dominio de laboratorio ya registrado en Dominios.
8. Crear ruta con la API existente de Dominios.
9. Probe y petición HTTPS real con validación de certificado, sin `-k`.
10. Validar path/internal path/strip path según la ruta creada.
11. Solicitar logs del servicio y comprobar redacción de canarios.
12. Reiniciar sólo el servicio elegido.
13. Confirmar que los otros servicios no fueron recreados/reiniciados.
14. Stop/start sólo del servicio y nueva comprobación health.
15. Revelar un secreto administrado con motivo.
16. Confirmar auditoría sin valor secreto.
17. Ejecutar redeploy real.
18. Confirmar que la misma ruta resuelve al contenedor activo nuevo.
19. Eliminar la ruta mediante Dominios.
20. Eliminar la aplicación con purga, incluso si una fase previa falla.
21. Confirmar ausencia de contenedores, redes, volúmenes, rutas, estado y
    archivos temporales pertenecientes al E2E.

La validación visual complementaria debe comprobar en el panel real:

- apertura por doble clic, botón y teclado;
- tabs y botones exactamente como esta spec;
- secreto se oculta al cerrar/cambiar tab;
- selector muestra sólo dominios registrados;
- una mutación aceptada se observa por polling sin bloquear el modal.

No presentar mocks, snapshots o build frontend como evidencia E2E. Son gates,
pero la evidencia final es la ejecución real completa.

## OUT OF SCOPE

- Cambios backend no definidos por A.SPEC 0060.
- Crear dominio desde Apps.
- Terminal o Docker exec.
- Streaming/follow/download de logs.
- Métricas, CPU, RAM, shell o escalado.
- Edición/rotación de variables.
- Deploy de un servicio aislado.
- Nuevos middlewares o entrypoints.
- Publicación automática por heurística.
- Diseño alternativo al ASCII de esta spec sin aprobación.

## INVARIANTS

```yaml
invariants:
  - id: F1
    statement: Acciones globales y de servicio son visualmente distintas.
    proof: ApplicationDetail conserva deploy/down/delete; ServiceDialog no los renderiza.
  - id: F2
    statement: Apps consume Dominios y no duplica su lógica.
    proof: imports existentes, payload snapshots y ausencia de cliente/rutas alternativos.
  - id: F3
    statement: Un secreto revelado nunca entra en cache o storage y desaparece en 30 s.
    proof: fake timers, unmount/tab/application change y grep de storage/query cache.
  - id: F4
    statement: Servicios son dinámicos.
    proof: fixtures con nombres no estándar y E2E elige por inventario/puertos.
  - id: F5
    statement: Lifecycle de un servicio no reinicia los demás.
    proof: IDs/restart counts antes y después en E2E real.
  - id: F6
    statement: Ruta HTTPS sobrevive al reemplazo del contenedor en redeploy.
    proof: probe y request real antes/después con el mismo route id.
  - id: F7
    statement: Cleanup elimina exclusivamente recursos del E2E.
    proof: labels/ownership y snapshot de recursos preexistentes.
```

## VALIDATION

- Tests de render/RBAC de tarjetas y modal por servicio.
- Tests de apertura por mouse y teclado.
- Tests de body `{service}` y polling terminal.
- Tests de secreto: no cache, timeout, unmount y ausencia en toast/error.
- Tests de Dominios: imports y payloads exactos de la superficie existente.
- Build frontend.
- Regresión backend de A.SPEC 0060.
- E2E real ampliado, HTTPS sin bypass y cleanup verificado.
- `git diff --check`.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - aspecs/0061-apps-service-console-frontend-e2e.md
    - vendor/systutor-core/plugins/apps/frontend/**
    - scripts/e2e_apps_api.py
    - scripts/tests/test_e2e_apps_api.py
  import_only:
    - vendor/systutor-core/plugins/domains/frontend/**
  prohibited:
    - vendor/systutor-core/plugins/apps/backend/**
    - vendor/systutor-core/plugins/apps/migrations/**
    - vendor/systutor-core/plugins/apps/permissions/**
    - vendor/systutor-core/plugins/domains/backend/**
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/hosting/**
    - docker-compose.yml
    - .env*
```

## COMMITS

```text
docs(aspec): define Apps service console and real E2E (A.SPEC 0061)
refactor(apps ui): extract dynamic service cards
feat(apps ui): add service overview and scoped lifecycle
feat(apps ui): add managed variables and audited secret reveal
feat(apps ui): reuse Domains routes in service modal
feat(apps ui): add service logs and operations tabs
test(apps ui): cover service modal RBAC secrets and domain payloads
test(e2e): validate service domains lifecycle secrets and cleanup
```

## DEFINITION OF DONE

- [ ] Modal de aplicación conserva sólo responsabilidades globales.
- [ ] Cada servicio dinámico abre su modal por mouse y teclado.
- [ ] Overview, Variables, Dominios, Logs y Operaciones coinciden con ASCII.
- [ ] Lifecycle afecta sólo al servicio seleccionado.
- [ ] Secretos están enmascarados y reveal se limpia en máximo 30 segundos.
- [ ] Dominios se importa/reutiliza sin crear lógica paralela.
- [ ] Ruta HTTPS funciona antes y después de redeploy.
- [ ] E2E real cubre login, GitHub, deploy, dominio, lifecycle, reveal y cleanup.
