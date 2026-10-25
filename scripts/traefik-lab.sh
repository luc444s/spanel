#!/usr/bin/env bash
# Laboratorio aislado de Traefik para Spanel.
#
# Levanta un Traefik propio (contenedor/red con prefijo spanel-traefik-lab) en
# puertos altos, sin tocar el Traefik real ni /opt/stacks. `adopt` solo
# inspecciona el Traefik existente, en modo lectura.
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
#   SPANEL_PROXY_ACME_EMAIL  email para ACME staging (opcional; sin email no
#                            se configura ningún resolver)
#   LAB_IMAGE                imagen de Traefik (default: traefik:v3.7.8)
#   LAB_HTTP_PORT            default 9080 (host -> 80)
#   LAB_HTTPS_PORT           default 9443 (host -> 443)
#   LAB_DASHBOARD_PORT       default 9081 (host -> 8080)
#   SPANEL_PROXY_TRAEFIK_CONTAINER  contenedor real a inspeccionar (default traefik)
#   SPANEL_PROXY_CONFIG_DIR  directorio dinámico del Traefik real
#                            (default /opt/stacks/traefik/config)
#
# Seguridad:
#   - Solo crea/elimina recursos con el label com.spanel.lab=true.
#   - Nunca ejecuta docker system prune, ni toca /opt/stacks desde up/down.
#   - `down` sin --purge conserva los datos del laboratorio.
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

LAB_NAME="spanel-traefik-lab"
LAB_NETWORK="spanel-traefik-lab"
LAB_LABEL="com.spanel.lab=true"
LAB_IMAGE="${LAB_IMAGE:-traefik:v3.7.8}"
LAB_HTTP_PORT="${LAB_HTTP_PORT:-9080}"
LAB_HTTPS_PORT="${LAB_HTTPS_PORT:-9443}"
LAB_DASHBOARD_PORT="${LAB_DASHBOARD_PORT:-9081}"

REAL_CONTAINER="${SPANEL_PROXY_TRAEFIK_CONTAINER:-traefik}"
REAL_CONFIG_DIR="${SPANEL_PROXY_CONFIG_DIR:-/opt/stacks/traefik/config}"
ACME_EMAIL="${SPANEL_PROXY_ACME_EMAIL:-}"

usage() {
  cat <<'EOF'
Laboratorio aislado de Traefik para Spanel.

Modos:
  adopt             Inspecciona el Traefik real (solo lectura). No modifica nada.
  plan              Imprime contenedor, red, directorios, puertos, config y
                    comandos que ejecutaría `up`. No necesita Docker ni muta nada.
  up                Crea/reutiliza el laboratorio en 9080/9443/9081.
  status            Muestra el estado del laboratorio.
  down              Elimina SOLO los recursos con com.spanel.lab=true.
  down --purge      Además borra los datos del laboratorio (pide confirmación).

Opciones:
  --purge           Solo con `down`: borra config/dynamic/data del laboratorio.
  --yes             No pregunta en `down --purge`.
  -h, --help        Muestra esta ayuda.

Entorno:
  SPANEL_PROXY_ACME_EMAIL   Email para ACME staging. Sin email, sin resolver.
  LAB_IMAGE                 Default: traefik:v3.7.8
  LAB_HTTP_PORT             Default: 9080  (host -> 80)
  LAB_HTTPS_PORT            Default: 9443  (host -> 443)
  LAB_DASHBOARD_PORT        Default: 9081  (host -> 8080)
  SPANEL_PROXY_TRAEFIK_CONTAINER  Default: traefik (solo adopt)
  SPANEL_PROXY_CONFIG_DIR   Default: /opt/stacks/traefik/config (solo adopt)

Puertos del laboratorio (solo en 127.0.0.1):
  9080  HTTP   -> 80
  9443  HTTPS  -> 443
  9081  dashboard/API (insecure, solo laboratorio)

Recursos propios:
  contenedor  spanel-traefik-lab
  red         spanel-traefik-lab
  directorio  scripts/traefik-lab/

Nunca se ejecuta docker system prune ni se toca /opt/stacks desde up/down.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "docker no está en PATH"
  docker info >/dev/null 2>&1 || fail "no se puede hablar con el daemon de Docker"
}

validate_email() {
  if [ -n "$ACME_EMAIL" ]; then
    case "$ACME_EMAIL" in
      *[[:space:]]*|*@*@*|@*|*@) fail "SPANEL_PROXY_ACME_EMAIL no parece un email válido: $ACME_EMAIL" ;;
      *@*) ;;
      *) fail "SPANEL_PROXY_ACME_EMAIL no parece un email válido: $ACME_EMAIL" ;;
    esac
  fi
}

container_exists() {
  docker inspect "$LAB_NAME" >/dev/null 2>&1
}

network_exists() {
  docker network inspect "$LAB_NETWORK" >/dev/null 2>&1
}

container_is_lab() {
  [ "$(docker inspect -f '{{index .Config.Labels "com.spanel.lab"}}' "$LAB_NAME" 2>/dev/null || true)" = "true" ]
}

network_is_lab() {
  [ "$(docker network inspect -f '{{index .Labels "com.spanel.lab"}}' "$LAB_NETWORK" 2>/dev/null || true)" = "true" ]
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
    return $?
  fi
  docker ps --format '{{.Ports}}' 2>/dev/null \
    | grep -Eq "(^|[, ])(0\.0\.0\.0|127\.0\.0\.1|\[::\]):${port}->"
}

check_ports_free() {
  local port
  local used=0
  for port in "$LAB_HTTP_PORT" "$LAB_HTTPS_PORT" "$LAB_DASHBOARD_PORT"; do
    if port_in_use "$port"; then
      echo "error: el puerto ${port} ya está ocupado; el laboratorio no arranca." >&2
      used=1
    fi
  done
  if [ "$used" = "1" ]; then
    fail "liberá esos puertos o elegí otros con LAB_HTTP_PORT/LAB_HTTPS_PORT/LAB_DASHBOARD_PORT"
  fi
}

render_static_config() {
  cat <<'EOF'
# managed-by: spanel traefik-lab
# Generado por scripts/traefik-lab.sh. No editar a mano: recrear con el script.
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
EOF

  if [ -n "$ACME_EMAIL" ]; then
    cat <<EOF

certificatesResolvers:
  letsencrypt-staging:
    acme:
      email: "${ACME_EMAIL}"
      storage: /etc/traefik/data/acme.json
      caServer: https://acme-staging-v02.api.letsencrypt.org/directory
      httpChallenge:
        entryPoint: web
EOF
  else
    cat <<'EOF'

# ACME deshabilitado: exportá SPANEL_PROXY_ACME_EMAIL para habilitar
# letsencrypt-staging (staging de Let's Encrypt; no emite certificados públicos).
EOF
  fi
}

build_run_args() {
  RUN_ARGS=(
    run -d
    --name "$LAB_NAME"
    --label "com.spanel.lab=true"
    --label "com.spanel.lab.role=traefik"
    --restart unless-stopped
    --network "$LAB_NETWORK"
    -p "127.0.0.1:${LAB_HTTP_PORT}:80"
    -p "127.0.0.1:${LAB_HTTPS_PORT}:443"
    -p "127.0.0.1:${LAB_DASHBOARD_PORT}:8080"
    -v "${LAB_CONFIG}:/etc/traefik/traefik.yml:ro"
    -v "${LAB_DYNAMIC}:/etc/traefik/dynamic:ro"
    -v "${LAB_DATA}:/etc/traefik/data"
    "$LAB_IMAGE"
  )
}

print_cmd() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

plan() {
  validate_email
  build_run_args

  echo "Plan del laboratorio Traefik (no ejecuta nada)"
  echo
  echo "  contenedor:  ${LAB_NAME}"
  echo "  red:         ${LAB_NETWORK}"
  echo "  label:       ${LAB_LABEL}"
  echo "  imagen:      ${LAB_IMAGE}"
  echo "  config:      ${LAB_CONFIG}"
  echo "  dynamic:     ${LAB_DYNAMIC}"
  echo "  datos:       ${LAB_DATA}"
  echo
  echo "  puertos (solo 127.0.0.1):"
  echo "    ${LAB_HTTP_PORT}:80      (HTTP, host 127.0.0.1)"
  echo "    ${LAB_HTTPS_PORT}:443     (HTTPS, host 127.0.0.1)"
  echo "    ${LAB_DASHBOARD_PORT}:8080    (dashboard/API, host 127.0.0.1)"
  echo
  if [ -n "$ACME_EMAIL" ]; then
    echo "  ACME: staging habilitado (letsencrypt-staging) para ${ACME_EMAIL}"
  else
    echo "  ACME: deshabilitado (definí SPANEL_PROXY_ACME_EMAIL para staging)"
  fi
  echo
  echo "Directorios y archivos:"
  print_cmd mkdir -p "$LAB_ROOT" "$LAB_DYNAMIC" "$LAB_DATA"
  if [ ! -f "$LAB_CONFIG" ]; then
    echo "  # escribir config estática en $LAB_CONFIG:"
    sed 's/^/  | /' < <(render_static_config)
  else
    echo "  # reutilizar config existente: $LAB_CONFIG (no se sobrescribe)"
  fi
  if [ -n "$ACME_EMAIL" ] && [ ! -f "$LAB_ACME" ]; then
    print_cmd install -m 600 /dev/null "$LAB_ACME"
  fi
  echo
  echo "Comandos:"
  echo "  # si la red ya existe y tiene ${LAB_LABEL}, se reutiliza; si existe sin el label, 'up' aborta"
  print_cmd docker network create --label "$LAB_LABEL" "$LAB_NETWORK"
  echo "  # si el contenedor ya existe con el label, se reutiliza; no se recrea"
  echo "  # 'up' verifica que ${LAB_HTTP_PORT}, ${LAB_HTTPS_PORT} y ${LAB_DASHBOARD_PORT} estén libres"
  print_cmd docker "${RUN_ARGS[@]}"
  echo
  echo "No se verifica Docker ni se comprueban puertos en 'plan'; eso ocurre en 'up'."
}

up() {
  require_docker
  validate_email
  build_run_args

  if container_exists; then
    container_is_lab || fail "existe un contenedor '${LAB_NAME}' que no es del laboratorio (sin label ${LAB_LABEL}); no se toca"
    local state
    state="$(docker inspect -f '{{.State.Status}}' "$LAB_NAME")"
    if [ "$state" = "running" ]; then
      info "contenedor ${LAB_NAME} ya existe y está running; se reutiliza"
    else
      info "contenedor ${LAB_NAME} existe (${state}); se inicia sin recrearlo"
      docker start "$LAB_NAME" >/dev/null
      if ! wait_running; then
        docker logs --tail 40 "$LAB_NAME" 2>/dev/null || true
        fail "el contenedor ${LAB_NAME} no quedó running"
      fi
    fi
    print_summary
    return 0
  fi

  if network_exists; then
    network_is_lab || fail "existe una red '${LAB_NETWORK}' que no es del laboratorio; no se toca"
  fi

  check_ports_free

  mkdir -p "$LAB_ROOT" "$LAB_DYNAMIC" "$LAB_DATA"
  if [ ! -f "$LAB_CONFIG" ]; then
    info "escribiendo config estática en ${LAB_CONFIG}"
    render_static_config > "$LAB_CONFIG"
  else
    info "reutilizando config existente ${LAB_CONFIG}"
  fi

  if [ -n "$ACME_EMAIL" ]; then
    if [ ! -f "$LAB_ACME" ]; then
      install -m 600 /dev/null "$LAB_ACME"
    fi
    chmod 600 "$LAB_ACME" 2>/dev/null || true
  fi

  if ! network_exists; then
    info "creando red ${LAB_NETWORK}"
    docker network create --label "$LAB_LABEL" "$LAB_NETWORK" >/dev/null
  else
    info "reutilizando red ${LAB_NETWORK}"
  fi

  info "arrancando contenedor ${LAB_NAME} con ${LAB_IMAGE}"
  if ! docker "${RUN_ARGS[@]}" >/dev/null; then
    echo "error: no se pudo arrancar ${LAB_NAME}" >&2
    if container_exists && container_is_lab; then
      docker rm -f "$LAB_NAME" >/dev/null 2>&1 || true
    fi
    fail "se revirtió el alta del laboratorio"
  fi

  if ! wait_running; then
    docker logs --tail 40 "$LAB_NAME" 2>/dev/null || true
    if container_exists && container_is_lab; then
      docker rm -f "$LAB_NAME" >/dev/null 2>&1 || true
    fi
    fail "el laboratorio no quedó running; se revirtió el alta"
  fi

  wait_dashboard || true
  print_summary
}

wait_running() {
  local running=""
  local i
  for i in $(seq 1 20); do
    running="$(docker inspect -f '{{.State.Running}}' "$LAB_NAME" 2>/dev/null || echo false)"
    [ "$running" = "true" ] && return 0
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
  echo "aviso: el dashboard aún no responde en 127.0.0.1:${LAB_DASHBOARD_PORT}" >&2
  return 1
}

print_summary() {
  cat <<EOF

Laboratorio Traefik listo (aislado).
  contenedor:  ${LAB_NAME}
  red:         ${LAB_NETWORK}
  HTTP:        http://127.0.0.1:${LAB_HTTP_PORT}
  HTTPS:       https://127.0.0.1:${LAB_HTTPS_PORT}
  dashboard:   http://127.0.0.1:${LAB_DASHBOARD_PORT}/dashboard/
  config:      ${LAB_CONFIG}
  dynamic:     ${LAB_DYNAMIC}
  datos:       ${LAB_DATA}
  ACME:        $(if [ -n "$ACME_EMAIL" ]; then echo "staging (letsencrypt-staging)"; else echo "deshabilitado"; fi)

YAML dinámico: dejá archivos *.yml en ${LAB_DYNAMIC} (Traefik los recarga solo).
Baja segura:   $0 down
Baja + datos:  $0 down --purge
EOF
}

status() {
  require_docker

  if ! container_exists; then
    echo "no existe el contenedor ${LAB_NAME}"
    echo "crealo con: $0 up"
    exit 1
  fi
  container_is_lab || fail "el contenedor '${LAB_NAME}' no es del laboratorio (sin label ${LAB_LABEL}); no se inspecciona más"

  local state running started ports networks
  state="$(docker inspect -f '{{.State.Status}}' "$LAB_NAME")"
  running="$(docker inspect -f '{{.State.Running}}' "$LAB_NAME")"
  started="$(docker inspect -f '{{.State.StartedAt}}' "$LAB_NAME")"
  ports="$(docker port "$LAB_NAME" 2>/dev/null || true)"
  networks="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$LAB_NAME")"

  echo "Laboratorio Traefik"
  echo "  contenedor: ${LAB_NAME} (${state})"
  echo "  imagen:     $(docker inspect -f '{{.Config.Image}}' "$LAB_NAME")"
  echo "  iniciado:   ${started}"
  echo "  red:        ${networks:-desconocida}"
  echo "  puertos:"
  if [ -n "$ports" ]; then
    printf '%s\n' "$ports" | sed 's/^/    /'
  else
    echo "    (sin puertos publicados)"
  fi
  echo "  config:     ${LAB_CONFIG}"
  echo "  dynamic:    $(count_yaml "$LAB_DYNAMIC") archivo(s) YAML en ${LAB_DYNAMIC}"
  if [ -n "$ACME_EMAIL" ]; then
    echo "  ACME:       staging habilitado"
  else
    echo "  ACME:       deshabilitado (SPANEL_PROXY_ACME_EMAIL vacío)"
  fi

  if [ "$running" = "true" ] && command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 2 "http://127.0.0.1:${LAB_DASHBOARD_PORT}/dashboard/" >/dev/null 2>&1; then
      echo "  dashboard:  responde en http://127.0.0.1:${LAB_DASHBOARD_PORT}/"
    else
      echo "  dashboard:  sin respuesta en 127.0.0.1:${LAB_DASHBOARD_PORT}"
    fi
  fi

  [ "$running" = "true" ] || exit 1
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

  if ! docker inspect "$REAL_CONTAINER" >/dev/null 2>&1; then
    fail "no existe el contenedor '${REAL_CONTAINER}' (configurá SPANEL_PROXY_TRAEFIK_CONTAINER)"
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

  echo "Adopción (solo lectura) del Traefik real"
  echo "  contenedor: ${REAL_CONTAINER} (${state})"
  echo "  imagen:     ${image}"
  echo "  StartedAt:  ${started}"
  echo "  restart:    ${restart:-no}"
  echo "  red:        ${networks:-desconocida}"
  echo "  puertos:"
  if [ -n "$ports" ]; then
    printf '%s\n' "$ports" | sed 's/^/    /'
  else
    echo "    (sin puertos publicados)"
  fi
  echo "  config dir: ${config_dir}"
  echo "  mounts:"
  if [ -n "$mounts" ]; then
    printf '%s\n' "$mounts" | sed 's/^/    /'
  else
    echo "    (sin mounts)"
  fi

  if [ -d "$config_dir" ]; then
    yaml_count="$(count_yaml "$config_dir")"
    echo "  YAML:       ${yaml_count} archivo(s) en ${config_dir}"
    local static_yml="${config_dir}/../traefik.yml"
    if [ -f "$static_yml" ]; then
      echo "  entrypoints:"
      grep -E '^[[:space:]]+address:' "$static_yml" 2>/dev/null | sed 's/^[[:space:]]*/    /' || true
      echo "  resolvers:"
      awk '/^certificatesResolvers:/{f=1; next} f && /^[^[:space:]]/{f=0} f && /^  [^[:space:]]+:/{gsub(/^[[:space:]]+|:.*/,""); print "    " $0}' "$static_yml" 2>/dev/null || true
    fi
  else
    echo "  YAML:       directorio no accesible desde este host"
  fi

  echo
  echo "No se modificó ningún recurso."
}

down() {
  require_docker

  local removed=0

  if container_exists; then
    if ! container_is_lab; then
      fail "el contenedor '${LAB_NAME}' no tiene ${LAB_LABEL}; no se elimina"
    fi
    info "eliminando contenedor ${LAB_NAME}"
    docker rm -f "$LAB_NAME" >/dev/null
    removed=1
  else
    echo "contenedor ${LAB_NAME}: no existe"
  fi

  if network_exists; then
    if ! network_is_lab; then
      echo "aviso: la red ${LAB_NETWORK} no tiene ${LAB_LABEL}; no se elimina" >&2
    else
      info "eliminando red ${LAB_NETWORK}"
      if ! docker network rm "$LAB_NETWORK" >/dev/null; then
        echo "aviso: no se pudo eliminar la red ${LAB_NETWORK} (¿tiene contenedores conectados?)" >&2
      else
        removed=1
      fi
    fi
  else
    echo "red ${LAB_NETWORK}: no existe"
  fi

  if [ "$PURGE" = "1" ]; then
    purge_data
  else
    echo "datos del laboratorio conservados en ${LAB_ROOT}"
    [ "$removed" = "0" ] || echo "para borrarlos también: $0 down --purge"
  fi
}

purge_data() {
  local paths=()
  [ -d "$LAB_DYNAMIC" ] && paths+=("$LAB_DYNAMIC")
  [ -d "$LAB_DATA" ] && paths+=("$LAB_DATA")
  [ -f "$LAB_CONFIG" ] && paths+=("$LAB_CONFIG")

  if [ "${#paths[@]}" -eq 0 ]; then
    echo "no hay datos del laboratorio que borrar"
    return 0
  fi

  echo "Se borrarán únicamente datos propios del laboratorio:"
  printf '  %s\n' "${paths[@]}"

  if [ "$ASSUME_YES" != "1" ]; then
    printf '¿Confirmás el borrado? [s/N] '
    if ! read -r answer; then
      answer=""
    fi
    case "$answer" in
      s|S|y|Y) ;;
      *) echo "cancelado: no se borró ningún dato"; return 0 ;;
    esac
  fi

  local p
  for p in "${paths[@]}"; do
    case "$p" in
      "${LAB_ROOT}/"*) rm -rf -- "$p" ;;
      *) fail "ruta inesperada fuera del laboratorio: $p (no se borra)" ;;
    esac
  done
  echo "datos del laboratorio eliminados"
}

# ---- argumentos -----------------------------------------------------------

if [ "$#" -eq 0 ]; then
  usage >&2
  exit 2
fi

case "$1" in
  -h|--help|help) usage; exit 0 ;;
  adopt|plan|up|status|down) MODE="$1"; shift ;;
  *) fail "modo desconocido: $1 (usá --help)" ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "opción desconocida para ${MODE}: $1 (usá --help)" ;;
  esac
done

if [ "$PURGE" = "1" ] && [ "$MODE" != "down" ]; then
  fail "--purge solo tiene sentido con 'down'"
fi

case "$MODE" in
  adopt) adopt ;;
  plan) plan ;;
  up) up ;;
  status) status ;;
  down) down ;;
esac
