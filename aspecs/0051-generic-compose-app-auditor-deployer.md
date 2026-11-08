# A.SPEC 0051 — Auditor y desplegador primitivo de aplicaciones Compose

> `risk: high` — clona código privado, construye Dockerfiles no confiables y
> crea contenedores/volúmenes en el host Docker de laboratorio por SSH. Un
> error de confinamiento podría montar rutas del host, ampliar capacidades o
> reemplazar recursos ajenos. Requiere `approver: luc444s` y falla cerrado.

## WHY

Antes de integrar el módulo `apps` se necesita probar su primitiva mínima con
una aplicación real: descubrir su Compose y sus Dockerfiles, explicar riesgos
con archivo/línea/remediación, exigir procesos no-root y, sólo cuando la
auditoría quede limpia, desplegar automáticamente todos los servicios
declarados.

La aplicación de validación es el repositorio privado
`luc444s/software-de-gestion-para-editores`, rama exacta
`feature/workflow-diseno`. Esa rama ya contiene frontend, backend, Dockerfiles
y `docker-compose.dokploy.yml`. La definición final de laboratorio debe añadir
su servicio `database` y Dockerfile dentro del checkout de la aplicación. El
runner no conoce PostgreSQL, MySQL, MongoDB, Elasticsearch, SQL Server ni
ninguna tecnología concreta: sólo conoce servicios Compose.

## WHAT

Crear una CLI independiente:

```bash
python3 scripts/app_deploy.py inspect \
  --repo luc444s/software-de-gestion-para-editores \
  --branch feature/workflow-diseno --non-root

python3 scripts/app_deploy.py deploy \
  --repo luc444s/software-de-gestion-para-editores \
  --branch feature/workflow-diseno --non-root \
  --env-file /ruta/fuera/del/repo/app.env
```

`inspect` y `deploy --dry-run` son equivalentes respecto de efectos: pueden
usar `gh` y leer el checkout, pero no escriben en el host Docker, no construyen
imágenes y no crean/modifican/eliminan recursos. `deploy` ejecuta exactamente
la misma auditoría y sólo continúa si no existen hallazgos `error`.

## SCOPE

### 1. Descubrimiento

1. Clonar repositorios GitHub exclusivamente con `gh repo clone`, rama y
   `--single-branch`; también admitir `--source-dir` para un checkout ya
   preparado. Nunca imprimir tokens ni URLs autenticadas.
2. Descubrir nombres canónicos `compose.yaml`, `compose.yml`,
   `docker-compose.yaml`, `docker-compose.yml` y variantes
   `docker-compose.*.yaml|yml`, incluida `docker-compose.dokploy.yml`.
3. Si no hay candidato: error. Si hay más de uno: listar candidatos y exigir
   `--compose-file`; nunca elegir ambiguamente.
4. Resolver cada Dockerfile desde `services.*.build.context` y
   `services.*.build.dockerfile` (default `Dockerfile`). Contexto y Dockerfile
   deben quedar dentro del checkout real, sin escape por `..` o symlink.
5. Servicios `image:` son válidos en general. `--require-build` exige
   Dockerfile para cada servicio y se usa en el E2E de esta A.SPEC.

### 2. Informe de seguridad

Salida humana estable y opción `--format json`. Cada hallazgo contiene:

```json
{
  "severity": "error|warning|info",
  "code": "compose_privileged",
  "service": "frontend",
  "path": "docker-compose.yml",
  "line": 12,
  "message": "...",
  "remediation": "..."
}
```

La auditoría Compose rechaza siempre:

- `privileged: true`;
- cualquier `cap_add`, `devices` o montaje del socket Docker;
- `network_mode`, `pid`, `ipc`, `uts`, `userns` o `cgroup` del host;
- `security_opt` que desactive seccomp/AppArmor o `no-new-privileges`;
- bind mounts absolutos o que escapen del checkout/raíz de estado permitida;
- montajes de `/`, `/proc`, `/sys`, `/dev` o `/var/run`;
- `build.network: host`, entitlements privilegiados y `user: 0|root`.

La auditoría Dockerfile es por etapa final, conserva números de línea y marca:

- `error` con `--non-root`: `USER` final ausente, `root`, `0` o UID 0;
- `warning`: `ADD` remoto, descarga ejecutada por pipe a shell, `chmod 777`,
  setuid/sudoers y secretos aparentes en `ARG`/`ENV`;
- `info`: Dockerfile sin `HEALTHCHECK` (Compose puede aportar uno).

No se debe afirmar que una heurística demuestra seguridad total. El reporte
explica que una construcción Docker ejecuta código no confiable.

### 3. Modo `--non-root`

Usa el daemon Docker actual; no crea ni selecciona otro daemon.

1. Antes del build exige `USER` final no-root en cada Dockerfile.
2. Genera fuera del checkout un override con `cap_drop: [ALL]` y
   `security_opt: [no-new-privileges:true]`; no modifica fuentes en silencio.
3. Después del build inspecciona `.Config.User` de cada imagen; vacío/root/UID
   0 aborta antes de `up`.
4. Después de `up` inspecciona usuario configurado y procesos efectivos. Un
   proceso UID 0 aborta el despliegue; nunca se reintenta como root.
5. `privileged: true` se rechaza incluso sin `--non-root`.

### 4. Secretos y estado

- Nunca cargar `.env` del repositorio automáticamente. En particular, no leer
  ni usar `backend/.env` del repositorio de validación.
- `--env-file` debe estar fuera del checkout, ser regular, no symlink y tener
  permisos sin lectura de grupo/otros. Sus valores no aparecen en reporte,
  argv remoto, excepciones ni logs.
- En SSH, transferir checkout y env por stdin/tar a un release estable debajo
  de una raíz configurada; escribir env con modo `0600`.
- Guardar manifiesto sin secretos: repo, rama, commit, hash del árbol efectivo,
  Compose, servicios, imágenes, host, fecha y estado.
- Nombres y operaciones quedan confinados al proyecto Compose generado. No
  adoptar, bajar ni reemplazar recursos sin labels de propiedad del runner.

### 5. Ejecución local/SSH

- `--transport local|ssh`; SSH reutiliza las variables
  `SPANEL_DOCKER_SSH_*`/secreto ya configuradas, sin password en argv.
- Comandos remotos con argumentos/rutas validados y correctamente quoted.
- Build de todos los servicios, inspección de imágenes, `compose up -d`, espera
  bounded de health/running y reporte final.
- Si build o validación post-build falla, no ejecutar `up`.
- Si `up`/health falla, conservar logs diagnósticos redactados y marcar estado
  fallido; no tocar proyectos ajenos.
- `status`, `logs` y `down` sólo operan proyectos con manifiesto/labels propios.
  `down` conserva volúmenes; `down --purge-data` exige confirmación explícita.

## TARGET APP — PRUEBA REAL

En un checkout de `feature/workflow-diseno`, GENERATOR/main agent ejecutan el
ciclo real, sin introducir conocimiento de esa aplicación en el runner:

1. Primera auditoría: documentar los hallazgos de sus Dockerfiles/Compose.
2. Corregir en el checkout de la aplicación (no en heurísticas del runner):
   - frontend con runtime nginx no privilegiado y puerto no privilegiado;
   - backend con usuario propio no-root;
   - `database/Dockerfile` con usuario final no-root;
   - servicio Compose `database`, volumen persistente, health y dependencia;
   - frontend/backend/database definidos mediante `build`, no `image` directo.
3. No usar secretos versionados. Crear env de laboratorio fuera del checkout,
   con secretos aleatorios y `APP_ENV=production`; integraciones externas
   opcionales pueden quedar desactivadas.
4. Repetir `inspect --non-root --require-build`: cero errores.
5. `deploy --non-root --require-build` por SSH a la laptop lab.
6. Dejar en ejecución exactamente los servicios de aplicación `frontend`,
   `backend` y `database`, todos healthy/running, todos con proceso efectivo
   no-root y labels del proyecto.

Los cambios al repositorio externo no se commitean ni pushean sin una
autorización separada. El manifiesto debe registrar el commit base y hash del
checkout modificado para que el despliegue no se presente falsamente como el
commit limpio de GitHub.

## OUT OF SCOPE

- Plugin `apps`, API, UI, RBAC, PostgreSQL de SPanel o migraciones del kernel.
- Detectar o configurar motores de base de datos por nombre.
- Interpretar semántica de aplicaciones o generar `DATABASE_URL`.
- Docker Rootless, daemon adicional, Swarm o Kubernetes.
- Reparar automáticamente Dockerfiles/Compose.
- Publicar dominio/TLS o abrir puertos públicos en esta fase.
- Hacer commit/push al repositorio de la aplicación.

## CONTRACT

Precondiciones de deploy: auditoría estática sin errores, env externo seguro,
transporte disponible, proyecto/nombres no ocupados por recursos ajenos y, con
`--non-root`, todas las imágenes declaran usuario no-root.

Postcondición: todos los servicios declarados están creados por el proyecto y
running/healthy; con `--non-root`, ninguna imagen ni proceso efectivo usa UID 0.
Ante cualquier error previo a `up`, el host no cambia.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Dry-run nunca ejecuta Docker ni escribe en el host remoto.
    proof: tests con runners espía en cero llamadas mutantes y snapshot antes/después del lab.
  - id: I2
    statement: El runner no contiene nombres, imágenes, variables ni reglas de PostgreSQL/MySQL/Mongo/Elastic/SQL Server.
    proof: grep/test de arquitectura y fixture alterno con nombres neutrales.
  - id: I3
    statement: Un Compose con privilegios o escape de host jamás llega a build/up.
    proof: matriz de tests por cada clave prohibida, runner espía con cero llamadas.
  - id: I4
    statement: --non-root exige USER no-root estático, Config.User no-root y procesos efectivos no-root.
    proof: tests de parser, inspect post-build y top post-up; UID 0 aborta sin fallback.
  - id: I5
    statement: Los secretos no proceden del checkout ni aparecen en argv/reporte/logs/manifiesto.
    proof: canarios únicos y asserts negativos en todas las salidas/llamadas persistidas.
  - id: I6
    statement: Sólo se operan recursos creados y etiquetados por el proyecto del runner.
    proof: tests con proyecto homónimo ajeno rechazan deploy/down sin mutación.
  - id: I7
    statement: Descubrimiento ambiguo nunca elige silenciosamente un Compose.
    proof: tests de cero, uno y múltiples candidatos y resolución explícita.
  - id: I8
    statement: La prueba real termina con frontend, backend y database en producción de laboratorio, los tres no-root.
    proof: docker compose ps/inspect/top por SSH, health y manifiesto del commit/hash efectivo.
```

## VERIFICATION

GENERATOR ejecuta, en este orden:

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin

.venv/bin/python -m pytest scripts/tests/test_app_deploy.py -q

.venv/bin/python -m ruff check \
  scripts/app_deploy.py scripts/tests/test_app_deploy.py \
  scripts/tests/test_app_deploy_live.py

SPANEL_LIVE_TESTS=0 .venv/bin/python -m pytest \
  scripts/tests/test_app_deploy_live.py -q

git diff --check
```

El live test con `SPANEL_LIVE_TESTS=0` debe hacer skip explícito. Después de
GENERATOR, el agente principal ejecuta el ciclo GitHub/SSH real en la laptop y
adjunta evidencia de los tres servicios. Dado que el DoD exige que sigan en
producción de laboratorio, ese E2E no hace cleanup automático.

## ROLLBACK

`app_deploy.py down --project <id>` conserva datos; `--purge-data` es separado
y destructivo. Revertir esta A.SPEC elimina el runner, no los recursos ya
desplegados: deben bajarse primero por su manifiesto. Nunca ejecutar cleanup por
patrón global.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0051-generic-compose-app-auditor-deployer.md
    - scripts/app_deploy.py
    - scripts/tests/test_app_deploy.py
    - scripts/tests/test_app_deploy_live.py
  prohibited:
    - vendor/systutor-core/src/**
    - vendor/systutor-core/plugins/**
    - apps/**
    - docker-compose.yml
    - Dockerfile
    - .env*
    - README.md
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - checkout temporal de repositorios GitHub privados
    - build y lifecycle de un proyecto Compose etiquetado
    - filesystem remoto bajo raíz propia del runner
  indirect:
    - daemon Docker existente de la laptop lab
    - CPU, red y almacenamiento consumidos por builds/servicios
  must_not_affect:
    - contenedores WordPress/static existentes
    - redes/volúmenes ajenos
    - aplicación SPanel y su base de datos
    - secretos versionados o configuración del host
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: el runner orquesta servicios declarativos y no conoce tecnologías de aplicación
  implementation_shape: un único script monolítico scripts/app_deploy.py
  line_limit: none
  package_split_prohibited: true
```

## Traceability

- Requirement: script primitivo que detecta Compose/Dockerfiles, detalla
  peligros, soporta dry-run, exige procesos no-root y despliega automáticamente
  cuando el informe queda limpio.
- Validation target: `luc444s/software-de-gestion-para-editores`,
  `feature/workflow-diseno`.
- owner: luc444s
- approver: luc444s
- Deployment target: laptop lab por SSH configurado en SPanel.

## Definition of Done

- [x] `inspect` y `deploy --dry-run` producen informe detallado sin mutación.
- [x] `--non-root` rechaza USER/imagen/proceso root y nunca hace fallback.
- [x] Auditoría bloquea privilegios, host escapes y socket Docker.
- [x] Runner no contiene lógica específica de motores de datos.
- [x] Suites offline y skip-gate live verdes.
- [x] Laptop lab conserva `frontend`, `backend` y `database` running/healthy,
      construidos por sus Dockerfiles y con procesos no-root.
- [x] Ningún contenedor/volumen/red ajeno fue modificado.

## Validation Evidence

- Primera auditoría sobre `feature/workflow-diseno`: bloqueó backend y frontend
  por `dockerfile_final_user_root`; no ejecutó build/up.
- Auditoría final y dry-run: `errors=0`, `findings=0`.
- Gates: 49 tests offline verdes, Ruff verde, live gate sin opt-in = 1 skip y
  `git diff --check` verde.
- E2E SSH laptop, proyecto `editors-lab`, commit base
  `f3d53946429dcbb1dda8aa2ebff140aeff4b5622`, árbol efectivo
  `9bb777bac3fd6d6516d8d3e21220c5b16fdae1854994c8529cbe9c4630086cd6`:
  - backend: running/healthy, `Config.User=app`, UID efectivo 10001;
  - database: running/healthy, `Config.User=postgres`, UID efectivo 70;
  - frontend: running/healthy, `Config.User=101`, UID efectivo 101;
  - los tres tienen owner label `editors-lab` y siguen ejecutándose;
  - backend confirma `CLOUDFLARE_R2_ENABLED=true`.
