# A.SPEC 0033 — Serve every site database from one phpMyAdmin

> `risk: high` — toca redes Docker, credenciales de base de datos y el proxy de
> phpMyAdmin. Una red mal conectada expone bases; un login mal armado manda una
> contraseña al servidor equivocado. Exige `approver:` humano.
>
> Continúa A.SPEC 0017/0019 (credencial propia y proxy de phpMyAdmin) y A.SPEC
> 0031 (sitios WordPress con su propio MariaDB).

## WHY

Hoy conviven dos generaciones de base de datos:

- El MariaDB heredado (`mariadb`) en la red `mariadb_internal`, que es el único
  servidor que conoce `spanel-phpmyadmin` porque corre con `PMA_HOST=mariadb` y
  `PMA_ARBITRARY=0`.
- Un MariaDB por sitio (`spanel-<slug>-db`, o el de un sitio adoptado como
  `legalattorney-clean-db`) en la red de su propio proyecto.

El panel ya resuelve el host, la base, el usuario y la contraseña del sitio
(`database_binding`), y ya sabe iniciar sesión en phpMyAdmin del lado servidor.
Lo que falta es que phpMyAdmin **pueda llegar** a esa base y **sepa elegirla**:
con un único `PMA_HOST` fijo, abrir "Base de datos" en `legalattorney-clean`
intenta autenticar contra `mariadb` y falla.

La verdad nueva: **un solo phpMyAdmin atiende todas las bases, viejas y nuevas,
eligiendo el servidor por sitio y sin exponer ninguna base a Internet.**

## WHAT

`spanel-phpmyadmin` pasa a ser un hub multi-servidor:

1. Corre con `PMA_ARBITRARY=1` y un `ArbitraryServerRegexp` que solo admite
   nombres de contenedores de base (`mariadb`, `spanel-*-db`, `*-db`).
2. Está conectado a una red interna nueva `spanel-db`, además de su red actual
   (la que comparte con la app).
3. El MariaDB heredado y todos los MariaDB por sitio quedan conectados a
   `spanel-db`; no se mueven de sus redes originales.
4. El panel, al iniciar la sesión de phpMyAdmin de un sitio, envía
   `pma_servername=<contenedor-db>` en el POST de login.
5. Antes de ese login, el panel asegura —de forma idempotente— que el contenedor
   de base del sitio está conectado a `spanel-db`; si ya lo está, no ejecuta
   Docker.
6. El provisioning de WordPress conecta automáticamente el MariaDB nuevo a
   `spanel-db`.
7. Si `SPANEL_PHPMYADMIN_ARBITRARY` es `false` o
   `SPANEL_PHPMYADMIN_DB_NETWORK` está vacío, el comportamiento es exactamente
   el actual: `PMA_HOST` fijo, sin `pma_servername`, sin conexiones nuevas.

## SCOPE

- `HostingSettings`: `phpmyadmin_arbitrary` y `phpmyadmin_db_network`.
- `PmaClient.login()`: campo opcional `pma_servername`.
- `HostingService.phpmyadmin_login_credentials()`: pasar el servidor y asegurar
  la red del contenedor de base.
- Módulo puro `network.py`: lectura de redes desde `docker inspect` y decisión
  de conexión.
- `Provisioner`: conectar el MariaDB del sitio nuevo a la red configurada.
- `scripts/phpmyadmin_up.sh`: red `spanel-db`, `PMA_ARBITRARY=1`, config con
  regexp, conexión del MariaDB heredado y de PMA.
- `scripts/phpmyadmin_arbitrary.php`: config de phpMyAdmin.
- Tests nuevos y ampliación de los existentes.
- Documentación y `.env.docker.example`.

## OUT OF SCOPE

- Cambiar credenciales, usuarios o bases existentes.
- Migrar datos entre MariaDB.
- Crear un phpMyAdmin por sitio (Opción B, descartada).
- Publicar phpMyAdmin en `0.0.0.0` o abrir un puerto público.
- DNS, Traefik, certificados o Cloudflare.
- La UI de Hosting (no hay cambios de frontend).
- Cambiar el contrato de `GET/POST /hosting/sites`, sus schemas o sus rutas.
- Añadir un panel SQL propio o reemplazar phpMyAdmin.
- Tocar el plugin `mail`, `domains`, `tenant`, `filemanager` o el kernel.

## CONTRACT

### Precondiciones

- Plugin `hosting` habilitado, con sitios WordPress que declaran su base por
  entorno o por `wp-config.php`.
- El contenedor de phpMyAdmin existe o se recrea con el script del repo.
- El operador puede crear la red `spanel-db` (el script la crea `--internal`).

### Configuración nueva

| Variable | Default | Efecto |
|---|---|---|
| `SPANEL_PHPMYADMIN_ARBITRARY` | `false` | Si es `true`, el panel envía `pma_servername` |
| `SPANEL_PHPMYADMIN_DB_NETWORK` | vacío | Red donde se conectan los MariaDB; vacío = desactivado |

Ambas deben estar activas para el modo multi-base. Con una sola, no cambia
nada.

### Postcondiciones observables

1. `GET` a la base de un sitio con MariaDB propio sigue devolviendo host, base,
   usuario y contraseña (sin cambios en el schema).
2. Abrir "phpMyAdmin" en un sitio con MariaDB propio inicia sesión contra su
   contenedor y muestra su base, no la de `mariadb`.
3. Abrir "phpMyAdmin" en un sitio heredado sigue funcionando contra `mariadb`.
4. El contenedor de base del sitio queda con una interfaz más en `spanel-db`;
   sus redes originales no cambian.
5. Un sitio estático, o uno sin base, no dispara conexiones ni logins.
6. La auditoría registra `database`, `host` y —si hubo conexión— `network`; nunca
   la contraseña.

### Seguridad

- `spanel-db` se crea con `--internal`.
- El regexp de phpMyAdmin solo admite nombres de contenedor de base.
- El panel valida el host con la misma regla antes de enviarlo
  (`database_binding._validate_host`).
- phpMyAdmin sigue escuchando solo en `127.0.0.1` (script) y se accede por el
  proxy del panel.
- La contraseña nunca viaja en argv, logs, URLs ni auditoría.

## INVARIANTS

```yaml
invariants:
  - id: I1
    statement: Con arbitrary=false o red vacía, el login no envía pma_servername y el comportamiento es el actual.
    proof: test de PmaClient sin server; test de settings con defaults; tests existentes de login verdes.
  - id: I2
    statement: Con arbitrary=true, el POST de login incluye exactamente un pma_servername igual al contenedor de base validado.
    proof: servidor HTTP de test que captura el body; assert del campo y de que no hay otros valores.
  - id: I3
    statement: Un host con esquema, IP o localhost nunca llega a phpMyAdmin.
    proof: tests de validacion pura rechazando esos valores.
  - id: I4
    statement: La conexion de red solo se ejecuta si el contenedor no esta ya en la red configurada, y solo sobre ese contenedor.
    proof: test de networks_of/needs_connection; test de servicio con docker espia que no llama connect dos veces.
  - id: I5
    statement: El sitio heredado sobre mariadb sigue autenticando y no cambia de red.
    proof: regexp admite mariadb; script conecta mariadb pero no la quita de mariadb_internal; tests de login legacy.
  - id: I6
    statement: phpMyAdmin no queda publicado fuera de loopback ni accesible sin sesion del panel.
    proof: revision del argv del script (127.0.0.1) y tests existentes de sesion/proxy.
  - id: I7
    statement: Ningun test, log, argv ni fila de auditoria contiene la contrasena de la base.
    proof: tests de errores de login y de auditoria; grep de secretos en el diff.
  - id: I8
    statement: Los sitios estaticos y las rutas WordPress-only siguen rechazando phpMyAdmin como hoy.
    proof: tests de guard 409 existentes verdes.
  - id: I9
    statement: Si conectar la red del MariaDB falla al crear un sitio, el provisioning falla y limpia contenedores/volumenes como hoy.
    proof: test con provisioner que falla en network connect; cleanup llamado y sin fila.
  - id: I10
    statement: El proxy, el rewrite y el streaming de phpMyAdmin no cambian.
    proof: tests test_phpmyadmin_proxy.py, test_phpmyadmin_rewrite.py y test_phpmyadmin_concurrency.py verdes.
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
  plugins/hosting/backend/network.py \
  plugins/hosting/backend/settings.py \
  plugins/hosting/backend/phpmyadmin_proxy.py \
  plugins/hosting/backend/service.py \
  plugins/hosting/backend/provisioner.py \
  plugins/hosting/backend/tests/test_phpmyadmin_proxy.py \
  plugins/hosting/backend/tests/test_network_attach.py \
  plugins/hosting/backend/tests/test_phpmyadmin_arbitrary.py
```

### V1 — Login con servidor

- `PmaClient.login("u", "p", "spanel-x-db")` envía `pma_servername=spanel-x-db`
  además de usuario y contraseña.
- `PmaClient.login("u", "p")` no incluye la clave `pma_servername`.
- El login sigue rechazando credenciales inválidas sin filtrar la contraseña.

### V2 — Settings

- `register_hosting_settings()` con `SPANEL_PHPMYADMIN_ARBITRARY=true` y
  `SPANEL_PHPMYADMIN_DB_NETWORK=spanel-db` devuelve ambos valores.
- Sin variables, devuelve `False` y `""`.
- Valores `0/no/off` se leen como `false`.

### V3 — Red

- `networks_of(info)` lee `NetworkSettings.Networks`.
- `needs_connection(info, "spanel-db")` es `True` cuando falta y `False` cuando
  ya está.
- `validate_network_name` rechaza nombres con espacios o esquema.
- Un sitio estático no intenta conectar ninguna base.

### V4 — Servicio y provisioning

- Con `arbitrary=true`, el servicio resuelve el host del sitio y lo pasa a
  `client.login()`.
- Con `arbitrary=false`, pasa `None`.
- Con la base ya en `spanel-db`, no ejecuta `docker network connect`.
- El `Provisioner` agrega `network connect <red> <db>` después de crear el
  MariaDB cuando la red está configurada.
- La auditoría de `phpmyadmin_access` incluye `host` y `database`; si conectó,
  `network`.

### V5 — Script de despliegue

```bash
bash -n scripts/phpmyadmin_up.sh
```

- El script crea `spanel-db` como `--internal` si falta.
- Mantiene `-p 127.0.0.1:<PORT>:8080`.
- Pasa `PMA_ARBITRARY=1` y monta `phpmyadmin_arbitrary.php`.
- Conecta el contenedor de `DB_HOST` y el de PMA a `spanel-db`.

### V6 — Regresión

- Tests de phpMyAdmin (proxy, rewrite, auth, concurrency) verdes.
- Tests de creación de sitios y de guard de tipo verdes.
- No hay cambios de schema ni de rutas HTTP.

### V7 — Smoke live (operador, producción)

```bash
docker network create --internal spanel-db 2>/dev/null || true
./scripts/phpmyadmin_up.sh --down --yes
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
# abrir Base de datos -> phpMyAdmin en legalattorney-clean
# esperado: base legalattorney_clean (nombre real), no las bases de mariadb
```

## ROLLBACK

- Desactivar `SPANEL_PHPMYADMIN_ARBITRARY=false` y
  `SPANEL_PHPMYADMIN_DB_NETWORK=` devuelve el login al modo fijo sin tocar
  datos.
- Recrear phpMyAdmin con el script anterior o con `PMA_ARBITRARY=0`.
- Las interfaces en `spanel-db` se quitan con
  `docker network disconnect spanel-db <contenedor>`; no se borra ninguna base,
  volumen ni fila de `hosting_sites`.
- Revertir el commit no desconecta redes ya creadas: se compensa con el comando
  anterior. No hay migraciones ni replay.

## Change Surface

```yaml
change_surface:
  allowed:
    - aspecs/0033-phpmyadmin-multi-database.md
    - scripts/phpmyadmin_up.sh
    - scripts/phpmyadmin_arbitrary.php                                  # nuevo
    - vendor/systutor-core/plugins/hosting/backend/settings.py
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/service.py
    - vendor/systutor-core/plugins/hosting/backend/provisioner.py
    - vendor/systutor-core/plugins/hosting/backend/network.py           # nuevo
    - vendor/systutor-core/plugins/hosting/backend/tests/test_phpmyadmin_proxy.py
    - vendor/systutor-core/plugins/hosting/backend/tests/test_network_attach.py      # nuevo
    - vendor/systutor-core/plugins/hosting/backend/tests/test_phpmyadmin_arbitrary.py # nuevo
    - vendor/systutor-core/plugins/hosting/README.md
    - .env.docker.example
    - README.md
  prohibited:
    - vendor/systutor-core/src/systutor/kernel/**
    - vendor/systutor-core/src/systutor/api/**
    - vendor/systutor-core/plugins/mail/**
    - vendor/systutor-core/plugins/tenant/**
    - vendor/systutor-core/plugins/domains/**
    - vendor/systutor-core/plugins/filemanager/**
    - vendor/systutor-core/plugins/hosting/frontend/**
    - vendor/systutor-core/plugins/hosting/backend/migrations/**
    - vendor/systutor-core/plugins/hosting/backend/database.py
    - vendor/systutor-core/plugins/hosting/backend/database_binding.py
    - vendor/systutor-core/plugins/hosting/backend/wpconfig.py
    - vendor/systutor-core/plugins/hosting/backend/router.py
    - vendor/systutor-core/plugins/hosting/backend/schemas.py
    - docker-compose.yml
    - scripts/traefik-lab.sh
    - scripts/traefik-lab/**
    - .env.docker
```

## Blast Radius

```yaml
blast_radius:
  direct:
    - conexion de phpMyAdmin con las bases de los sitios
    - red Docker de los contenedores MariaDB
    - provisioning de WordPress nuevos
  indirect:
    - boton "Base de datos" y sesion de phpMyAdmin
    - contenedor spanel-phpmyadmin y su red
  must_not_affect:
    - credenciales, usuarios y datos de las bases
    - rutas HTTP y schemas de hosting
    - sitios estaticos y sus contenedores
    - proxy, rewrite y streaming de phpMyAdmin
    - mail, domains, tenant, kernel y filemanager
```

## Composition

```yaml
composition:
  requires_aspecs:
    - 0017
    - 0019
    - 0021
    - 0031
  must_compose_with:
    - plugin hosting (credencial y ciclo de vida del sitio)
    - contenedor spanel-phpmyadmin
    - red Docker del sitio y de la app
  systemic_invariants:
    - un dueno por Host
    - ningun secreto en argv, logs ni auditoria
    - phpMyAdmin nunca publicado a Internet
  composition_checks:
    - PYTHONPATH=vendor/systutor-core:vendor/systutor-core/src .venv/bin/python -m pytest vendor/systutor-core/plugins/hosting/backend/tests vendor/systutor-core/plugins/domains/backend/tests vendor/systutor-core/plugins/tenant/backend/tests -q
```

## Structural Constraints

```yaml
structural_constraints:
  primary_rule: el transporte Docker y la red viven en un modulo puro; el servicio decide cuando conectar y el proxy solo transporta
  entrypoints_must_stay_thin: true
  review_threshold_lines: 400
  extraction_threshold_lines: 600
  preferred_new_logic_locations:
    - vendor/systutor-core/plugins/hosting/backend/network.py
    - vendor/systutor-core/plugins/hosting/backend/phpmyadmin_proxy.py
```

## Traceability

- Requirement: un solo phpMyAdmin para el MariaDB heredado y los MariaDB por
  sitio, sin migrar datos ni exponer bases.
- owner: luc444s
- approver: luc444s
- Commit: `f102784`
- Deployment: crear `spanel-db`, recrear `spanel-phpmyadmin` con el script,
  activar las dos variables y reconstruir la app.

## Definition of Done

- [x] Objetivo satisfied
- [x] Scope respected
- [x] Contract satisfied
- [x] Independent falsable truth exists now
- [x] Invariants preserved
- [x] Verification passed
- [x] Rollback / compensation is honest
- [x] Composition checks passed when applicable
- [x] No unrelated changes
- [x] Structural constraints respected
- [x] Traceability established
