# A.SPEC 0034 — Resolve a database host alias to its container

> `risk: normal` — cambia qué contenedor usa el panel para hablar con la base de
> un sitio adoptado. No borra, crea ni publica nada; una resolución incorrecta
> apuntaría al contenedor equivocado. Exige `approver:` humano.
>
> Corrige el supuesto de A.SPEC 0033: el host de WordPress puede ser un alias de
> red (`db`) y no el nombre del contenedor (`legalattorney-clean-db`).

## WHY

`database_binding` toma `WORDPRESS_DB_HOST` del entorno del sitio y lo valida
como nombre de contenedor Docker. En sitios creados por el panel ese valor es
`spanel-<slug>-db`, que coincide con el contenedor. En sitios adoptados de un
`docker compose` (por ejemplo `legalattorney-clean`) el valor es `db`: un alias
de la red de Compose.

Con `host="db"`, `docker inspect db` no encuentra nada:

- "Base de datos" responde `409 hosting_database_unavailable`;
- phpMyAdmin no abre;
- la lista de usuarios de WordPress y el reset de contraseña tampoco pueden
  ejecutar SQL;
- el auto-attach de A.SPEC 0033 nunca corre.

## WHAT

Cuando el host validado **no** es un contenedor, el panel lo resuelve al nombre
real del contenedor usando las redes del propio sitio:

1. Toma las redes de `NetworkSettings.Networks` del contenedor WordPress.
2. En cada una, lista los contenedores conectados.
3. Busca el contenedor cuyos `Aliases` de esa red incluyen el host declarado.
4. Devuelve ese nombre de contenedor; si no hay match o hay más de uno, deja el
   comportamiento anterior (error controlado), nunca adivina.

Si `docker inspect <host>` ya funciona, no se ejecuta ningún escaneo: los sitios
creados por el panel siguen igual.

El host crudo (`db`) se conserva para WordPress; solo cambia el nombre que usa el
panel para inspeccionar, ejecutar SQL y elegir el servidor de phpMyAdmin.

## SCOPE

- Resolver puro + I/O en `hosting/backend/database.py`.
- `HostingService._database_binding_for`: resolver después de validar.
- Tests unitarios y de servicio.
- README del plugin.

## OUT OF SCOPE

- Cambiar `WORDPRESS_DB_HOST` de ningún sitio.
- Recrear contenedores, tocar redes o volúmenes.
- Cambiar `database_binding` ni su validación.
- phpMyAdmin, su login o su red (A.SPEC 0033).
- Migraciones, rutas HTTP o schemas.
- Otros plugins o el kernel.

## CONTRACT

Precondiciones:

- Sitio WordPress con `NetworkSettings.Networks` legible.
- El host declarado resuelve a un contenedor por alias en una de esas redes.

Postcondiciones:

1. `_database_binding_for` devuelve el nombre real del contenedor de base.
2. `docker inspect <resuelto>`, `docker exec <resuelto>`, el auto-attach a
   `spanel-db` y el `pma_servername` usan ese nombre.
3. El host crudo usado por WordPress no cambia.
4. Si no se puede resolver, el error es el mismo que hoy
   (`hosting_database_unavailable`), sin elegir un contenedor al azar.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Si el host ya es un contenedor, no se ejecuta ningún escaneo de alias y el resultado es idéntico.
    proof: test de servicio con inspect exitoso; cero llamadas a network inspect.
  - id: I2
    statement: Solo se consideran las redes del contenedor del sitio.
    proof: test con un alias igual en una red ajena; no se elige.
  - id: I3
    statement: El host crudo de WordPress no se modifica.
    proof: test de _database_binding_for; primer valor sigue siendo "db".
  - id: I4
    statement: Un alias inexistente o ambiguo no elige contenedor; devuelve el host original para que falle como hoy.
    proof: tests de cero matches y de dos matches.
  - id: I5
    statement: La resolución no imprime ni registra credenciales.
    proof: los comandos solo llevan nombres de red/contenedor; test de auditoría sin secretos.
  - id: I6
    statement: Los sitios spanel-*-db siguen funcionando sin cambios.
    proof: tests existentes de database/users/reset verdes.
  - id: I7
    statement: Con el host resuelto, phpMyAdmin recibe el contenedor real y lo conecta a spanel-db.
    proof: test de servicio con alias "db" y client espía; server == contenedor real.
  - id: I8
    statement: Los sitios estáticos y los guards WordPress-only siguen igual.
    proof: tests existentes verdes.
```

## VERIFICATION

### V0 — Gates

```bash
cd /data/data/com.termux/files/home/Proyectos/mailadmin
PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src \
  .venv/bin/python -m pytest \
  vendor/systutor-core/plugins/hosting/backend/tests \
  vendor/systutor-core/plugins/domains/backend/tests \
  vendor/systutor-core/plugins/tenant/backend/tests -q
cd vendor/systutor-core && ruff check \
  plugins/hosting/backend/database.py \
  plugins/hosting/backend/service.py \
  plugins/hosting/backend/tests/test_database_alias.py
```

### V1 — Resolver puro

- `alias_contenedor("db", candidatos)` devuelve el nombre cuando un solo
  contenedor declara ese alias en una red del sitio.
- Devuelve `None` si no hay match.
- Devuelve `None` si hay dos contenedores con el mismo alias en la misma red.
- Ignora aliases de una red que no pertenece al sitio.

### V2 — Servicio

- Host ya existente (`spanel-demo-db`) → sin llamadas a `docker network inspect`.
- Host alias (`db`) → `_database_binding_for` devuelve
  `legalattorney-clean-db` y conserva `db` como host crudo.
- Con alias resuelto, `phpmyadmin_login_credentials` pasa el contenedor real a
  `client.login()` y a la conexión de red.
- Alias no resoluble → `AppError` 409, sin ejecutar SQL ni login.

### V3 — Live (operador)

```bash
docker exec -i -e PYTHONPATH=/app:/app/vendor/systutor-core spanel-app-1 python - <<'PY'
from plugins.hosting.backend.inspection import inspect_container
from plugins.hosting.backend.settings import docker_env
from plugins.hosting.backend.database_binding import database_credentials
info = inspect_container(docker_env(), "legalattorney-clean")
print(database_credentials(info))
PY
```

Esperado: `('legalattorney-clean-db', 'legalattorney_clean', 'wp_clean', '...')`,
no `('db', ...)`.

## ROLLBACK

- Revertir el commit devuelve el comportamiento anterior: el host validado se
  usa tal cual. No hay migración ni recurso Docker que deshacer.
- Los sitios con `spanel-*-db` no se ven afectados en ningún caso.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0034-database-host-alias.md
    - vendor/systutor-core/plugins/hosting/backend/database.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_database_alias.py   # nuevo
    - vendor/systutor-core/plugins/hosting/README.md
  prohibited:
    - vendor/systutor-core/plugins/hosting/backend/database_binding.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - vendor/systutor-core/plugins/hosting/backend/migrations/**
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/network.py
    - vendor/systutor-core/plugins/hosting/frontend/**
    - vendor/systutor-core/plugins/domains/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - scripts/**
    - docker-compose.yml
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - seleccion del contenedor de base de un sitio adoptado
    - lista de usuarios y reset de WordPress
    - apertura de phpMyAdmin
  indirect:
    - auditoria database_access y phpmyadmin_access
  must_not_affect:
    - host crudo de WordPress
    - sitios spanel-*-db
    - phpMyAdmin, su login y su red
    - credenciales y datos
    - rutas, schemas y migraciones
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0017
    - 0019
    - 0033
  must_compose_with:
    - plugin hosting
    - phpMyAdmin hub
  systemic_invariants:
    - ningun secreto en argv, logs ni auditoria
    - un solo contenedor por base
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest vendor/systutor-core/plugins/hosting/backend/tests vendor/systutor-core/plugins/domains/backend/tests vendor/systutor-core/plugins/tenant/backend/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: la resolucion de alias vive en database.py; el servicio solo la aplica despues de validar
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/database.py
```

## Traceability

- Requirement: que un sitio adoptado con `WORDPRESS_DB_HOST=db` pueda abrir su
  base, listar usuarios y entrar a phpMyAdmin.
- owner: luc444s
- approver: luc444s
- Commit:
- Deployment: git pull + `docker compose up -d --build app`; sin migraciones ni
  cambios de red.

## Definition of Done

- [ ] Objective satisfied
- [ ] Scope respected
- [ ] Contract satisfied
- [ ] Independent falsable truth exists now
- [ ] Invariants preserved
- [ ] Verification passed
- [ ] Rollback / compensation is honest
- [ ] Composition checks passed when applicable
- [ ] No unrelated changes
- [ ] Structural constraints respected
- [ ] Traceability established
