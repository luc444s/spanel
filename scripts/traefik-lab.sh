#!/usr/bin/env bash
# Laboratorio aislado de Traefik + CA ACME local para Spanel.
#
# Levanta un Traefik propio, una CA ACME (step-ca) y un DNS interno, con
# prefijo `spanel-` y label `com.spanel.lab=true`. Nada de esto toca el Traefik
# real ni /opt/stacks. `adopt` solo inspecciona el Traefik existente, en modo
# lectura.
#
# Uso:
#   ./scripts/traefik-lab.sh --help
#   ./scripts/traefik-lab.sh adopt
#   ./scripts/traefik-lab.sh plan
#   ./scripts/traefik-lab.sh up
#   ./scripts/traefik-lab.sh status
#   ./scripts/traefik-lab.sh down
#   ./scripts/traefik-lab.sh down --purge
#
# Variables:
#   LAB_IMAGE                imagen de Traefik (default: traefik:v3.7.8)
#   LAB_CA_IMAGE             imagen de la CA (default: smallstep/step-ca:0.30.2)
#   LAB_DNS_IMAGE            imagen de DNS (default: 4km3/dnsmasq:2.90-r3)
#   LAB_HTTP_PORT            default 9080 (host -> 80)
#   LAB_HTTPS_PORT           default auto (443 si está libre, si no 9443)
#   LAB_DASHBOARD_PORT       default 9081 (host -> 8080)
#   LAB_DNS_PORT             default 5353 (DNS interno publicado en 127.0.0.1)
#   LAB_DNS_PUBLISH          default 0; con 1 publica el DNS en 127.0.0.1
#   SPANEL_PROXY_ACME_EMAIL  email para ACME staging de Let's Encrypt (opcional)
#   SPANEL_PROXY_TRAEFIK_CONTAINER  contenedor real a inspeccionar (default traefik)
#   SPANEL_PROXY_CONFIG_DIR  directorio dinámico del Traefik real
#                            (default /opt/stacks/traefik/config)
#
# Seguridad:
#   - Solo crea/elimina recursos con el label com.spanel.lab=true.
#   - Nunca ejecuta docker system prune, ni toca /opt/stacks desde up/down.
#   - `down` sin --purge conserva los datos del laboratorio (incluida la CA).
#   - La clave privada de la CA vive en el volumen spanel-step-ca-data y nunca
#     se copia al repositorio; solo se exporta el certificado raíz público.
set -euo pipefail

MODE=""
PURGE=0
ASSUME_YES=0

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LAB_ROOT="${SCRIPT_DIR}/traefik-lab"
LAB_CONFIG="${LAB_ROOT}/traefik.yml"
LAB_DYNAMIC="${LAB_ROOT}/dynamic"
LAB_DATA="${LAB_ROOT}/data"
LAB_ACME="${LAB_DATA}/acme.json"
LAB_CA_DIR="${LAB_ROOT}/ca"
LAB_DNS_DIR="${LAB_ROOT}/dns"
LAB_DNS_HOSTS="${LAB_DNS_DIR}/hosts"
LAB_CA_ROOT="${LAB_CA_DIR}/root_ca.crt"
LAB_CA_PASSWORD="${LAB_CA_DIR}/password"

LAB_NAME="spanel-traefik-lab"
LAB_CA_NAME="spanel-step-ca"
LAB_DNS_NAME="spanel-lab-dns"
LAB_NETWORK="spanel-traefik-lab"
LAB_CA_VOLUME="spanel-step-ca-data"
LAB_LABEL="com.spanel.lab=true"
LAB_LABEL_ROLE="com.spanel.lab.role"
LAB_VERSION="2"

LAB_IMAGE="${LAB_IMAGE:-traefik:v3.7.8}"
LAB_CA_IMAGE="${LAB_CA_IMAGE:-smallstep/step-ca:0.30.2}"
LAB_DNS_IMAGE="${LAB_DNS_IMAGE:-4km3/dnsmasq:2.90-r3}"
LAB_HTTP_PORT="${LAB_HTTP_PORT:-9080}"
LAB_HTTPS_PORT="${LAB_HTTPS_PORT:-auto}"
LAB_DASHBOARD_PORT="${LAB_DASHBOARD_PORT:-9081}"
LAB_DNS_PORT="${LAB_DNS_PORT:-5353}"
LAB_DNS_PUBLISH="${LAB_DNS_PUBLISH:-0}"

REAL_CONTAINER="${SPANEL_PROXY_TRAEFIK_CONTAINER:-traefik}"
REAL_CONFIG_DIR="${SPANEL_PROXY_CONFIG_DIR:-/opt/stacks/traefik/config}"
ACME_EMAIL="${SPANEL_PROXY_ACME_EMAIL:-}"

HTTPS_PORT=""
DNS_IP=""
TRAEFIK_IP=""

usage() {
  cat <<'EOF'
Isolated Traefik lab + local ACME CA for Spanel.

Modos:
  adopt             Inspect the real Traefik (read-only). Modifies nothing.
  plan              Print containers, network, ports, config and commands.
                    Does not need Docker and mutates nothing.
  up                Create/reuse the lab and its local CA.
  status            Show the lab and CA status.
  down              Delete ONLY resources with com.spanel.lab=true.
  down --purge      Also delete the data and the CA volume (asks for confirmation).

Opciones:
  --purge           Only with `down`: delete data, CA and its own volume.
  --yes             Do not ask on `down --purge`.
  -h, --help        Show this help.

Entorno:
  LAB_IMAGE                Default: traefik:v3.7.8
  LAB_CA_IMAGE             Default: smallstep/step-ca:0.30.2
  LAB_DNS_IMAGE            Default: 4km3/dnsmasq:2.90-r3
  LAB_HTTP_PORT            Default: 9080   (host -> 80)
  LAB_HTTPS_PORT           Default: auto   (443 if free, otherwise 9443)
  LAB_DASHBOARD_PORT       Default: 9081   (host -> 8080)
  LAB_DNS_PORT             Default: 5353   (only if LAB_DNS_PUBLISH=1)
  LAB_DNS_PUBLISH          Default: 0
  SPANEL_PROXY_ACME_EMAIL  Email for the additional Let's Encrypt ACME staging.

Lab ports (published only on 127.0.0.1):
  <https>  HTTPS -> 443   (443 or 9443, decided by `up`)
  9080     HTTP  -> 80
  9081     dashboard/API

Own resources:
  container   spanel-traefik-lab
  container   spanel-step-ca        (local ACME CA)
  container   spanel-lab-dns        (internal DNS for lab domains)
  network     spanel-traefik-lab
  volume      spanel-step-ca-data
  directory   scripts/traefik-lab/

Traefik internal resolver: `internal` -> step-ca.
Internal domains: `python3 scripts/proxy_domain.py add --domain lucas.spanel`.

docker system prune is never run and /opt/stacks is never touched from up/down.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*" >&2
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "docker is not in PATH"
  docker info >/dev/null 2>&1 || fail "cannot talk to the Docker daemon"
}

validate_email() {
  if [ -n "$ACME_EMAIL" ]; then
    case "$ACME_EMAIL" in
      *[[:space:]]*|*@*@*|@*|*@) fail "SPANEL_PROXY_ACME_EMAIL does not look like a valid email: $ACME_EMAIL" ;;
      *@*) ;;
      *) fail "SPANEL_PROXY_ACME_EMAIL does not look like a valid email: $ACME_EMAIL" ;;
    esac
  fi
}

container_exists() {
  # --type container: el nombre del contenedor coincide con el de la red.
  docker inspect --type container "$LAB_NAME" >/dev/null 2>&1
}

named_container_exists() {
  docker inspect --type container "$1" >/dev/null 2>&1
}

network_exists() {
  docker network inspect "$LAB_NETWORK" >/dev/null 2>&1
}

container_is_lab() {
  [ "$(docker inspect -f '{{index .Config.Labels "com.spanel.lab"}}' "$LAB_NAME" 2>/dev/null || true)" = "true" ]
}

named_container_is_lab() {
  [ "$(docker inspect -f '{{index .Config.Labels "com.spanel.lab"}}' "$1" 2>/dev/null || true)" = "true" ]
}

container_is_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = "true" ]
}

container_ip() {
  local name="$1"
  docker inspect -f "{{(index .NetworkSettings.Networks \"${LAB_NETWORK}\").IPAddress}}" "$name" 2>/dev/null || true
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
    return $?
  fi
  if command -v docker >/dev/null 2>&1; then
    docker ps --format '{{.Ports}}' 2>/dev/null \
      | grep -Eq "(^|[, ])(0\.0\.0\.0|127\.0\.0\.1|\[::\]):${port}->" && return 0
  fi
  return 1
}

resolve_https_port() {
  if [ "$LAB_HTTPS_PORT" != "auto" ]; then
    HTTPS_PORT="$LAB_HTTPS_PORT"
    return 0
  fi
  if port_in_use 443; then
    HTTPS_PORT="9443"
  else
    HTTPS_PORT="443"
  fi
}

check_ports_free() {
  local port
  local used=0
  local ports=("$LAB_HTTP_PORT" "$HTTPS_PORT" "$LAB_DASHBOARD_PORT")
  if [ "$LAB_DNS_PUBLISH" = "1" ]; then
    ports+=("$LAB_DNS_PORT")
  fi
  for port in "${ports[@]}"; do
    if port_in_use "$port"; then
      echo "error: port ${port} is already in use; the lab will not start." >&2
      used=1
    fi
  done
  if [ "$used" = "1" ]; then
    fail "free those ports or choose others with LAB_HTTP_PORT/LAB_HTTPS_PORT/LAB_DASHBOARD_PORT/LAB_DNS_PORT"
  fi
}

preflight_ports() {
  local need_traefik=1
  if container_exists; then
    local version mounts
    version="$(docker inspect -f '{{index .Config.Labels "com.spanel.lab.version"}}' "$LAB_NAME" 2>/dev/null || true)"
    mounts="$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$LAB_NAME" 2>/dev/null || true)"
    if [ "$version" = "$LAB_VERSION" ] && printf '%s' "$mounts" | grep -q '/lab-ca'; then
      need_traefik=0
    fi
  fi
  [ "$need_traefik" = "0" ] || check_ports_free

  if [ "$LAB_DNS_PUBLISH" = "1" ] && ! named_container_exists "$LAB_DNS_NAME"; then
    if port_in_use "$LAB_DNS_PORT"; then
      fail "DNS port ${LAB_DNS_PORT} is already in use; the lab will not start"
    fi
  fi
}

render_static_config() {
  cat <<'EOF'
# managed-by: spanel traefik-lab
# Generated by scripts/traefik-lab.sh. Do not edit by hand: recreate it with the script.
entryPoints:
  web:
    address: ":80"
  websecure:
    address: ":443"

api:
  dashboard: true
  # Solo laboratorio: el puerto 8081 se publica únicamente en 127.0.0.1.
  insecure: true

providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true

log:
  level: INFO
accessLog: {}

certificatesResolvers:
  # CA ACME local del laboratorio. Emite certificados reales para nombres
  # internos (por ejemplo lucas.spanel) sin salir a Internet.
  internal:
    acme:
      caServer: https://spanel-step-ca:9000/acme/acme/directory
      caCertificates:
        - /lab-ca/root_ca.crt
      email: lab@spanel.internal
      storage: /etc/traefik/data/acme-internal.json
      tlsChallenge: {}
EOF

  if [ -n "$ACME_EMAIL" ]; then
    cat <<EOF

  letsencrypt-staging:
    acme:
      email: "${ACME_EMAIL}"
      storage: /etc/traefik/data/acme.json
      caServer: https://acme-staging-v02.api.letsencrypt.org/directory
      httpChallenge:
        entryPoint: web
EOF
  fi
}

config_has_internal_resolver() {
  [ -f "$LAB_CONFIG" ] && grep -q '^[[:space:]]*internal:' "$LAB_CONFIG" 2>/dev/null
}

config_is_ours() {
  [ -f "$LAB_CONFIG" ] && head -n 2 "$LAB_CONFIG" | grep -q 'managed-by: spanel traefik-lab'
}

build_run_args() {
  RUN_ARGS=(
    run -d
    --name "$LAB_NAME"
    --label "$LAB_LABEL"
    --label "${LAB_LABEL_ROLE}=traefik"
    --label "com.spanel.lab.version=${LAB_VERSION}"
    --restart unless-stopped
    --network "$LAB_NETWORK"
    -p "127.0.0.1:${LAB_HTTP_PORT}:80"
    -p "127.0.0.1:${HTTPS_PORT}:443"
    -p "127.0.0.1:${LAB_DASHBOARD_PORT}:8080"
    -v "${LAB_CONFIG}:/etc/traefik/traefik.yml:ro"
    -v "${LAB_DYNAMIC}:/etc/traefik/dynamic:ro"
    -v "${LAB_DATA}:/etc/traefik/data"
    -v "${LAB_CA_DIR}:/lab-ca:ro"
    "$LAB_IMAGE"
  )
}

build_ca_args() {
  CA_RUN_ARGS=(
    run -d
    --name "$LAB_CA_NAME"
    --label "$LAB_LABEL"
    --label "${LAB_LABEL_ROLE}=ca"
    --label "com.spanel.lab.version=${LAB_VERSION}"
    --restart unless-stopped
    --network "$LAB_NETWORK"
    --dns "$DNS_IP"
    -e "DOCKER_STEPCA_INIT_NAME=Spanel Lab CA"
    -e "DOCKER_STEPCA_INIT_DNS_NAMES=spanel-step-ca,localhost,127.0.0.1"
    -e "DOCKER_STEPCA_INIT_ADDRESS=:9000"
    -e "DOCKER_STEPCA_INIT_ACME=true"
    -e "DOCKER_STEPCA_INIT_PASSWORD=${CA_PASSWORD}"
    -v "${LAB_CA_VOLUME}:/home/step"
    "$LAB_CA_IMAGE"
  )
}

build_dns_args() {
  DNS_RUN_ARGS=(
    run -d
    --name "$LAB_DNS_NAME"
    --label "$LAB_LABEL"
    --label "${LAB_LABEL_ROLE}=dns"
    --label "com.spanel.lab.version=${LAB_VERSION}"
    --restart unless-stopped
    --network "$LAB_NETWORK"
    --cap-add=NET_ADMIN
    -v "${LAB_DNS_DIR}:/etc/dnsmasq-hosts:ro"
    "$LAB_DNS_IMAGE"
    --no-resolv
    --no-hosts
    --addn-hosts=/etc/dnsmasq-hosts/hosts
    --log-facility=-
    --port=53
  )
  if [ "$LAB_DNS_PUBLISH" = "1" ]; then
    DNS_RUN_ARGS+=(-p "127.0.0.1:${LAB_DNS_PORT}:53/udp")
  fi
}

print_cmd() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

ensure_dirs() {
  mkdir -p "$LAB_DYNAMIC" "$LAB_DATA" "$LAB_CA_DIR" "$LAB_DNS_DIR"
  if [ ! -f "$LAB_DNS_HOSTS" ]; then
    : > "$LAB_DNS_HOSTS"
  fi
}

ensure_ca_password() {
  if [ ! -f "$LAB_CA_PASSWORD" ]; then
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 24 > "$LAB_CA_PASSWORD"
    else
      head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$LAB_CA_PASSWORD"
    fi
    chmod 600 "$LAB_CA_PASSWORD" 2>/dev/null || true
  fi
  CA_PASSWORD="$(cat "$LAB_CA_PASSWORD")"
}

wait_ca_ready() {
  local i
  for i in $(seq 1 60); do
    if docker exec "$LAB_CA_NAME" test -f /home/step/certs/root_ca.crt >/dev/null 2>&1; then
      return 0
    fi
    if ! container_is_running "$LAB_CA_NAME"; then
      return 1
    fi
    sleep 1
  done
  return 1
}

export_ca_root() {
  if ! docker cp "${LAB_CA_NAME}:/home/step/certs/root_ca.crt" "$LAB_CA_ROOT" >/dev/null 2>&1; then
    fail "could not export the CA root certificate"
  fi
  chmod 644 "$LAB_CA_ROOT" 2>/dev/null || true
}

wait_running() {
  local name="$1"
  local i
  for i in $(seq 1 20); do
    container_is_running "$name" && return 0
    sleep 1
  done
  return 1
}

wait_dashboard() {
  command -v curl >/dev/null 2>&1 || return 0
  local i
  for i in $(seq 1 15); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${LAB_DASHBOARD_PORT}/dashboard/" >/dev/null 2>&1 \
      || curl -fsS --max-time 2 "http://127.0.0.1:${LAB_DASHBOARD_PORT}/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "warning: the dashboard does not respond yet on 127.0.0.1:${LAB_DASHBOARD_PORT}" >&2
  return 1
}

reload_dns() {
  if container_is_running "$LAB_DNS_NAME"; then
    docker kill -s HUP "$LAB_DNS_NAME" >/dev/null 2>&1 || true
  fi
}

point_dns_at_traefik() {
  local ip="$1"
  [ -n "$ip" ] || return 0
  if [ ! -f "$LAB_DNS_HOSTS" ]; then
    : > "$LAB_DNS_HOSTS"
  fi
  local tmp
  tmp="$(mktemp "${LAB_DNS_HOSTS}.XXXXXX")"
  awk -v ip="$ip" '
    /^[[:space:]]*#/ || NF == 0 { print; next }
    { $1 = ip; print }
  ' "$LAB_DNS_HOSTS" > "$tmp"
  chmod 0644 "$tmp"
  mv "$tmp" "$LAB_DNS_HOSTS"
  reload_dns
}

plan() {
  validate_email
  resolve_https_port
  CA_PASSWORD="<generated in scripts/traefik-lab/ca/password>"
  DNS_IP="<ip-of-spanel-lab-dns>"
  build_run_args
  build_ca_args
  build_dns_args

  echo "Traefik lab + local ACME CA plan (executes nothing)"
  echo
  echo "  container:   ${LAB_NAME}  (role=traefik, version=${LAB_VERSION})"
  echo "  container:   ${LAB_CA_NAME}  (role=ca)"
  echo "  container:   ${LAB_DNS_NAME}  (role=dns)"
  echo "  network:     ${LAB_NETWORK}"
  echo "  label:       ${LAB_LABEL}"
  echo "  CA volume:   ${LAB_CA_VOLUME}"
  echo "  image:       ${LAB_IMAGE}"
  echo "  CA image:    ${LAB_CA_IMAGE}"
  echo "  DNS image:   ${LAB_DNS_IMAGE}"
  echo "  config:      ${LAB_CONFIG}"
  echo "  dynamic:     ${LAB_DYNAMIC}"
  echo "  data:        ${LAB_DATA}"
  echo "  public CA:   ${LAB_CA_ROOT}"
  echo
  echo "  ports (127.0.0.1 only):"
  echo "    ${HTTPS_PORT}:443      (HTTPS, automatic choice)"
  echo "    ${LAB_HTTP_PORT}:80       (HTTP)"
  echo "    ${LAB_DASHBOARD_PORT}:8080    (dashboard/API)"
  if [ "$LAB_DNS_PUBLISH" = "1" ]; then
    echo "    ${LAB_DNS_PORT}:53/udp   (internal DNS)"
  fi
  echo
  echo "  internal resolver: internal -> step-ca (TLS-ALPN-01)"
  echo "  CA ACME:          https://spanel-step-ca:9000/acme/acme/directory"
  if [ -n "$ACME_EMAIL" ]; then
    echo "  extra ACME:       letsencrypt-staging for ${ACME_EMAIL}"
  fi
  echo
  echo "Directories and files:"
  print_cmd mkdir -p "$LAB_ROOT" "$LAB_DYNAMIC" "$LAB_DATA" "$LAB_CA_DIR" "$LAB_DNS_DIR"
  echo "  # write static config to $LAB_CONFIG (includes the internal resolver)"
  echo
  echo "Commands:"
  print_cmd docker network create --label "$LAB_LABEL" "$LAB_NETWORK"
  print_cmd docker "${DNS_RUN_ARGS[@]}"
  print_cmd docker "${CA_RUN_ARGS[@]}"
  print_cmd docker cp "${LAB_CA_NAME}:/home/step/certs/root_ca.crt" "$LAB_CA_ROOT"
  print_cmd docker "${RUN_ARGS[@]}"
  echo
  echo "Docker is not checked and ports are not verified in 'plan'; that happens in 'up'."
}

ensure_network() {
  if network_exists; then
    [ "$(docker network inspect -f '{{index .Labels "com.spanel.lab"}}' "$LAB_NETWORK" 2>/dev/null || true)" = "true" ] \
      || fail "a network '${LAB_NETWORK}' exists that is not part of the lab; not touching it"
    info "reusing network ${LAB_NETWORK}"
  else
    info "creating network ${LAB_NETWORK}"
    docker network create --label "$LAB_LABEL" "$LAB_NETWORK" >/dev/null
  fi
}

ensure_dns_container() {
  if named_container_exists "$LAB_DNS_NAME"; then
    named_container_is_lab "$LAB_DNS_NAME" \
      || fail "a container '${LAB_DNS_NAME}' exists that is not part of the lab; not touching it"
    if container_is_running "$LAB_DNS_NAME"; then
      info "container ${LAB_DNS_NAME} is already running; reusing it"
    else
      info "starting container ${LAB_DNS_NAME}"
      docker start "$LAB_DNS_NAME" >/dev/null
      wait_running "$LAB_DNS_NAME" || fail "container ${LAB_DNS_NAME} did not stay running"
    fi
    return 0
  fi

  build_dns_args
  info "creating container ${LAB_DNS_NAME}"
  docker "${DNS_RUN_ARGS[@]}" >/dev/null
  wait_running "$LAB_DNS_NAME" || fail "container ${LAB_DNS_NAME} did not stay running"
}

ensure_ca_container() {
  ensure_ca_password

  if named_container_exists "$LAB_CA_NAME"; then
    named_container_is_lab "$LAB_CA_NAME" \
      || fail "a container '${LAB_CA_NAME}' exists that is not part of the lab; not touching it"
    if container_is_running "$LAB_CA_NAME"; then
      # El DNS del CA queda fijado con --dns en la creación. Si el contenedor
      # de DNS se recreó, obtiene OTRA IP y el CA quedaría resolviendo hacia
      # una IP muerta: ACME daría 'validation target' sin éxito. El volumen
      # de la CA se conserva, así que recrear es barato.
      if [ "$(docker inspect -f '{{json .HostConfig.Dns}}' "$LAB_CA_NAME" 2>/dev/null)" != "[\"$DNS_IP\"]" ]; then
        info "CA ${LAB_CA_NAME} pins --dns to a different IP than the current DNS; recreating it (volume preserved)"
        docker rm -f "$LAB_CA_NAME" >/dev/null
        build_ca_args
        if ! docker "${CA_RUN_ARGS[@]}" >/dev/null; then
          fail "could not recreate ${LAB_CA_NAME} with the current DNS"
        fi
        wait_running "$LAB_CA_NAME" || fail "the recreated CA did not stay running"
      else
        info "container ${LAB_CA_NAME} is already running; reusing it"
      fi
    else
      info "starting container ${LAB_CA_NAME}"
      docker start "$LAB_CA_NAME" >/dev/null
    fi
  else
    build_ca_args
    info "creating container ${LAB_CA_NAME} (first CA initialization)"
    docker "${CA_RUN_ARGS[@]}" >/dev/null
  fi

  if ! wait_ca_ready; then
    docker logs --tail 40 "$LAB_CA_NAME" 2>/dev/null | sed 's/^/  /' >&2 || true
    fail "CA ${LAB_CA_NAME} did not finish initializing"
  fi
  export_ca_root
}

ensure_traefik_config() {
  if [ -f "$LAB_CONFIG" ]; then
    config_is_ours || fail "file ${LAB_CONFIG} was not generated by this script; not overwriting it"
    if config_has_internal_resolver; then
      info "reusing existing config ${LAB_CONFIG}"
      return 0
    fi
    info "updating ${LAB_CONFIG} to add the internal resolver"
  else
    info "writing static config to ${LAB_CONFIG}"
  fi
  render_static_config > "$LAB_CONFIG"
}

ensure_traefik_container() {
  if container_exists; then
    container_is_lab || fail "a container '${LAB_NAME}' exists that is not part of the lab; not touching it"
    local version mounts
    version="$(docker inspect -f '{{index .Config.Labels "com.spanel.lab.version"}}' "$LAB_NAME" 2>/dev/null || true)"
    mounts="$(docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$LAB_NAME" 2>/dev/null || true)"
    if [ "$version" != "$LAB_VERSION" ] || ! printf '%s' "$mounts" | grep -q '/lab-ca'; then
      info "container ${LAB_NAME} is from an older version; recreating it (lab resources only)"
      docker rm -f "$LAB_NAME" >/dev/null
    else
      if container_is_running "$LAB_NAME"; then
        info "container ${LAB_NAME} is already running; reusing it"
        return 0
      fi
      info "starting container ${LAB_NAME}"
      docker start "$LAB_NAME" >/dev/null
      wait_running "$LAB_NAME" || fail "container ${LAB_NAME} did not stay running"
      return 0
    fi
  fi

  build_run_args
  info "creating container ${LAB_NAME} con ${LAB_IMAGE}"
  if ! docker "${RUN_ARGS[@]}" >/dev/null; then
    echo "error: could not start ${LAB_NAME}" >&2
    if container_exists && container_is_lab; then
      docker rm -f "$LAB_NAME" >/dev/null 2>&1 || true
    fi
    fail "Traefik container creation was rolled back"
  fi
  if ! wait_running "$LAB_NAME"; then
    docker logs --tail 40 "$LAB_NAME" 2>/dev/null || true
    if container_exists && container_is_lab; then
      docker rm -f "$LAB_NAME" >/dev/null 2>&1 || true
    fi
    fail "container ${LAB_NAME} did not stay running; creation was rolled back"
  fi
}

up() {
  require_docker
  validate_email
  resolve_https_port
  preflight_ports
  ensure_dirs
  DNS_IP=""
  TRAEFIK_IP=""

  ensure_network
  ensure_dns_container
  DNS_IP="$(container_ip "$LAB_DNS_NAME")"
  [ -n "$DNS_IP" ] || fail "could not get the IP of ${LAB_DNS_NAME}"

  ensure_ca_container
  ensure_traefik_config

  if [ -n "$ACME_EMAIL" ] && [ ! -f "$LAB_ACME" ]; then
    install -m 600 /dev/null "$LAB_ACME"
  fi
  chmod 600 "$LAB_ACME" 2>/dev/null || true

  ensure_traefik_container
  TRAEFIK_IP="$(container_ip "$LAB_NAME")"
  point_dns_at_traefik "$TRAEFIK_IP"
  wait_dashboard || true
  print_summary
}

print_summary() {
  local fingerprint=""
  if [ -f "$LAB_CA_ROOT" ] && command -v openssl >/dev/null 2>&1; then
    fingerprint="$(openssl x509 -in "$LAB_CA_ROOT" -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//')"
  fi
  cat <<EOF

Lab ready (isolated, without touching the real Traefik).
  traefik:     ${LAB_NAME}
  CA ACME:     ${LAB_CA_NAME} (${LAB_CA_IMAGE})
  internal DNS: ${LAB_DNS_NAME}
  network:     ${LAB_NETWORK}
  HTTPS:       https://127.0.0.1:${HTTPS_PORT}
  HTTP:        http://127.0.0.1:${LAB_HTTP_PORT}
  dashboard:   http://127.0.0.1:${LAB_DASHBOARD_PORT}/dashboard/
  config:      ${LAB_CONFIG}
  dynamic:     ${LAB_DYNAMIC}
  root CA:     ${LAB_CA_ROOT}
  fingerprint: ${fingerprint:-not available}
  ACME:        internal (CA local)$(if [ -n "$ACME_EMAIL" ]; then echo " + letsencrypt-staging"; fi)

Next step:
  python3 scripts/proxy_domain.py doctor
  python3 scripts/proxy_domain.py add --site <wp-container> --domain lucas.spanel

Safe teardown: $0 down
Teardown + data: $0 down --purge
EOF
}

status() {
  require_docker
  resolve_https_port

  local any=0
  local name state role running
  for name in "$LAB_NAME" "$LAB_CA_NAME" "$LAB_DNS_NAME"; do
    if named_container_exists "$name"; then
      named_container_is_lab "$name" \
        || fail "container '${name}' is not part of the lab (missing label ${LAB_LABEL}); not inspecting further"
      any=1
      state="$(docker inspect -f '{{.State.Status}}' "$name")"
      running="$(docker inspect -f '{{.State.Running}}' "$name")"
      role="$(docker inspect -f '{{index .Config.Labels "com.spanel.lab.role"}}' "$name" 2>/dev/null || true)"
      echo "${name}  role=${role:-?}  state=${state}"
      echo "  image:     $(docker inspect -f '{{.Config.Image}}' "$name")"
      echo "  started:   $(docker inspect -f '{{.State.StartedAt}}' "$name")"
      echo "  ip:        $(container_ip "$name")"
      if [ "$running" = "true" ]; then
        case "$name" in
          "$LAB_NAME") echo "  ports:     $(docker port "$name" 2>/dev/null | tr '\n' ' ')" ;;
          "$LAB_CA_NAME") echo "  acme:      https://spanel-step-ca:9000/acme/acme/directory" ;;
          "$LAB_DNS_NAME") echo "  entries:   $(grep -vc '^[[:space:]]*\(#\|$\)' "$LAB_DNS_HOSTS" 2>/dev/null || echo 0)" ;;
        esac
      fi
      echo
    fi
  done

  if [ "$any" = "0" ]; then
    echo "no lab resource exists"
    echo "create it with: $0 up"
    exit 1
  fi

  if [ -f "$LAB_CA_ROOT" ] && command -v openssl >/dev/null 2>&1; then
    echo "Local root CA:"
    openssl x509 -in "$LAB_CA_ROOT" -noout -subject -dates -fingerprint -sha256 2>/dev/null \
      | sed 's/^/  /'
  else
    echo "Root CA: not exported (did you run 'up'?)"
  fi

  echo
  echo "Dynamic YAML: $(count_yaml "$LAB_DYNAMIC") file(s) in ${LAB_DYNAMIC}"
  echo "Internal domains:"
  if [ -s "$LAB_DNS_HOSTS" ]; then
    grep -v '^[[:space:]]*\(#\|$\)' "$LAB_DNS_HOSTS" | sed 's/^/  /'
  else
    echo "  (none)"
  fi

  for name in "$LAB_NAME" "$LAB_CA_NAME" "$LAB_DNS_NAME"; do
    if named_container_exists "$name" && ! container_is_running "$name"; then
      exit 1
    fi
  done
}

count_yaml() {
  local dir="$1"
  if [ -d "$dir" ]; then
    find "$dir" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | wc -l | tr -d ' '
  else
    echo "0"
  fi
}

adopt() {
  require_docker

  if ! docker inspect --type container "$REAL_CONTAINER" >/dev/null 2>&1; then
    fail "container '${REAL_CONTAINER}' does not exist (configure SPANEL_PROXY_TRAEFIK_CONTAINER)"
  fi

  local state started image restart ports networks mounts config_dir yaml_count
  state="$(docker inspect -f '{{.State.Status}}' "$REAL_CONTAINER")"
  started="$(docker inspect -f '{{.State.StartedAt}}' "$REAL_CONTAINER")"
  image="$(docker inspect -f '{{.Config.Image}}' "$REAL_CONTAINER")"
  restart="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$REAL_CONTAINER")"
  ports="$(docker port "$REAL_CONTAINER" 2>/dev/null || true)"
  networks="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$REAL_CONTAINER")"
  mounts="$(docker inspect -f '{{range .Mounts}}{{.Source}} => {{.Destination}}{{"\n"}}{{end}}' "$REAL_CONTAINER")"

  config_dir="$(printf '%s\n' "$mounts" | awk -F' => ' '$2 == "/etc/traefik/config" { print $1; exit }')"
  if [ -z "$config_dir" ]; then
    config_dir="$(printf '%s\n' "$mounts" | awk -F' => ' '$2 == "/etc/traefik" { print $1; exit }')"
  fi
  config_dir="${config_dir:-$REAL_CONFIG_DIR}"

  echo "Adoption (read-only) of the real Traefik"
  echo "  container:  ${REAL_CONTAINER} (${state})"
  echo "  image:      ${image}"
  echo "  StartedAt:  ${started}"
  echo "  restart:    ${restart:-no}"
  echo "  network:    ${networks:-unknown}"
  echo "  ports:"
  if [ -n "$ports" ]; then
    printf '%s\n' "$ports" | sed 's/^/    /'
  else
    echo "    (no published ports)"
  fi
  echo "  config dir: ${config_dir}"
  echo "  mounts:"
  if [ -n "$mounts" ]; then
    printf '%s\n' "$mounts" | sed 's/^/    /'
  else
    echo "    (no mounts)"
  fi

  if [ -d "$config_dir" ]; then
    yaml_count="$(count_yaml "$config_dir")"
    echo "  YAML:       ${yaml_count} file(s) in ${config_dir}"
    local static_yml="${config_dir}/../traefik.yml"
    if [ -f "$static_yml" ]; then
      echo "  entrypoints:"
      grep -E '^[[:space:]]+address:' "$static_yml" 2>/dev/null | sed 's/^[[:space:]]*/    /' || true
      echo "  resolvers:"
      awk '/^certificatesResolvers:/{f=1; next} f && /^[^[:space:]]/{f=0} f && /^  [^[:space:]]+:/{gsub(/^[[:space:]]+|:.*/,""); print "    " $0}' "$static_yml" 2>/dev/null || true
    fi
  else
    echo "  YAML:       directory not accessible from this host"
  fi

  echo
  echo "No resource was modified."
}

remove_named_lab_container() {
  local name="$1"
  if named_container_exists "$name"; then
    if ! named_container_is_lab "$name"; then
      echo "warning: container ${name} does not have ${LAB_LABEL}; not deleting it" >&2
      return 1
    fi
    info "deleting container ${name}"
    docker rm -f "$name" >/dev/null
    return 0
  fi
  echo "container ${name}: does not exist"
  return 1
}

down() {
  require_docker

  local removed=0

  remove_named_lab_container "$LAB_NAME" && removed=1
  remove_named_lab_container "$LAB_CA_NAME" && removed=1
  remove_named_lab_container "$LAB_DNS_NAME" && removed=1

  if network_exists; then
    if [ "$(docker network inspect -f '{{index .Labels "com.spanel.lab"}}' "$LAB_NETWORK" 2>/dev/null || true)" != "true" ]; then
      echo "warning: network ${LAB_NETWORK} does not have ${LAB_LABEL}; not deleting it" >&2
    else
      info "deleting network ${LAB_NETWORK}"
      if ! docker network rm "$LAB_NETWORK" >/dev/null; then
        echo "warning: could not delete network ${LAB_NETWORK} (does it have connected containers?)" >&2
      else
        removed=1
      fi
    fi
  else
    echo "network ${LAB_NETWORK}: does not exist"
  fi

  if [ "$PURGE" = "1" ]; then
    purge_data
  else
    echo "lab data preserved in ${LAB_ROOT}"
    [ "$removed" = "0" ] || echo "to delete them too: $0 down --purge"
  fi
}

volume_exists() {
  docker volume inspect "$LAB_CA_VOLUME" >/dev/null 2>&1
}

purge_data() {
  local paths=()
  [ -d "$LAB_DYNAMIC" ] && paths+=("$LAB_DYNAMIC")
  [ -d "$LAB_DATA" ] && paths+=("$LAB_DATA")
  [ -f "$LAB_CONFIG" ] && paths+=("$LAB_CONFIG")
  [ -d "$LAB_DNS_DIR" ] && paths+=("$LAB_DNS_DIR")
  [ -d "$LAB_CA_DIR" ] && paths+=("$LAB_CA_DIR")

  local volume=""
  if volume_exists; then
    volume="$LAB_CA_VOLUME"
  fi

  if [ "${#paths[@]}" -eq 0 ] && [ -z "$volume" ]; then
    echo "there is no lab data to delete"
    return 0
  fi

  echo "Only the lab's own data will be deleted:"
  printf '  %s\n' "${paths[@]}"
  [ -n "$volume" ] && echo "  docker volume: ${volume}"

  if [ "$ASSUME_YES" != "1" ]; then
    printf 'Confirm deletion? [y/N] '
    if ! read -r answer; then
      answer=""
    fi
    case "$answer" in
      s|S|y|Y) ;;
      *) echo "cancelled: no data was deleted"; return 0 ;;
    esac
  fi

  local p
  for p in "${paths[@]}"; do
    case "$p" in
      "${LAB_ROOT}/"*) rm -rf -- "$p" ;;
      *) fail "unexpected path outside the lab: $p (not deleted)" ;;
    esac
  done
  if [ -n "$volume" ]; then
    docker volume rm "$volume" >/dev/null 2>&1 || echo "warning: could not delete volume ${volume}" >&2
  fi
  echo "lab data deleted"
}

# ---- argumentos -----------------------------------------------------------

if [ "$#" -eq 0 ]; then
  usage >&2
  exit 2
fi

case "$1" in
  -h|--help|help) usage; exit 0 ;;
  adopt|plan|up|status|down) MODE="$1"; shift ;;
  *) fail "unknown mode: $1 (use --help)" ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option for ${MODE}: $1 (use --help)" ;;
  esac
done

if [ "$PURGE" = "1" ] && [ "$MODE" != "down" ]; then
  fail "--purge only makes sense with 'down'"
fi

case "$MODE" in
  adopt) adopt ;;
  plan) plan ;;
  up) up ;;
  status) status ;;
  down) down ;;
esac
