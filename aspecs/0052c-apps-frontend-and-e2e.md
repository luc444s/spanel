# A.SPEC 0052C — Interfaz Apps async-first y E2E integrado

> `risk: high` — expone en UI OAuth, repos privados y operaciones Docker ya
> implementadas por 0052B. No añade nuevas capacidades backend: debe respetar
> estrictamente RBAC, polling, secretos y confirmaciones del contrato existente.

## WHY

0052A entrega el motor único y 0052B la API async completa. Falta una interfaz
usable que nunca espere un build dentro de la mutación HTTP, no cachee secretos
y permita entender findings/fases antes de desplegar.

## WHAT

Registrar la ruta `/app/apps` y construir la experiencia completa:

- conectar/desconectar GitHub;
- crear e inspeccionar una aplicación;
- revisar/remediar findings;
- configurar variables, ingress y dominio;
- desplegar y seguir fases por polling;
- consultar deployments/operaciones/logs;
- ejecutar lifecycle según RBAC;
- validar el flujo real GitHub → worker → SSH → proxy.

## SCOPE

### 1. Registro y navegación

`frontend/register.ts` declara plugin `apps`, ruta `apps`, label **Apps** y
permiso `apps.applications.read`. Usar SDK/shell UI y React Query como los demás
plugins; no importar lógica ni componentes WordPress.

### 2. Conectar GitHub

Cabecera de Apps muestra:

- desconectado: botón **Conectar GitHub** con `apps.github.connect`;
- conectado: avatar/login/scopes/estado, nunca token;
- **Desconectar** con confirmación.

Connect llama start, valida que authorization URL tenga host exacto
`github.com` y navega en la misma pestaña. Callback backend vuelve con sólo
`?github=connected|error`; la UI limpia el query mediante replace, refresca
metadata y muestra toast. Nunca recibe/lee code, state, verifier o token.

Si conexión está expired/revoked, muestra **Reconectar GitHub**. Una aplicación
cuya fuente pertenece a otra conexión muestra identidad no secreta y acción
`source/reconnect` sólo con permiso create.

### 3. Wizard de creación e inspección

Pasos:

1. nombre, `owner/repo`, rama y Compose opcional;
2. submit con nueva Idempotency-Key;
3. 202 cierra formulario y crea tracker de operation;
4. polling cada 2 s muestra `queued`, `cloning`, `auditing`;
5. terminal detiene polling y hace un refetch.

Ambigüedad Compose lista candidatos server-side y permite nueva inspección con
selección explícita. No hay campos token, URL git arbitraria, container, network,
project name, path o Docker args.

### 4. Informe de seguridad

Mostrar warning permanente: auditoría estática no demuestra seguridad y Docker
build ejecuta código no confiable.

Findings agrupados error/warning/info muestran code, service, path:line, message
y remediation. Cualquier error deshabilita deploy. La UI no ofrece bypass,
“deploy anyway” ni toggle non-root.

### 5. Deploy async

Formulario platform-only:

- variables nombre/valor;
- ingress service entre servicios inspeccionados;
- puerto 1..65535;
- dominio;
- confirmación exacta.

Variables permanecen sólo en estado local del diálogo. No React Query cache,
localStorage, sessionStorage, URL, analytics ni toast. Al obtener 202 se limpian
inmediatamente.

El diálogo cierra al aceptar; un tracker/toast sigue fases `queued`, `cloning`,
`auditing`, `staging`, `building`, `verifying_images`, `starting`,
`verifying_runtime`, `publishing`. Error muestra sólo mensaje público backend.

### 6. Detalle y lifecycle

Detalle por tabs/secciones:

- Overview: estado persistido, dominio, ingress, commit y servicios;
- Auditoría: último informe;
- Deployments: historial inmutable y activo;
- Operations: kind/status/phase/timestamps/error público;
- Logs: resultado redactado del último job solicitado.

Refresh, logs, start, stop, restart y down crean jobs con key nueva y polling.
No stream Docker ni requests largos. Acciones incompatibles con estado o job
activo quedan disabled. Delete/purge platform-only usa confirmación reforzada y
no infiere éxito antes del estado terminal.

Gates:

- connect: `apps.github.connect`;
- create/inspect/reconnect: `apps.applications.create`;
- read: `apps.applications.read`;
- lifecycle/logs: `apps.applications.manage`;
- deploy/delete: permiso Apps correspondiente **y** `core.platform.admin`.

Backend sigue siendo autoridad; ocultar botones no reemplaza 403/404.

### 7. Polling e idempotencia

- Hook único reusable para operations.
- Intervalo 2 s con cancelación al desmontar.
- Stop inmediato en succeeded/failed/interrupted.
- Un refetch final de application/list.
- No retry automático que cree otra operation.
- Reintento de request incierto reutiliza key/body; nueva intención genera key.
- Varias cards pueden observar jobs sin duplicar mutaciones.

### 8. E2E final

Con `luc444s/software-de-gestion-para-editores`, rama
`feature/workflow-diseno`:

1. usuario conecta GitHub por OAuth real sin exponer token;
2. create devuelve 202 y web no clona;
3. inspect avanza fases y presenta cero errors en checkout validado;
4. deploy platform-approved deja frontend/backend/database healthy y non-root;
5. dominio llega al frontend por puerto no privilegiado;
6. refresh/logs/stop/start/restart son jobs visibles;
7. segundo release controlado activa nuevo deployment;
8. release fallido restaura anterior;
9. desconectar GitHub bloquea nuevo inspect/deploy pero lifecycle continúa;
10. la aplicación queda running al terminar salvo autorización de cleanup.

No registrar token/capturas con secretos en evidencia.

## OUT OF SCOPE

- Cambiar API, modelos, worker, runtime o contratos definidos por 0052A/B salvo
  corrección demostrable requerida por integración.
- Repo browser, selector de organizaciones o instalación GitHub App.
- Editor de Compose/Dockerfile, reparación automática o terminal.
- Logs streaming, métricas, escalado, backups, rollback manual.
- Diseño mobile avanzado fuera de layout responsive básico existente.

## INVARIANTS

```yaml
invariants:
  - id: C1
    statement: UI nunca recibe ni persiste tokens/codes/state/verifiers OAuth.
    proof: types/API sólo exponen metadata y callback query connected|error; grep storage/cache.
  - id: C2
    statement: Toda operación runtime se observa por 202 + polling y nunca por request largo.
    proof: API mocks, hook terminal y ausencia de waits/streams Docker.
  - id: C3
    statement: Valores env se destruyen del estado al aceptar job y no entran en caches/storage/URL.
    proof: tests de mutation/cache y canarios negativos en UI state serializable.
  - id: C4
    statement: Error de auditoría no tiene bypass visual ni request deploy habilitado.
    proof: fixtures findings error y asserts disabled/no mutation.
  - id: C5
    statement: Deploy/delete requieren doble gate de permiso Apps + plataforma.
    proof: matriz de permissions renderiza/no renderiza; backend tests 0052B siguen verdes.
  - id: C6
    statement: Polling termina una vez, cancela al desmontar y hace un único refetch terminal.
    proof: fake timers para succeeded/failed/interrupted/unmount.
  - id: C7
    statement: UI nunca permite elegir container, network, project, host, path ni Docker argv.
    proof: schemas/form types y payload snapshots.
  - id: C8
    statement: E2E demuestra dominio sano y tres servicios efectivos non-root sin afectar Hosting.
    proof: API/UI + docker inspect/top + HTTPS y snapshot de recursos ajenos.
```

## VERIFICATION

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

# regresión backend antes del frontend
.venv/bin/python -m pytest \
  scripts/tests/test_app_deploy.py \
  vendor/systutor-core/plugins/apps/backend/tests -q

cd apps/web
npm run build

cd /data/data/com.termux/files/home/Proyectos/mailadmin
SPANEL_LIVE_TESTS=0 .venv/bin/python -m pytest \
  scripts/tests/test_app_deploy_live.py -q

docker compose config >/dev/null
git diff --check
```

Si el proyecto dispone de runner de tests frontend, añadir tests del polling,
OAuth metadata, gates y secret clearing al gate; el build es mínimo obligatorio.
El live deshabilitado reporta skip. El E2E real se ejecuta después de commit con
credenciales/configuración de laboratorio y evidencia redactada.

## ROLLBACK

Revertir 0052C elimina sólo interfaz/E2E integrado; API/worker 0052B y CLI 0052A
siguen disponibles. No ejecutar down/delete durante rollback de frontend.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0052-async-first-apps-module.md
    - aspecs/0052c-apps-frontend-and-e2e.md
    - vendor/systutor-core/plugins/apps/frontend/**
    - scripts/tests/test_app_deploy_live.py
  prohibited:
    - vendor/systutor-core/plugins/apps/backend/**
    - vendor/systutor-core/plugins/apps/migrations/**
    - vendor/systutor-core/plugins/apps/permissions/**
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/hosting/**
    - vendor/systutor-core/plugins/domains/**
    - apps/web/src/features/auth/**
    - Dockerfile
    - docker-compose.yml
    - .env*
```

## Commit obligatorio

```text
feat(apps ui): add async github deployment flow (A.SPEC 0052C)
```

Debe partir de 0052A+B verdes. Incluye sólo frontend y E2E de integración; no
mezcla refactors backend.

## Definition of Done

- [ ] Connect/disconnect/reconnect GitHub visibles sin secretos.
- [ ] Wizard create/inspect y findings completos.
- [ ] Deploy async por fases, sin bypass non-root ni cache de env.
- [ ] Detalle, history, operations, logs y lifecycle por polling.
- [ ] Gates RBAC correctos y backend sigue siendo autoridad.
- [ ] Frontend build y regresión backend verdes.
- [ ] E2E real prueba OAuth, tres servicios non-root, dominio y rollback.
