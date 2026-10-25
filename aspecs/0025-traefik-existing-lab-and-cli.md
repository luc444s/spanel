# A.SPEC 0025 — Traefik existente: laboratorio seguro y CLI de dominios

> `risk: high` — escribe configuración dinámica del proxy de producción y
> puede emitir certificados públicos. La primera entrega es deliberadamente
> CLI y sin UI para poder probar cada paso.

## WHY

En el servidor ya hay un Traefik v3.7.8 administrando servicios reales en
`/opt/stacks/traefik/`. Agregar un dominio hoy exige editar YAML a mano,
acertar el resolver de Let's Encrypt y no romper las rutas existentes. La
operación debe automatizarse sin crear un segundo Traefik, sin reiniciar
contenedores y sin tocar archivos ajenos.

Antes de integrar nada en la base de datos o en la UI, hace falta una forma
segura y reversible de probar: un Traefik de laboratorio y un CLI que haga
exactamente lo mismo contra el Traefik real.

## WHAT

Dos entregables, en este orden:

1. `scripts/traefik-lab.sh`: levanta un Traefik de laboratorio aislado y
   adopta (solo verifica) el Traefik real. Nunca destruye recursos ajenos.
2. `scripts/proxy_domain.py`: CLI para diagnosticar, listar, agregar, consultar
   y eliminar dominios gestionados por Spanel escribiendo YAML dinámico.

La lógica del CLI se escribe como módulo importable desde el primer día, para
que A.SPEC 0027 la reutilice en el plugin `proxy` sin duplicarla.

## CONFIGURACIÓN

Variables del CLI (con defaults seguros):

```text
SPANEL_PROXY_SSH_HOST          fallback: SPANEL_DOCKER_SSH_HOST
SPANEL_PROXY_SSH_USER          fallback: SPANEL_DOCKER_SSH_USER
SPANEL_PROXY_SSH_PORT          fallback: SPANEL_DOCKER_SSH_PORT (22)
SPANEL_PROXY_SSH_PASSWORD      fallback: SPANEL_DOCKER_SSH_PASSWORD
SPANEL_PROXY_TRAEFIK_CONTAINER traefik
SPANEL_PROXY_CONFIG_DIR        /opt/stacks/traefik/config
SPANEL_PROXY_NETWORK           proxy
SPANEL_PROXY_ENTRYPOINT_HTTP   web
SPANEL_PROXY_ENTRYPOINT_HTTPS  websecure
SPANEL_PROXY_CERT_RESOLVER     letsencrypt
SPANEL_PROXY_STAGING_RESOLVER  letsencrypt-staging
SPANEL_PROXY_PUBLIC_IP         obligatorio para verificar DNS
SPANEL_PROXY_ACME_EMAIL        obligatorio solo en el laboratorio
```

`doctor` lee el `traefik.yml` real y falla con un mensaje claro si los
entrypoints o resolvers configurados no coinciden. Nada de nombres
hardcodeados más allá de los defaults.

## SCRIPT DE LABORATORIO

```bash
./scripts/traefik-lab.sh adopt     # solo inspecciona el Traefik real
./scripts/traefik-lab.sh plan      # imprime qué haría, sin cambios
./scripts/traefik-lab.sh up        # laboratorio en 9080/9443/9081
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down      # solo recursos propios
```

Reglas:

- `set -euo pipefail` y verificación previa de `docker`.
- Nombres propios: contenedor `spanel-traefik-lab`, red
  `spanel-traefik-lab`, directorios `scripts/traefik-lab/`.
- Todo recurso creado lleva `com.spanel.lab=true`.
- Si un puerto está ocupado, **no** arranca: explica cuál y sale con error.
- Si el contenedor ya existe, lo reutiliza; no lo recrea.
- `down` solo elimina recursos con el label del laboratorio. `down --purge`
  elimina además los datos del laboratorio, con confirmación explícita.
- Prohibido: `docker rm`, `docker stop`, `docker system prune`,
  `docker compose down -v` sobre recursos ajenos, o tocar `/opt/stacks`.
- ACME del laboratorio en staging por defecto; sin TLS público si no hay DNS.
- `adopt` no modifica nada: informa contenedor, `StartedAt`, entrypoints,
  resolvers, red, directorio dinámico y cantidad de archivos existentes.

## CLI

```bash
python3 scripts/proxy_domain.py doctor
python3 scripts/proxy_domain.py list
python3 scripts/proxy_domain.py add --site spanel-lucas-wp --domain lucas.spanel1... --www --dry-run
python3 scripts/proxy_domain.py add --site spanel-lucas-wp --domain lucas.spanel1... --set-wordpress-url
python3 scripts/proxy_domain.py status --domain lucas.spanel1...
python3 scripts/proxy_domain.py remove --domain lucas.spanel1...
```

Reglas:

- `--dry-run` imprime el YAML, el destino y los comandos remotos sin ejecutar
  escrituras ni cambios en WordPress.
- Códigos de salida estables: `0` éxito, `2` validación, `3` DNS, `4`
  transporte/Traefik, `5` certificado, `6` WordPress.
- Toda escritura es atómica: archivo temporal en el mismo directorio, validación
  YAML y `mv` final. Permisos `0644`, propietario root.
- Backup previo en `backups/<stamp>/` del archivo del sitio.
- Un archivo por sitio: `spanel-<slug>.yml`, con `# managed-by: spanel`.
- Solo se eliminan archivos cuyo nombre empiece con `spanel-` y cuyo contenido
  tenga el marcador.
- No se reinicia Traefik ni los sitios.
- La conexión a la red `proxy` es idempotente y no reinicia el contenedor.
- El estado del certificado se obtiene por TLS (`openssl s_client` o `ssl` de
  Python), nunca leyendo `acme.json`.
- La actualización de WordPress usa `wordpress:cli` con `--volumes-from`; no
  pasa contraseñas por `argv` ni por logs.
- Log de auditoría local en `logs/proxy-domain.log` con actor, sitio, FQDN,
  acción y resultado; sin secretos.

## YAML GENERADO

```yaml
# managed-by: spanel
http:
  routers:
    spanel-lucas-http:
      rule: Host(`lucas.spanel1...`)
      entryPoints: [web]
      middlewares: [spanel-lucas-redirect]
      service: spanel-lucas
      priority: 200
    spanel-lucas:
      rule: Host(`lucas.spanel1...`)
      entryPoints: [websecure]
      service: spanel-lucas
      tls:
        certResolver: letsencrypt
      priority: 100
  middlewares:
    spanel-lucas-redirect:
      redirectScheme:
        scheme: https
        permanent: true
  services:
    spanel-lucas:
      loadBalancer:
        servers:
          - url: http://spanel-lucas-wp:80
```

Los nombres de routers, middleware y service son únicos por sitio. La regla
incluye `www.` solo si se pidió y el DNS de `www` existe.

La `priority` explícita desambigua el caso de sitios que todavía tienen los
labels de Traefik que agrega Hosting al provisionar: el router HTTP por label
no redirige a HTTPS. El router de Spanel para `web` usa prioridad `200` y el de
`websecure` `100`, de modo que la redirección gane sin recrear el contenedor.
Para sitios nuevos, una vez activo `proxy`, se recomienda provisionar con
`SPANEL_HOSTING_TRAEFIK=false`; los sitios existentes conservan sus labels
hasta una recreación planificada.

## VALIDACIONES

- FQDN: minúsculas, sin esquema, sin puerto, sin `/`, sin `*`, con al menos un
  punto y TLD válido.
- El sitio existe, es un contenedor WordPress gestionado y pertenece al
  tenant cuando la operación venga de la API futura.
- El FQDN no está en otro archivo gestionado ni en la base.
- DNS: A/AAAA resuelve a `SPANEL_PROXY_PUBLIC_IP`. Si no, estado
  `pending_dns` y no se pide certificado.
- El YAML se valida con un parser antes de escribirlo.
- Lock por sitio para evitar escrituras concurrentes.

## INVARIANTES

```yaml
invariants:
  - id: I1
    statement: Ningún comando del script o del CLI destruye recursos ajenos.
    proof: Solo actúa sobre nombres con prefijo spanel- y labels propios; prohibido rm/stop/prune globales.
  - id: I2
    statement: No se toca ninguna ruta YAML existente fuera de spanel-*.yml.
    proof: El escritor filtra por prefijo y marcador; el borrado revalida ambos.
  - id: I3
    statement: Agregar o quitar un dominio no reinicia Traefik ni los sitios.
    proof: File provider con watch; verificar StartedAt antes y después en la prueba live.
  - id: I4
    statement: La operación es idempotente.
    proof: Agregar el mismo dominio dos veces produce el mismo archivo; la red ya conectada no falla.
  - id: I5
    statement: Ningún secreto se escribe en logs, argv ni auditoría.
    proof: WordPress vía wp-cli con env; logs solo metadatos.
  - id: I6
    statement: Un YAML inválido nunca llega al directorio dinámico.
    proof: Parseo previo y escritura atómica con rename.
```

## VERIFICACIÓN

Esta entrega implementa el **entregable 1** (`scripts/traefik-lab.sh`). El CLI
del entregable 2 se implementa en una ejecución separada.

Comandos obligatorios:

```bash
bash -n scripts/traefik-lab.sh
./scripts/traefik-lab.sh --help
./scripts/traefik-lab.sh plan
```

Si el entorno tiene Docker disponible, además:

```bash
./scripts/traefik-lab.sh up
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down
```

- `shellcheck scripts/traefik-lab.sh` si está instalado; si no, reportarlo.
- `adopt` contra el servidor real: sin cambios, solo lectura.
- La prueba con certificado real se define en A.SPEC 0026.
- Las pruebas del CLI (Ruff, unitarios de YAML/DNS/idempotencia) quedan para la
  entrega 2.

## CHANGE SURFACE

```yaml
change_surface:
  allowed:
    - scripts/traefik-lab.sh
    - scripts/traefik-lab/README.md
    - scripts/traefik-lab/.gitignore
  prohibited:
    - vendor/**
    - apps/**
    - plugins/**
    - aspecs/**
    - .env
    - .env.docker
```

## BLAST RADIUS

```yaml
blast_radius:
  direct:
    - docker.local.lab
  indirect:
    - proxy.lab.config
  must_not_affect:
    - traefik.production
    - traefik.existing.routes
    - containers.other
    - dns
```


## FUERA DE ALCANCE

- UI, base de datos y plugin de la aplicación (A.SPEC 0027).
- Crear o actualizar Traefik en producción.
- DNS automático, wildcards, DNS-01 o certificados manuales.
- Modificar rutas existentes administradas por labels Docker.
- Migrar `siteurl` de todos los sitios o hacer search-replace de contenido.

## ROLLBACK

En laboratorio: `traefik-lab.sh down`. En servidor: restaurar el backup del
YAML del sitio, o borrar el archivo `spanel-<slug>.yml`, y opcionalmente
revertir `home`/`siteurl` con el valor anterior. Traefik no requiere reinicio.
