# Scripts operativos de SPanel

[English](README.md) | Español

Este directorio reúne herramientas de operador, migraciones puntuales,
laboratorios y pruebas E2E. Este documento es el índice para decidir **qué
script usar, dónde ejecutarlo y qué puede modificar**.

Ejecuta los comandos desde la raíz del repositorio, salvo que una sección diga
lo contrario:

```bash
cd /ruta/a/mailadmin
python3 scripts/<script>.py --help
```

## Reglas de seguridad

- Revisa primero `--help` y usa `--dry-run`, modo plan o ejecución sin
  `--apply`/`--execute` cuando exista.
- No confundas **Hosting** con **Apps**. Hosting registra sitios WordPress o
  estáticos existentes; Apps administra proyectos Docker Compose mediante su
  runtime o API.
- Los scripts que usan la base de datos cargan la configuración normal de
  SPanel. Verifica que apunte al entorno correcto antes de ejecutarlos.
- Los scripts Docker actúan sobre el daemon seleccionado por el entorno
  (`DOCKER_HOST` o las variables `SPANEL_DOCKER_SSH_*`, según la herramienta).
- No pases secretos por argumentos salvo que el propio CLI lo requiera. Los
  argumentos pueden quedar visibles en el historial y en la lista de procesos.
- Haz un respaldo antes de cualquier operación marcada como **destructiva**.

### Niveles usados en este documento

| Nivel | Significado |
|---|---|
| **Lectura** | Diagnóstico; no debería cambiar estado. |
| **Plan por defecto** | No escribe hasta agregar `--apply`, `--execute`, `--fix` o `--yes`. |
| **Escritura** | Puede modificar base de datos, configuración, rutas o recursos Docker. |
| **Destructivo** | Puede borrar, reemplazar o restaurar recursos. Requiere revisión especial. |

## Guía rápida

| Necesidad | Script |
|---|---|
| Adoptar un WordPress concreto | [`hosting_adopt.py`](#hosting_adoptpy) |
| Descubrir y sincronizar todos los sitios publicados | [`hosting_sync.py`](#hosting_syncpy) |
| Completar el dominio de un sitio adoptado | [`hosting_set_domain.py`](#hosting_set_domainpy) |
| Dar de baja un sitio sin borrar su contenedor | [`hosting_down.py`](#hosting_downpy) |
| Eliminar completamente un WordPress gestionado | [`hosting_delete_wordpress.py`](#hosting_delete_wordpresspy) |
| Respaldar o restaurar un sitio | [`site_backup.py`](#site_backuppy) / [`site_restore.py`](#site_restorepy) |
| Operar Apps por HTTP | [`spanel_apps.py`](#spanel_appspy) |
| Auditar o desplegar Compose directamente | [`app_deploy.py`](#app_deploypy) |
| Publicar o retirar un dominio en Traefik | [`proxy_domain.py`](#proxy_domainpy) |
| Refrescar/adoptar el inventario observado | [`domains_sync.py`](#domains_syncpy) / [`domains_adopt.py`](#domains_adoptpy) |
| Diagnosticar/reparar rutas y TLS | [`spanel-repair.py`](#spanel-repairpy) |
| Administrar certificados de correo | [`install_cert_agent.py`](#install_cert_agentpy) y [`spanel_cert_agent/`](#spanel_cert_agent) |

## Hosting y WordPress

### `hosting_adopt.py`

**Plan por defecto con `--dry-run`; escritura sin esa opción.** Adopta un único
contenedor WordPress existente en un tenant. Verifica que sea WordPress y crea
una fila `hosting_sites` más su auditoría. No inicia, detiene ni modifica el
contenedor.

```bash
python3 scripts/hosting_adopt.py \
  --container spanel-demo-wp --tenant demo --domain demo.example --dry-run

# Después de revisar:
python3 scripts/hosting_adopt.py \
  --container spanel-demo-wp --tenant demo --domain demo.example
```

No sirve para adoptar recursos de Apps ni sitios estáticos.

### `hosting_sync.py`

**Plan por defecto; escribe únicamente con `--apply`.** Recorre los
contenedores en ejecución, lee sus labels de Traefik, deduce dominio y tipo,
resuelve el tenant y reconcilia `hosting_sites`. Puede adoptar sitios WordPress
y estáticos o completar datos de filas existentes. Nunca inicia, detiene o
elimina contenedores.

```bash
# Revisar todo
python3 scripts/hosting_sync.py

# Revisar solamente uno y resolver datos ambiguos
python3 scripts/hosting_sync.py \
  --only sitio-web \
  --assign sitio-web=demo \
  --domain sitio-web=www.example.com \
  --kind sitio-web=static

# Aplicar exactamente la misma selección
python3 scripts/hosting_sync.py \
  --only sitio-web \
  --assign sitio-web=demo \
  --domain sitio-web=www.example.com \
  --kind sitio-web=static \
  --apply
```

Usa `--ignore` para excluir un contenedor y `--retire-old` para retirar filas
antiguas configuradas por el script; esto último no borra el contenedor.

### `hosting_set_domain.py`

**Plan por defecto con `--dry-run`; escritura sin esa opción.** Lista sitios o
asigna el dominio primario a un contenedor ya adoptado. Comprueba unicidad,
dominios extra y el sufijo permitido del tenant.

```bash
python3 scripts/hosting_set_domain.py --list
python3 scripts/hosting_set_domain.py \
  --container sitio-web --domain sitio.example.com --dry-run
```

### `hosting_down.py`

**Plan por defecto; destructivo para el registro con `--execute`.** Baja
lógicamente un sitio: retira su ruta administrada y elimina la fila de Hosting,
pero conserva contenedores, imágenes, volúmenes, datos y la reserva del dominio.
Exige confirmación exacta.

```bash
python3 scripts/hosting_down.py --container sitio-web
python3 scripts/hosting_down.py \
  --container sitio-web --execute --confirm sitio-web
```

Si la ruta vive en labels del propio contenedor, el script bloquea la baja. El
flujo de reemplazo puede conservarla temporalmente con
`--keep-labeled-route`.

### `hosting_delete_wordpress.py`

**Plan por defecto; destructivo con `--execute`.** Elimina un WordPress
gestionado, su contenedor y volumen, y borra únicamente su base indicada por
`WORDPRESS_DB_NAME`. No elimina el contenedor, volumen o imagen de MariaDB.
También limpia secretos y, salvo `--keep-domain`, libera el dominio.

```bash
python3 scripts/hosting_delete_wordpress.py --container spanel-demo-wp
python3 scripts/hosting_delete_wordpress.py \
  --container spanel-demo-wp --execute --confirm spanel-demo-wp
```

### `reset_wp_password.py`

**Lectura con `--listar` o `--dry-run`; escritura en el modo normal.** Lista
usuarios o cambia una sola fila de `wp_users` sin detener WordPress. Si no se
proporciona contraseña, genera una y la muestra una sola vez.

```bash
python3 scripts/reset_wp_password.py spanel-demo-wp --listar
python3 scripts/reset_wp_password.py spanel-demo-wp --usuario admin --dry-run
python3 scripts/reset_wp_password.py spanel-demo-wp --usuario admin
```

## Respaldos de sitios

### `site_backup.py`

**Escritura no destructiva.** Genera un ZIP verificable de un WordPress
(archivos y base) o un sitio estático (archivos). Detecta el tipo y evita
guardar credenciales en el manifiesto.

```bash
python3 scripts/site_backup.py --container sitio-web --dry-run
python3 scripts/site_backup.py --container sitio-web --out /root/backups
python3 scripts/site_backup.py --verify /root/backups/wp-sitio-web-FECHA.zip
```

Consulta `--help` para overrides de base de datos. Evita `--db-pass` en shells
compartidos; es preferible que el script resuelva las credenciales desde el
contenedor.

### `site_restore.py`

**Destructivo.** Valida un ZIP producido por `site_backup.py`, crea primero un
respaldo de seguridad del estado actual y restaura archivos y, en WordPress, la
base. Intenta rollback automático si falla.

```bash
python3 scripts/site_restore.py \
  --zip /root/backups/wp-sitio-web-FECHA.zip \
  --container sitio-web \
  --yes
```

### `site_backup_link.py`

**Lectura de un respaldo; abre temporalmente un servidor HTTP/HTTPS.** Sirve un
ZIP válido mediante un enlace de un solo uso y termina tras la descarga o el
TTL. Fuera de loopback exige origen público y, normalmente, TLS.

```bash
python3 scripts/site_backup_link.py \
  --zip /root/backups/wp-sitio-web-FECHA.zip --ttl 600
```

## Apps

### `spanel_apps.py`

**Escritura según el subcomando.** CLI recomendado para operar Apps por la API.
No accede directamente a PostgreSQL, Redis, Docker ni SSH. Requiere la URL y un
token por entorno o entrada estándar:

```bash
export SPANEL_APPS_API_URL=https://panel.example.com
export SPANEL_APPS_TOKEN='...'
python3 scripts/spanel_apps.py list
python3 scripts/spanel_apps.py services UUID_APLICACION
python3 scripts/spanel_apps.py --json status UUID_OPERACION
```

Subcomandos disponibles: `providers`, `repositories`, `add`, `inspect`,
`deploy`, `status`, `list`, `services`, `refresh`, `logs`, `start`, `stop`,
`restart`, `down`, `domain-list`, `domain-assign`, `domain-remove`,
`domain-probe` y `delete`. Usa `python3 scripts/spanel_apps.py <comando> --help`
para los argumentos de cada uno.

### `app_deploy.py`

**Escritura/destructivo según el subcomando.** Herramienta de runtime directa
para auditar y desplegar un repositorio Docker Compose sin pasar por la API.
Soporta `inspect`, `deploy`, `status`, `logs` y `down`, con transporte local o
SSH. `down --purge-data` elimina datos y exige confirmación.

```bash
python3 scripts/app_deploy.py inspect --source-dir /ruta/al/checkout
python3 scripts/app_deploy.py deploy --source-dir /ruta/al/checkout \
  --project demo --dry-run
python3 scripts/app_deploy.py status --project demo
```

Los proyectos creados por Apps llevan el label `io.spanel.app.owner`. Este CLI
no tiene un comando de adopción para proyectos Compose ajenos.

### `configure_apps.py`

**Plan con `--dry-run`; escritura en modo normal.** Configura exclusivamente
variables `SPANEL_APPS_*`, crea o valida la clave de cifrado y, con `--restart`,
recrea `app` y `apps-worker` en orden seguro. Nunca ejecuta `docker compose
down` ni imprime la clave.

```bash
python3 scripts/configure_apps.py --public-url https://panel.example.com --dry-run
python3 scripts/configure_apps.py --public-url https://panel.example.com --restart
```

### `migrate_env.py`

**Plan con `--dry-run`; escritura en base de datos en modo normal.** Migra el
`app.env` de un deployment existente al almacenamiento cifrado reutilizable de
Apps, excluyendo credenciales administradas. Debe ejecutarse **dentro del
contenedor `app`**, donde están disponibles los plugins y la configuración.

```bash
docker compose exec app python3 /app/scripts/migrate_env.py \
  --slug mi-app --dry-run
```

### `refresh_routes.py`

**Plan con `--dry-run`; escritura en modo normal.** Vuelve a renderizar las
rutas de Domains para Apps sin usar la UI ni redesplegar. Debe ejecutarse
**dentro del contenedor `app`**. `--restart-proxy` además reinicia Traefik.

```bash
docker compose exec app python3 /app/scripts/refresh_routes.py --dry-run
docker compose exec app python3 /app/scripts/refresh_routes.py --slug mi-app
```

### Scripts E2E de Apps

| Script | Nivel | Propósito |
|---|---|---|
| `e2e_apps_api.py` | **Destructivo** | E2E black-box contra login, API, worker y Docker reales; crea y limpia una aplicación. |
| `e2e_apps_software_editores.py` | **Destructivo** | E2E aislado directo contra el repositorio Software para Editores; genera artefactos de diagnóstico redactados. |

No son herramientas de administración cotidiana. Úsalos solo en un laboratorio
preparado y revisa sus variables/requisitos con `--help` y sus pruebas en
`scripts/tests/`.

### `configure_powerdns.sh`

Automatiza el laboratorio autoritativo y el despliegue privado del panel. El
plan es de solo lectura; `apply` levanta PowerDNS, genera `.env.powerdns` en
modo `0600`, monta la clave por ruta (nunca por valor), reconstruye el panel y
verifica la API autenticada desde el contenedor `app`.

```bash
scripts/configure_powerdns.sh plan
scripts/configure_powerdns.sh apply
scripts/configure_powerdns.sh status
scripts/configure_powerdns.sh verify
```

## Dominios, proxy y sitios estáticos

### `domains_sync.py`

**Plan por defecto; escribe solo con `--apply`.** Lee una instantánea completa
de los YAML gestionados de Traefik, valida sitio y propiedad del tenant, y
refresca atómicamente la observación PostgreSQL que usa la vista global de
Dominios. Nunca edita Traefik. `--json` produce un informe automatizable.

```bash
python3 scripts/domains_sync.py
python3 scripts/domains_sync.py --apply --json
```

### `domains_adopt.py`

**Plan por defecto; escribe solo con `--apply`.** Reclama la propiedad
administrativa de un dominio ya publicado cuando está libre y el tenant/sitio
coincide con su YAML gestionado. También actualiza la observación, pero nunca
reescribe YAML.

```bash
python3 scripts/domains_adopt.py --domain example.com --tenant demo
python3 scripts/domains_adopt.py --domain example.com --tenant demo --site SITE_ID --apply
```

`benchmark_domains_global.py` mide por separado sincronización y lectura global
proyectada con transporte local o Docker.

### `proxy_domain.py`

**Lectura o escritura según el subcomando.** Diagnostica Traefik y administra
dominios/rutas/certificados mediante `doctor`, `list`, `add`, `status` y
`remove`. El perfil `internal` usa el laboratorio; `public` usa el proxy real.

```bash
python3 scripts/proxy_domain.py --profile public doctor
python3 scripts/proxy_domain.py --profile public add \
  --site sitio-web --domain sitio.example.com
python3 scripts/proxy_domain.py --profile public status --domain sitio.example.com
```

### `spanel-static.py`

**Escritura/destructivo según el subcomando.** Crea y administra sitios
HTML/CSS/JS en nginx con volumen y ruta HTTPS. Ofrece `create`, `list`,
`status`, `delete` y `doctor`, localmente o por SSH.

```bash
python3 scripts/spanel-static.py create \
  --name pagina-demo --domain pagina-demo.spanel
python3 scripts/spanel-static.py list
python3 scripts/spanel-static.py delete --name pagina-demo
```

Documentación detallada: [`spanel_static/README.md`](spanel_static/README.md).

### `spanel-profiles.py`

**Escritura/destructivo según el subcomando.** Aprovisiona y verifica perfiles
estáticos deterministas (`plain`, `react`, `boot`, `astro`) con `up`, `verify`,
`status` y `down`. Está orientado a laboratorio/validación, no a sitios de
clientes.

```bash
python3 scripts/spanel-profiles.py status
python3 scripts/spanel-profiles.py up --only astro
python3 scripts/spanel-profiles.py verify
python3 scripts/spanel-profiles.py down
```

### `spanel-repair.py`

**Plan por defecto; escritura con `--fix`.** Detecta rutas Traefik incorrectas,
certificado por defecto, falta de `certResolver` y archivos huérfanos. Con
`--fix` puede parchear YAML, mover huérfanos y reiniciar Traefik respetando un
cooldown.

```bash
python3 scripts/spanel-repair.py
python3 scripts/spanel-repair.py --json
python3 scripts/spanel-repair.py --fix --only-host sitio.example.com
```

### `traefik-lab.sh`

**Escritura/destructivo dentro del laboratorio.** Crea un Traefik aislado, una
CA ACME local y DNS interno. No toca el Traefik real. `plan` no cambia nada;
`down --purge` elimina también datos y CA del laboratorio.

```bash
./scripts/traefik-lab.sh plan
./scripts/traefik-lab.sh up
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down
```

`adopt` solo inspecciona el Traefik real; no lo registra ni modifica.
Documentación detallada: [`traefik-lab/README.md`](traefik-lab/README.md).

### `probe_docker.py`

**Lectura.** Prueba la configuración y conectividad local/SSH con Docker,
ejecuta `docker ps` e identifica candidatos WordPress. Es un diagnóstico; no
adopta ni modifica contenedores.

```bash
python3 scripts/probe_docker.py
python3 scripts/probe_docker.py --env-file otro.env
```

## phpMyAdmin

### `phpmyadmin_up.sh`

**Escritura/destructivo limitado.** Levanta phpMyAdmin como hub interno para
bases existentes, crea la red interna si falta y conecta MariaDB sin sacarlo de
sus redes. `--down` elimina únicamente el contenedor phpMyAdmin.

```bash
./scripts/phpmyadmin_up.sh --host mariadb --dry-run
./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
./scripts/phpmyadmin_up.sh --down --yes
```

`phpmyadmin_arbitrary.php` no es un CLI: es la configuración montada por el
script. Restringe los hosts aceptados a `mariadb` o nombres terminados en
`-db`.

## Certificados de correo y tareas programadas

### `install_cert_agent.py`

**Plan con `--dry-run`; escritura como root en modo normal.** Descubre linajes
Certbot, genera la configuración del agente privilegiado e instala sus unidades
systemd. Es idempotente.

```bash
sudo python3 scripts/install_cert_agent.py --dry-run
sudo python3 scripts/install_cert_agent.py --allowlist mail.example.com
sudo python3 scripts/install_cert_agent.py --check --json
```

La arquitectura completa está en
[`../docs/certificate-manager.es.md`](../docs/certificate-manager.es.md).

### `spanel_cert_agent/`

**Escritura privilegiada según el subcomando.** Paquete del agente de
certificados. Su entrada es:

```bash
PYTHONPATH=scripts python3 -m spanel_cert_agent --help
PYTHONPATH=scripts python3 -m spanel_cert_agent \
  --config /etc/spanel/cert-agent.toml status
```

Subcomandos: `run-once`, `refresh`, `status`, `verify`, `audit`, `sync`,
`install` y `rollback`. Normalmente lo invocan las unidades systemd, no un
operador interactivo. `certbot-deploy-hook.sh` es el hook de Certbot y tampoco
se ejecuta manualmente.

### `mail_cert.py`

**Lectura o escritura crítica según el subcomando.** Flujo anterior/específico
para certificados internos de docker-mailserver. `status`, `check` y `audit`
diagnostican; `issue` y `renew` pueden emitir el certificado y recrear el
contenedor DMS desde su inspección, con rollback si la verificación falla.

```bash
python3 scripts/mail_cert.py status
python3 scripts/mail_cert.py audit --json
python3 scripts/mail_cert.py issue --dry-run
python3 scripts/mail_cert.py renew --all --yes
```

`mail-cert.service` y `mail-cert.timer` son unidades específicas del entorno
original y contienen rutas/host concretos. Revísalas y ajústalas antes de
instalarlas; para instalaciones nuevas se prefiere el agente genérico.

### `install_routes_timer.py`

**Plan con `--dry-run`; escritura como root en modo normal.** Instala,
actualiza o elimina el timer systemd que ejecuta `refresh_routes.py` dentro del
contenedor `app`.

```bash
sudo python3 scripts/install_routes_timer.py --dry-run
sudo python3 scripts/install_routes_timer.py --interval-minutes 15
sudo python3 scripts/install_routes_timer.py --uninstall
```

## Tenants y dominios de base de datos

### `sync_tenant_domains.py`

**Plan por defecto; escritura con `--apply`.** Elimina de `tenant_domains` las
filas redundantes que duplican el dominio primario de su propio tenant y
reporta colisiones. Siempre produce un informe JSON. No toca Docker, proxy,
certificados, usuarios ni sitios.

```bash
python3 scripts/sync_tenant_domains.py
python3 scripts/sync_tenant_domains.py --apply
```

### `unify_tenants.py`

**Plan por defecto; escritura con `--apply`; `--purge` es destructivo.**
Consolida dominios, usuarios y sitios en un tenant objetivo, desactiva los
orígenes y guarda un plan JSON reversible. No toca contenedores ni proxy.

```bash
python3 scripts/unify_tenants.py \
  --target-slug demo --from-slug antiguo --move-all-sites

python3 scripts/unify_tenants.py \
  --target-slug demo --from-slug antiguo --move-all-sites --apply

python3 scripts/unify_tenants.py \
  --rollback logs/unify-tenants-FECHA.json --apply
```

## Archivos auxiliares

### Paquetes internos

No se ejecutan archivo por archivo; implementan los entrypoints documentados:

| Directorio | Entry point |
|---|---|
| `spanel_static/` | `python3 scripts/spanel-static.py` |
| `spanel_profiles/` | `python3 scripts/spanel-profiles.py` |
| `spanel_cert_agent/` | `PYTHONPATH=scripts python3 -m spanel_cert_agent` |

### Unidades systemd

`scripts/systemd/` contiene plantillas para:

| Unidad | Función |
|---|---|
| `spanel-cert-agent.path/service/timer` | Procesar solicitudes y refrescar el estado de certificados. |
| `spanel-cert-audit.service/timer` | Auditar periódicamente certificados. |
| `spanel-cert-deploy.service` | Hook de despliegue de certificados. |
| `spanel-repair.service/timer` | Ejecutar reparación periódica de rutas/TLS. |
| `spanel-routes-refresh.service/timer` | Reconciliar rutas de Apps. |

No copies una unidad a producción sin revisar rutas, usuario, permisos y
variables del host. Los instaladores generan o adaptan las unidades cuando
corresponde.

### Pruebas

`scripts/tests/` y `spanel_cert_agent/tests/` contienen pruebas automatizadas;
no son comandos operativos. Las pruebas con `_live` o los E2E pueden requerir
Docker real, SSH, credenciales y recursos de laboratorio. No las ejecutes en
producción sin leer primero el archivo.

Ejemplo de pruebas unitarias sin laboratorio:

```bash
python3 -m pytest scripts/tests/test_hosting_sync.py -q
python3 -m pytest scripts/tests/test_spanel_apps.py -q
```

## Mantenimiento de este índice

Al agregar, renombrar o eliminar un entrypoint dentro de `scripts/`:

1. documenta propósito, contexto de ejecución y nivel de riesgo aquí;
2. incluye un ejemplo seguro o de plan;
3. conserva `--help` como referencia exacta de argumentos;
4. enlaza una guía especializada si el flujo necesita más detalle;
5. actualiza o agrega pruebas del script.
