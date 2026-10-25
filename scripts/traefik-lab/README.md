# Laboratorio Traefik + CA ACME local (`traefik-lab.sh`)

Laboratorio aislado para probar dominios con **HTTPS real** sin tocar el
Traefik del servidor (`traefik`, `/opt/stacks/traefik/`) y sin depender de un
dominio público.

- `spanel-traefik-lab`: Traefik v3.7.8 (proxy)
- `spanel-step-ca`: CA ACME local (`smallstep/step-ca`)
- `spanel-lab-dns`: DNS interno (`dnsmasq`)
- Red: `spanel-traefik-lab`
- Volumen de la CA: `spanel-step-ca-data`
- Label de todos los recursos: `com.spanel.lab=true`

## Requisitos

- Bash y Docker con daemon accesible.
- Puertos locales libres: `9080`, `9081`, `443` o `9443`, y `5353` si se
  publica el DNS.
- `plan` no necesita Docker y no modifica nada.
- `openssl` (para mostrar la huella de la CA).

## Uso

```bash
./scripts/traefik-lab.sh --help
./scripts/traefik-lab.sh adopt     # inspecciona el Traefik real (solo lectura)
./scripts/traefik-lab.sh plan      # muestra qué haría, sin cambios
./scripts/traefik-lab.sh up        # arranca Traefik + CA + DNS
./scripts/traefik-lab.sh status
./scripts/traefik-lab.sh down      # elimina solo recursos del laboratorio
./scripts/traefik-lab.sh down --purge   # además borra datos, CA y volumen
```

## Cómo se emiten los certificados

Traefik incluye un resolver `internal` que apunta a la CA local:

```yaml
certificatesResolvers:
  internal:
    acme:
      caServer: https://spanel-step-ca:9000/acme/acme/directory
      caCertificates: [/lab-ca/root_ca.crt]
      storage: /etc/traefik/data/acme-internal.json
      tlsChallenge: {}
```

El flujo es ACME real (TLS-ALPN-01): Traefik pide el certificado a `step-ca`,
`step-ca` valida el nombre con el DNS interno y emite un X.509 firmado por la
CA del laboratorio. El resultado es un certificado **criptográficamente real**
pero **no de confianza pública**: hay que instalar la raíz local una vez.

```bash
# confiar en la CA (Linux)
sudo cp scripts/traefik-lab/ca/root_ca.crt /usr/local/share/ca-certificates/spanel-lab-ca.crt
sudo update-ca-certificates
```

La clave privada de la CA vive en el volumen `spanel-step-ca-data` y nunca se
copia al repositorio. Solo se exporta `ca/root_ca.crt` (público).

## Agregar un dominio

```bash
# 1. laboratorio arriba
./scripts/traefik-lab.sh up

# 2. sitio WordPress de prueba conectado automáticamente a la red del lab
python3 scripts/proxy_domain.py add --site spanel-lucas-test-wp --domain lucas.spanel

# opcional: abrir el link al terminar
python3 scripts/proxy_domain.py add --site spanel-lucas-test-wp --domain lucas.spanel --open

# 3. resolver el nombre en el host (una vez)
echo "127.0.0.1 lucas.spanel" | sudo tee -a /etc/hosts

# 4. abrir el link que imprimió el CLI
```

`add` imprime la URL real (puerto incluido si no es 443), la línea de
`/etc/hosts`, el comando `curl` verificado y el recordatorio de confiar en la
CA. Con el puerto 443 libre, el link es limpio: `https://lucas.spanel`.

`--set-wordpress-url` actualiza `home` y `siteurl` con un `wordpress:cli`
efímero que hereda el entorno del sitio por un archivo temporal `0600`, así
funciona también cuando `wp-config.php` toma la BD de variables de entorno.

## Puertos

`up` decide el puerto HTTPS automáticamente:

| Host (127.0.0.1) | Contenedor | Uso |
| ---------------- | ---------- | --- |
| 443 o 9443 | 443 | HTTPS (`websecure`) |
| 9080 | 80 | HTTP (`web`) |
| 9081 | 8080 | dashboard/API (insecure, solo lab) |
| 5353 (opt-in) | 53/udp | DNS interno |

Se usa `443` si está libre; si no, `9443`. Se puede forzar con
`LAB_HTTPS_PORT=9443`. Los puertos se publican **solo en 127.0.0.1**.

## Configuración estática

`up` genera `scripts/traefik-lab/traefik.yml` si no existe. Si ya existe y fue
generado por este script, lo actualiza para agregar el resolver `internal`; si
no fue generado por el script, aborta sin sobrescribirlo.

- entrypoints `web` (:80) y `websecure` (:443)
- provider **file** sobre `/etc/traefik/dynamic` con `watch: true`
- resolver `internal` (CA local) y, opcionalmente, `letsencrypt-staging`
- sin provider Docker y sin montar el socket de Docker

## Seguridad

- `up` valida que un contenedor/red existente con el mismo nombre tenga el
  label `com.spanel.lab=true`; si no, aborta sin tocarlo.
- Si un puerto está ocupado, `up` no arranca e indica cuál.
- Los contenedores del lab se reutilizan; solo se recrean si son de una versión
  anterior (label `com.spanel.lab.version`).
- `down` solo elimina contenedores y red propios (verificando el label).
- `down --purge` borra únicamente `traefik.yml`, `dynamic/`, `data/`, `dns/`,
  `ca/` y el volumen `spanel-step-ca-data`, pidiendo confirmación.
- Nunca se ejecuta `docker system prune`, ni se tocan `/opt/stacks` ni
  contenedores ajenos.
- Nada del laboratorio sale a Internet.

## Variables

| Variable | Default | Uso |
| -------- | ------- | --- |
| `LAB_IMAGE` | `traefik:v3.7.8` | imagen del proxy |
| `LAB_CA_IMAGE` | `smallstep/step-ca:0.30.2` | imagen de la CA |
| `LAB_DNS_IMAGE` | `4km3/dnsmasq:2.90-r3` | imagen del DNS |
| `LAB_HTTP_PORT` | `9080` | puerto host HTTP |
| `LAB_HTTPS_PORT` | `auto` | `auto`, `443` o `9443` |
| `LAB_DASHBOARD_PORT` | `9081` | puerto host dashboard |
| `LAB_DNS_PORT` | `5353` | puerto DNS (si se publica) |
| `LAB_DNS_PUBLISH` | `0` | `1` publica el DNS en 127.0.0.1 |
| `SPANEL_PROXY_ACME_EMAIL` | vacío | habilita además `letsencrypt-staging` |
| `SPANEL_PROXY_TRAEFIK_CONTAINER` | `traefik` | contenedor real (solo `adopt`) |
| `SPANEL_PROXY_CONFIG_DIR` | `/opt/stacks/traefik/config` | config real (solo `adopt`) |

## `adopt`

Solo lectura sobre el contenedor real (`traefik` por defecto). Informa estado,
`StartedAt`, imagen, puertos, red, mounts, directorio de configuración dinámica,
cantidad de YAML y, si puede leerlo, entrypoints/resolvers del `traefik.yml`.
No crea, reinicia ni modifica nada.

## Rollback

```bash
./scripts/traefik-lab.sh down          # conserva datos y CA para inspección
./scripts/traefik-lab.sh down --purge  # elimina también datos, CA y volumen
```

El Traefik real y sus rutas no se ven afectados por ninguno de los dos.
