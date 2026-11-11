# `spanel_static` — SPanel static sites

English | [Español](README.es.md)

CLI to create an **HTML + CSS + JS** site in an nginx container, with its own
volume, distinguishable labels and HTTPS publishing through Traefik.

It is the first stage before integrating it into the `hosting` plugin: it does
not touch the database or the API. The route and the certificate are delegated
to `scripts/proxy_domain.py`, which is already the authority for the file
provider and the lab CA.

## Files

```text
scripts/spanel-static.py          CLI entry point
scripts/spanel_static/
  cli.py                          argparse and dispatch
  config.py                       .env, local/SSH transport
  docker.py                       bounded Docker commands
  names.py                        slug, domain, names and labels
  content.py                      initial content writing
  provision.py                    create/list/status/delete/doctor
  templates/                      index.html, styles.css, app.js
scripts/tests/test_spanel_static.py
```

## Usage in the lab

The CLI is meant to run **on the Docker host** (where the lab lives), not
inside the app container.

```bash
# 1. lab up
./scripts/traefik-lab.sh up

# 2. create the site: container + volume + content + HTTPS route
python3 scripts/spanel-static.py create --name paginadelucas --domain paginadelucas.spanel

# 3. check status and certificate
python3 scripts/spanel-static.py status --domain paginadelucas.spanel

# 4. list
python3 scripts/spanel-static.py list

# 5. delete (container + volume + route)
python3 scripts/spanel-static.py delete --name paginadelucas
```

`--name` produces the slug; `--domain` is optional in the internal profile and
defaults to `<slug>.spanel`.

### From another machine over SSH

The package must also exist on the Docker host. Then:

```bash
python3 scripts/spanel-static.py --transport ssh \
  --remote-repo /home/lucas/mailadmin \
  create --name paginadelucas
```

## What it creates

| Resource | Name |
|---|---|
| Container | `spanel-<slug>-web` |
| Volume | `spanel-<slug>-web` |
| Traefik route | `scripts/traefik-lab/dynamic/spanel-<slug>-web.yml` |
| Content | `index.html`, `styles.css`, `app.js` in `/usr/share/nginx/html` |

Ownership labels:

```text
com.spanel.static=true
com.spanel.static.domain=<domain>
spanel.site=<slug>
spanel.kind=static
spanel.managed=true
```

Deletion only acts when `com.spanel.static=true` and `spanel.kind=static`. It
never touches foreign containers.

## HTTPS

`create` ends by calling:

```bash
python3 scripts/proxy_domain.py --profile internal add \
  --site spanel-<slug>-web --domain <domain> --wait 60
```

That step connects the container to `spanel-traefik-lab`, writes the YAML,
updates the lab DNS and waits for the `internal` certificate (step-ca). The
result is a real certificate from the local CA, not publicly trusted: the root
at `scripts/traefik-lab/ca/root_ca.crt` must be trusted once.

Verification from the host:

```bash
curl --cacert scripts/traefik-lab/ca/root_ca.crt https://paginadelucas.spanel/
```

If the proxy publishes HTTPS on a port other than 443, the CLI detects it with
`docker port spanel-traefik-lab 443` and shows it in the output.

## Variables

| Variable | Default | Use |
|---|---|---|
| `SPANEL_STATIC_PROFILE` | `internal` | `internal` (local CA) or `public` (Let's Encrypt) |
| `SPANEL_STATIC_NETWORK` | `spanel-traefik-lab` / `proxy` | proxy network |
| `SPANEL_STATIC_IMAGE` | `nginx:alpine` | container image |
| `SPANEL_STATIC_WAIT` | `60` | seconds to wait for the certificate |
| `SPANEL_STATIC_REMOTE_REPO` | — | repo path on the Docker host (SSH transport) |
| `SPANEL_DOCKER_SSH_*` | — | SSH transport |
| `SPANEL_PROXY_*` | — | forwarded to `proxy_domain.py` |

## Tests

```bash
python3 -m pytest scripts/tests/test_spanel_static.py -q
```

The tests do not need Docker: they validate names, domains, labels, argv and
templates.

## Next step

Once the cycle is validated, the `provision.py`/`docker.py` logic moves to
`plugins/hosting/backend/static/` and `kind` is added to `hosting_sites`,
without changing the CLI contract.
