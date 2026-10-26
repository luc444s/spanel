# `spanel_static` — sitios estáticos de SPanel

CLI para crear un sitio **HTML + CSS + JS** en un contenedor nginx, con volumen
propio, etiquetas distinguibles y publicación HTTPS por Traefik.

Es la primera etapa antes de integrarlo al plugin `hosting`: no toca la base de
datos ni la API. La ruta y el certificado se delegan en `scripts/proxy_domain.py`,
que ya es la autoridad del file provider y la CA del laboratorio.

## Archivos

```text
scripts/spanel-static.py          entrada del CLI
scripts/spanel_static/
  cli.py                          argparse y despacho
  config.py                       .env, transporte local/SSH
  docker.py                       comandos Docker acotados
  names.py                        slug, dominio, nombres y labels
  content.py                      escritura del contenido inicial
  provision.py                    create/list/status/delete/doctor
  templates/                      index.html, styles.css, app.js
scripts/tests/test_spanel_static.py
```

## Uso en el laboratorio

El CLI está pensado para correr **en el host Docker** (donde vive el
laboratorio), no dentro del contenedor de la app.

```bash
# 1. laboratorio arriba
./scripts/traefik-lab.sh up

# 2. crear el sitio: contenedor + volumen + contenido + ruta HTTPS
python3 scripts/spanel-static.py create --name paginadelucas --domain paginadelucas.spanel

# 3. ver estado y certificado
python3 scripts/spanel-static.py status --domain paginadelucas.spanel

# 4. listar
python3 scripts/spanel-static.py list

# 5. borrar (contenedor + volumen + ruta)
python3 scripts/spanel-static.py delete --name paginadelucas
```

`--name` produce el slug; `--domain` es opcional en el perfil interno y por
defecto usa `<slug>.spanel`.

### Desde otra máquina por SSH

El paquete debe existir también en el host Docker. Luego:

```bash
python3 scripts/spanel-static.py --transport ssh \
  --remote-repo /home/lucas/mailadmin \
  create --name paginadelucas
```

## Qué crea

| Recurso | Nombre |
|---|---|
| Contenedor | `spanel-<slug>-web` |
| Volumen | `spanel-<slug>-web` |
| Ruta Traefik | `scripts/traefik-lab/dynamic/spanel-<slug>-web.yml` |
| Contenido | `index.html`, `styles.css`, `app.js` en `/usr/share/nginx/html` |

Etiquetas de propiedad:

```text
com.spanel.static=true
com.spanel.static.domain=<dominio>
spanel.site=<slug>
spanel.kind=static
spanel.managed=true
```

El borrado solo actúa si `com.spanel.static=true` y `spanel.kind=static`.
Nunca toca contenedores ajenos.

## HTTPS

`create` termina llamando a:

```bash
python3 scripts/proxy_domain.py --profile internal add \
  --site spanel-<slug>-web --domain <dominio> --wait 60
```

Ese paso conecta el contenedor a `spanel-traefik-lab`, escribe el YAML, actualiza
el DNS del laboratorio y espera el certificado `internal` (step-ca). El resultado
es un certificado real de la CA local, no de confianza pública: hay que confiar
en `scripts/traefik-lab/ca/root_ca.crt` una vez.

Verificación desde el host:

```bash
curl --cacert scripts/traefik-lab/ca/root_ca.crt https://paginadelucas.spanel/
```

Si el proxy publica HTTPS en un puerto distinto de 443, el CLI lo detecta con
`docker port spanel-traefik-lab 443` y lo muestra en la salida.

## Variables

| Variable | Default | Uso |
|---|---|---|
| `SPANEL_STATIC_PROFILE` | `internal` | `internal` (CA local) o `public` (Let's Encrypt) |
| `SPANEL_STATIC_NETWORK` | `spanel-traefik-lab` / `proxy` | red del proxy |
| `SPANEL_STATIC_IMAGE` | `nginx:alpine` | imagen del contenedor |
| `SPANEL_STATIC_WAIT` | `60` | segundos de espera al certificado |
| `SPANEL_STATIC_REMOTE_REPO` | — | ruta del repo en el host Docker (transporte SSH) |
| `SPANEL_DOCKER_SSH_*` | — | transporte SSH |
| `SPANEL_PROXY_*` | — | se reenvían a `proxy_domain.py` |

## Pruebas

```bash
python3 -m pytest scripts/tests/test_spanel_static.py -q
```

Las pruebas no necesitan Docker: validan nombres, dominios, etiquetas, argv y
plantillas.

## Siguiente paso

Cuando el ciclo esté validado, la lógica de `provision.py`/`docker.py` se mueve
a `plugins/hosting/backend/static/` y se agrega `kind` a `hosting_sites`, sin
cambiar el contrato del CLI.
