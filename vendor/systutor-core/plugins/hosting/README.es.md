# WordPress Hosting

[English](README.md) | Español

A.SPEC 0013/0014. Este plugin permite a un administrador de tenant ver los sitios
WordPress que **su tenant posee**, listar los usuarios de uno de esos sitios y
restablecer la contraseña de exactamente un usuario por su ID numérico de
WordPress.

## La propiedad es una fila, no una etiqueta

`hosting_sites` es la fuente de verdad. Un contenedor no es propiedad de nadie
porque exista, porque su imagen diga `wordpress` o porque lleve una etiqueta
`spanel.tenant`: cualquiera con acceso a Docker puede escribir una etiqueta. Toda
búsqueda de sitio filtra `tenant_id` contra el `TenantContext` del core.

Los contenedores existentes no tienen propietario hasta que un operador los
adopta:

```bash
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo
python3 scripts/hosting_adopt.py --container spanel-demowp-wp --tenant demo --dry-run
```

La adopción es un CLI de operador, nunca un endpoint HTTP, y rechaza un
contenedor que ya esté en `hosting_sites`.

## API

Las tres rutas requieren el `TenantContext` del kernel:

- `GET /api/v1/plugins/hosting/sites` — `hosting.sites.read`
- `GET /api/v1/plugins/hosting/sites/{site_id}/users` — `hosting.users.read`
- `POST /api/v1/plugins/hosting/sites/{site_id}/users/{wp_user_id}/password` —
  `hosting.users.password`
- `POST /api/v1/plugins/hosting/sites` — `hosting.sites.create`; acepta
  `{"kind": "wordpress"|"static", "name": "...", "domain": "..."}` y devuelve
  la contraseña de administrador generada una sola vez (`null` para un sitio
  estático). `admin_email` ya no se acepta: WordPress usa el email del usuario
  autenticado, con `admin@<domain>` como fallback. El dominio debe pertenecer al
  `site_suffix` del tenant y estar libre en `hosting_sites` y en los extras del
  proxy.
- `POST /api/v1/plugins/hosting/sites` con `kind="static"` aprovisiona
  `spanel-<slug>-web` más su volumen homónimo, siembra `index.html`,
  `styles.css` y `app.js`, y los sirve con la imagen del perfil elegido
  (por defecto `nginx`, imagen `SPANEL_HOSTING_STATIC_IMAGE`). No crea base de
  datos, usuario de WordPress ni contraseña. El payload puede llevar
  `"profile": "<id>"` (A.SPEC 0042): un id desconocido es
  `404 hosting_profile_unknown`, y un perfil con `kind="wordpress"` es un
  error de esquema 422. Cada contenedor estático lleva la etiqueta
  `spanel.profile=<id>`. Cuando el plugin `domains` está habilitado,
  su publicador de file provider es dueño de la ruta primaria; en caso
  contrario el contenedor recibe etiquetas Traefik exactamente como WordPress
  (con el puerto del perfil).
  `start`, `stop`, `restart`,
  `logs` y el filemanager funcionan para ambos tipos; `config`, `database`,
  `phpmyadmin`, `users`, `secrets/salts` y el restablecimiento de contraseña
  responden `409 hosting_site_wrong_kind` en un sitio estático.
- `GET /api/v1/plugins/hosting/static-profiles` — `hosting.sites.read`; lista
  los perfiles estáticos que consume el selector de "crear sitio" (A.SPEC 0042).
  Las respuestas llevan los NOMBRES de las variables de entorno, nunca sus
  valores.
- `POST/PUT/DELETE /api/v1/plugins/hosting/static-profiles/{profile_id}` —
  `hosting.profiles.manage` (RBAC de plataforma, sin `is_superadmin`). Cada
  cambio audita `hosting.profile.{created,updated,deleted}`; `DELETE` responde
  `409 hosting_profile_in_use` cuando un sitio usa el perfil y
  `409 hosting_profile_builtin` en el `nginx` integrado (su imagen sigue siendo
  editable, su nombre es inmutable y nunca se puede borrar).
- `POST /api/v1/plugins/hosting/sites/{site_id}/{start|stop|restart}` —
  `hosting.sites.manage`
- `GET /api/v1/plugins/hosting/sites/{site_id}/logs?lines=200` — `hosting.sites.read`
- `DELETE /api/v1/plugins/hosting/sites/{site_id}` — `hosting.sites.delete`
  (solo permiso de plataforma; elimina permanentemente los contenedores y datos
  del sitio).
- `GET /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.read`;
  devuelve el entorno editable, la lista de claves editables y los NOMBRES de
  los secretos que existen. Los valores de los secretos nunca se devuelven.
- `PATCH /api/v1/plugins/hosting/sites/{site_id}/config` — `hosting.sites.manage`;
  acepta `{"env": {"KEY": "value"}}` solo para claves de la lista blanca. El
  cambio se escribe en el archivo de entorno estable del sitio y se aplica
  recreando el contenedor desde su propio `docker inspect`, reutilizando los
  volúmenes con nombre y las etiquetas. Las claves de base de datos
  (`WORDPRESS_DB_*`, `MYSQL_PWD`) se rechazan: un tenant que pudiera
  reescribirlas podría apuntar su sitio a la base de datos de otro tenant.
- `POST /api/v1/plugins/hosting/sites/{site_id}/secrets/salts` —
  `hosting.sites.manage`; rota las sales de autenticación de WordPress con
  `wp config shuffle-salts`. Las sales viven en `wp-config.php` dentro del
  volumen del sitio y PHP las lee en cada petición, así que no hace falta
  reiniciar ni recrear. La respuesta dice qué se rotó, nunca el valor nuevo.
- `POST /api/v1/plugins/hosting/sites/{site_id}/database` — `hosting.sites.manage`;
  devuelve la credencial de base de datos **propia** del sitio (`host`, `database`,
  `user`, `password`) y la URL de phpMyAdmin, con `Cache-Control: no-store`.
  El host viene del entorno del contenedor y debe ser un nombre de contenedor
  Docker (un MariaDB compartido o uno por sitio); un esquema, `localhost` o una
  IP produce 409. La contraseña se audita como `hosting.site.database_access`
  sin el valor.

Los esquemas de petición prohíben campos extra: un cliente no puede enviar
`tenant_id`, un nombre de contenedor ni ningún otro selector de Docker. Un sitio
que pertenece a otro tenant es indistinguible de un sitio inexistente y devuelve
404.

## UI de configuración

El diálogo de detalle del sitio tiene una acción `Configuración`. Abre el entorno
editable y los nombres de los secretos; los valores nunca se muestran. Sin
`hosting.sites.manage`, o en un sitio con `spanel.managed=false`, es de solo
lectura. Guardar envía solo los valores cambiados y no vacíos y recrea el
contenedor, así que el diálogo advierte sobre el breve tiempo de inactividad.
`Rotar claves de seguridad` pide confirmación primero, porque rotar las sales
cierra todas las sesiones de WordPress.

## Acceso a la base de datos

`Base de datos`, junto a `Configuración` y visible solo con
`hosting.sites.manage`, revela el usuario de base de datos propio del sitio
WordPress y abre phpMyAdmin en una pestaña nueva del mismo origen. Es la
credencial que el sitio ya usa, así que no hay usuario SQL nuevo, ni grant, ni
migración; phpMyAdmin ve exactamente una base de datos. El host se deriva del
sitio poseído y se valida como nombre de contenedor Docker. El proxy usa
`SPANEL_PHPMYADMIN_INTERNAL_URL` sobre una red Docker interna compartida; el
contenedor de la app debe resolver esa dirección. Un POST autenticado con Bearer
inicia sesión en el servidor como el propio usuario de BD de WordPress del sitio
y devuelve un ticket de pestaña de un solo uso válido 30 segundos. Una cookie
opaca con alcance de sitio autoriza las peticiones posteriores al proxy. Ninguna
contraseña de base de datos aparece en la URL de la pestaña, en la cookie del
proxy ni en la caché de sesión. `SPANEL_PHPMYADMIN_URL` solo permanece en la
respuesta de base de datos heredada y la UI ya no la sigue.

### Un phpMyAdmin para todas las bases de datos (A.SPEC 0033)

Con `SPANEL_PHPMYADMIN_ARBITRARY=true` y `SPANEL_PHPMYADMIN_DB_NETWORK`
definido, phpMyAdmin corre como hub multiservidor (`PMA_ARBITRARY=1` más el
`ArbitraryServerRegexp` en `scripts/phpmyadmin_arbitrary.php`). El POST de login
lleva `pma_servername=<database container>`, el host ya validado por
`database_binding`. Antes de ese POST el servicio lee
`NetworkSettings.Networks` del `docker inspect` de la base de datos; ejecuta
`docker network connect <network> <container>` solo cuando falta la interfaz,
así que un segundo login no hace nada. Las redes originales del contenedor
quedan intactas. `network.py` es dueño de esa decisión pura; `service.py` solo
realiza el transporte.

Aprovisionar un sitio WordPress conecta su MariaDB nueva a la misma red
justo después de crear el contenedor. Si esa conexión falla, la creación sigue
el camino de fallo existente y elimina sus propios contenedores y volúmenes. La
fila de auditoría `hosting.site.phpmyadmin_access` incluye `database`, `host` y,
solo cuando hubo conexión, `network`; nunca la contraseña.

Cualquiera de los dos ajustes por separado (o ambos apagados/vacíos) conserva
el comportamiento anterior byte a byte: `PMA_HOST` fijo, sin `pma_servername` y
sin comando de red Docker. El script de despliegue crea `spanel-db` como
`--internal`, mantiene el binding en `127.0.0.1` y conecta a ella tanto el
MariaDB heredado como phpMyAdmin:

```bash
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
```

Los cuatro valores se leen primero del entorno del contenedor
(`WORDPRESS_DB_*`). Cuando el entorno no los declara, el plugin lee
`/var/www/html/wp-config.php` como **texto** y extrae las definiciones literales
de `DB_HOST`, `DB_NAME`, `DB_USER` y `DB_PASSWORD`. No ejecuta PHP y no modifica
el archivo, el contenedor ni la base de datos. Se admite un contenedor de base
de datos fuera de WordPress, incluido un puerto personalizado como
`mariadb:3306`; el nombre del contenedor se valida igual que en la ruta de
entorno. La lista de usuarios y el restablecimiento de contraseña usan las
mismas dos fuentes.

### Sitios adoptados con alias de red (A.SPEC 0034)

Un sitio de `docker compose` puede declarar `WORDPRESS_DB_HOST=db`, donde `db`
es un alias en la red de compose en lugar de un nombre de contenedor. El panel
primero intenta `docker inspect <host>`; solo cuando eso falla lee el
`NetworkSettings.Networks` propio del contenedor WordPress, lista los
contenedores conectados a esas redes y compara el host declarado con sus
`Aliases`. Solo se acepta una coincidencia: ninguna o dos coincidencias dejan el
host original en su sitio para que siga disparándose el camino de error
controlado existente. El host crudo sigue siendo lo que recibe WordPress; el
nombre de contenedor resuelto se usa solo para `docker inspect`, `docker exec`,
SQL y el servidor phpMyAdmin.

El nombre del contenedor se resuelve desde la fila poseída y luego se usa con el
core probado de Docker/WordPress de A.SPEC 0012 (`discovery`, `database`,
`passwords`, `reset`). Un contenedor que ya no existe se lista como `missing`;
las rutas de usuarios y reset responden 409 y nombran el contenedor.

## Reset

`POST .../users/{wp_user_id}/password` resuelve el usuario solo por ID, genera
una contraseña cuando el cuerpo la omite, escribe exactamente una fila, la lee
de vuelta por ID y devuelve la contraseña generada una vez con
`Cache-Control: no-store`. Una contraseña enviada por el llamante nunca se
devuelve. Ninguna contraseña ni hash se escribe en logs, detalles de auditoría
ni respuestas de listado.

La escritura se audita con `record_audit` (`hosting.user.password_reset`); este
plugin no declara eventos en A.SPEC 0013.

## Configuración

El transporte Docker viene del `.env` del core:

- `SPANEL_DOCKER_TRANSPORT` (`ssh` o `local`)
- `SPANEL_DOCKER_SSH_HOST`, `SPANEL_DOCKER_SSH_USER`, `SPANEL_DOCKER_SSH_PORT`,
  `SPANEL_DOCKER_SSH_STRICT_HOST_KEY`
- `SPANEL_DOCKER_SSH_PASSWORD`, con fallback a `MAIL_SERVER_PASSWORD`
- `SPANEL_HOSTING_COMMAND_TIMEOUT` (por defecto 60 segundos)
- `SPANEL_HOSTING_WP_IMAGE` (`wordpress:latest`), `SPANEL_HOSTING_DB_IMAGE`
  (`mariadb:11`), `SPANEL_HOSTING_NETWORK` (`proxy`, debe existir ya)
- `SPANEL_HOSTING_TRAEFIK` (`true`), `SPANEL_HOSTING_INSTALL_TIMEOUT` (180 s),
  `SPANEL_HOSTING_LOG_LINES` (200, máximo 1000)
- `SPANEL_HOSTING_SITES_DIR` (`/opt/spanel/sites`): el directorio estable por
  sitio en el host Docker. La ruta de configuración escribe `wp.env` allí con
  modo 600, así el secreto de base de datos de WordPress sobrevive a un reinicio
  y se puede volver a leer. Antes, los archivos de entorno vivían en `/tmp` y se
  borraban tras el aprovisionamiento.
- `SPANEL_PHPMYADMIN_URL` (opcional): URL base de la instancia phpMyAdmin. Puede
  contener `{server}`; se mantiene por compatibilidad en la respuesta de base de
  datos.
- `SPANEL_PHPMYADMIN_INTERNAL_URL` (por defecto `http://spanel-phpmyadmin:8080`):
  upstream solo de servidor en una red compartida por phpMyAdmin y la app.
- `SPANEL_PHPMYADMIN_SESSION_TTL` (1500 s, máximo 3600),
  `SPANEL_PHPMYADMIN_TIMEOUT` (15 s).
- `SPANEL_PHPMYADMIN_ARBITRARY` (`false`): con `true`, el login envía
  `pma_servername`; se ignora salvo que `SPANEL_PHPMYADMIN_DB_NETWORK` esté
  definido.
- `SPANEL_PHPMYADMIN_DB_NETWORK` (vacío): red Docker interna compartida por
  phpMyAdmin y cada MariaDB de sitio. Vacío desactiva el modo multibase de datos.
- `SPANEL_HOSTING_FILES_ROOT` (`/var/www/html`): raíz del lado del servidor que
  Hosting entrega al plugin `filemanager` para un sitio poseído en ejecución. El
  cliente nunca elige el contenedor ni una ruta absoluta.

El aprovisionamiento crea dos contenedores etiquetados y dos volúmenes
etiquetados, instala WordPress con `wordpress:cli` y solo entonces registra la
propiedad. Una instalación fallida compensa eliminando únicamente los recursos
de ese intento. Las contraseñas de base de datos y de administrador nunca
viajan en argumentos de comando; los archivos de entorno transitorios se borran
tras el aprovisionamiento. La red creada es compartida y el camino de borrado
del sitio nunca la elimina.

Ningún secreto se registra en logs y el objeto de configuración nunca se
devuelve por una ruta.

## Rollback

Deshabilita el plugin: las rutas y la navegación desaparecen y las filas de
`hosting_sites` permanecen. Un rollback completo ejecuta el downgrade de la
migración, que elimina la tabla y por tanto pierde los metadatos de propiedad;
los contenedores y volúmenes quedan intactos.
