# Laboratorio Traefik (`traefik-lab.sh`)

Traefik aislado para probar configuración dinámica **sin tocar** el Traefik real
del servidor (`traefik`, `/opt/stacks/traefik/`).

- Contenedor: `spanel-traefik-lab`
- Red: `spanel-traefik-lab`
- Label de todos los recursos: `com.spanel.lab=true`
- Datos: `scripts/traefik-lab/` (config estática, `dynamic/`, `data/`)

## Requisitos

- Bash y Docker con daemon accesible.
- Puertos locales libres: `9080`, `9443`, `9081`.
- `plan` no necesita Docker y no modifica nada.

## Uso

```bash
./scripts/traefik-lab.sh --help
./scripts/traefik-lab.sh adopt     # inspecciona el Traefik real (solo lectura)
./scripts/traefik-lab.sh plan      # muestra qué haría, sin cambios
./scripts/traefik-lab.sh up        # arranca el laboratorio
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down      # elimina solo recursos del laboratorio
./scripts/traefik-lab.sh down --purge   # además borra los datos (confirma)
```

### Puertos

| Host (127.0.0.1) | Contenedor | Uso |
| ---------------- | ---------- | --- |
| 9080 | 80 | HTTP (`web`) |
| 9443 | 443 | HTTPS (`websecure`) |
| 9081 | 8080 | dashboard/API (insecure, solo lab) |

Los puertos se publican **solo en 127.0.0.1**.

### ACME staging

Sin `SPANEL_PROXY_ACME_EMAIL` no se configura ningún resolver. Con email:

```bash
SPANEL_PROXY_ACME_EMAIL=ops@example.com ./scripts/traefik-lab.sh up
```

Se habilita `letsencrypt-staging` (CA de staging, no emite certificados
públicos válidos) con HTTP-01 en el entrypoint `web`. El `acme.json` vive en
`scripts/traefik-lab/data/` con permisos `600`.

## Configuración estática

`up` genera `scripts/traefik-lab/traefik.yml` si no existe (no lo sobrescribe):

- entrypoints `web` (:80) y `websecure` (:443)
- dashboard/API insecure en :8080 (publicado en 9081)
- provider **file** sobre `/etc/traefik/dynamic` con `watch: true`
- sin provider Docker y sin montar el socket de Docker

Para probar rutas, dejá archivos `*.yml` en `scripts/traefik-lab/dynamic/`;
Traefik los recarga solo. No hace falta reiniciar el contenedor.

## Seguridad

- `up` valida que un contenedor/red existente con el mismo nombre tenga el
  label `com.spanel.lab=true`; si no, aborta sin tocarlo.
- Si `9080`, `9443` o `9081` están ocupados, `up` no arranca e indica cuál.
- Si el contenedor ya existe, se reutiliza; no se recrea.
- `down` solo elimina el contenedor y la red propios (verificando el label).
- `down --purge` borra únicamente `traefik.yml`, `dynamic/` y `data/` del lab,
  pidiendo confirmación (o `--yes` en automatización).
- Nunca se ejecuta `docker system prune`, ni se tocan `/opt/stacks` ni
  contenedores ajenos.

## Variables

| Variable | Default | Uso |
| -------- | ------- | --- |
| `SPANEL_PROXY_ACME_EMAIL` | vacío | habilita ACME staging |
| `LAB_IMAGE` | `traefik:v3.7.8` | imagen del lab |
| `LAB_HTTP_PORT` | `9080` | puerto host HTTP |
| `LAB_HTTPS_PORT` | `9443` | puerto host HTTPS |
| `LAB_DASHBOARD_PORT` | `9081` | puerto host dashboard |
| `SPANEL_PROXY_TRAEFIK_CONTAINER` | `traefik` | contenedor real (solo `adopt`) |
| `SPANEL_PROXY_CONFIG_DIR` | `/opt/stacks/traefik/config` | config real (solo `adopt`) |

## `adopt`

Solo lectura sobre el contenedor real (`traefik` por defecto). Informa estado,
`StartedAt`, imagen, puertos, red, mounts, directorio de configuración dinámica,
cantidad de YAML y, si puede leerlo, entrypoints/resolvers del `traefik.yml`.
No crea, reinicia ni modifica nada.

## Rollback

```bash
./scripts/traefik-lab.sh down          # conserva datos para inspección
./scripts/traefik-lab.sh down --purge  # elimina también datos del lab
```

El Traefik real y sus rutas no se ven afectados por ninguno de los dos.
