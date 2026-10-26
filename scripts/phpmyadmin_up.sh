#!/usr/bin/env bash
# Levanta phpMyAdmin como hub multi-servidor apuntando a MariaDB que YA existen.
#
# No crea ni modifica bases, volúmenes ni contenedores de sitios: crea la red
# interna $DB_NETWORK si falta y conecta (sin desconectar de sus redes) el
# MariaDB heredado y el propio phpMyAdmin a ella. Con --down solo borra $NAME.
#
# Uso:
#   DB_HOST=mariadb ./scripts/phpmyadmin_up.sh
#   ./scripts/phpmyadmin_up.sh --host mariadb --port 8081
#   ./scripts/phpmyadmin_up.sh --host mariadb --network mariadb_internal
#   ./scripts/phpmyadmin_up.sh --host mariadb --dry-run
#   ./scripts/phpmyadmin_up.sh --down --yes
#
# Variables: NAME, IMAGE, NETWORK, DB_NETWORK, PORT, DB_HOST, DB_PORT,
#            PUBLIC_URL, CONFIG, MEMORY, CPUS, RESTART.
set -euo pipefail

NAME="${NAME:-spanel-phpmyadmin}"
IMAGE="${IMAGE:-phpmyadmin:5.2.3}"
NETWORK="${NETWORK:-mariadb_internal}"
DB_NETWORK="${DB_NETWORK:-spanel-db}"
PORT="${PORT:-8081}"
DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-3306}"
CONFIG="${CONFIG:-}"
MEMORY="${MEMORY:-256m}"
CPUS="${CPUS:-0.50}"
RESTART="${RESTART:-unless-stopped}"
PUBLIC_URL="${PUBLIC_URL:-}"
ACTION="up"
DRY_RUN=0
ASSUME_YES=0

usage() {
  cat <<'EOF'
Levanta phpMyAdmin como hub para todas las bases.

  --host HOST         MariaDB heredado (contenedor). Obligatorio salvo --down.
  --port PORT         Puerto local, solo 127.0.0.1. Default: 8081.
  --network NET       Red donde vive el MariaDB. Default: mariadb_internal.
  --db-network NET    Red interna que comparten phpMyAdmin y las bases.
                      Default: spanel-db (se crea --internal si falta).
  --name NAME         Nombre del contenedor. Default: spanel-phpmyadmin.
  --image IMAGE       Default: phpmyadmin:5.2.3.
  --public-url URL    URL que verá el navegador (PMA_ABSOLUTE_URI).
  --config FILE       config.user.inc.php opcional, montado en solo lectura.
  --memory 256m       Límite de memoria. --cpus 0.50
  --dry-run           Muestra el comando y no ejecuta nada.
  --down              Borra SOLO el contenedor con ese nombre.
  --yes               No pregunta en --down.
  -h, --help          Esto.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) DB_HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --network) NETWORK="$2"; shift 2 ;;
    --db-network) DB_NETWORK="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --public-url) PUBLIC_URL="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --down) ACTION="down"; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "opción desconocida: $1 (usá --help)" ;;
  esac
done

PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:${PORT}/}"

command -v docker >/dev/null 2>&1 || fail "docker no está en PATH"
docker info >/dev/null 2>&1 || fail "no se puede hablar con el daemon de Docker"

if [ "$ACTION" = "down" ]; then
  if ! docker inspect "$NAME" >/dev/null 2>&1; then
    echo "no existe el contenedor $NAME; nada que borrar"
    exit 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "docker rm -f $NAME"
    exit 0
  fi
  if [ "$ASSUME_YES" != "1" ]; then
    printf 'Borrar el contenedor %s? [s/N] ' "$NAME"
    read -r answer
    case "$answer" in
      s|S|y|Y) ;;
      *) echo "cancelado"; exit 1 ;;
    esac
  fi
  docker rm -f "$NAME"
  echo "contenedor $NAME eliminado; la base, la red y los volúmenes no se tocaron"
  exit 0
fi

[ -n "$DB_HOST" ] || { usage; fail "falta --host (o DB_HOST=...)"; }

docker network inspect "$NETWORK" >/dev/null 2>&1 \
  || fail "la red '$NETWORK' no existe. Si el MariaDB está en otra red, pasá --network. Para crearla: docker network create --internal $NETWORK"

ARBITRARY_CONFIG="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/phpmyadmin_arbitrary.php"
[ -f "$ARBITRARY_CONFIG" ] || fail "falta $ARBITRARY_CONFIG"

if ! docker network inspect "$DB_NETWORK" >/dev/null 2>&1; then
  if [ "$DRY_RUN" = "1" ]; then
    echo "docker network create --internal $DB_NETWORK"
  else
    docker network create --internal "$DB_NETWORK" >/dev/null \
      || fail "no se pudo crear la red interna '$DB_NETWORK'"
    echo "red interna $DB_NETWORK creada"
  fi
fi

if docker inspect "$NAME" >/dev/null 2>&1; then
  status=$(docker inspect -f '{{.State.Status}}' "$NAME")
  fail "ya existe el contenedor '$NAME' (estado: $status). Usá --down, o --name con otro nombre"
fi

if docker inspect "$DB_HOST" >/dev/null 2>&1; then
  running=$(docker inspect -f '{{.State.Running}}' "$DB_HOST")
  [ "$running" = "true" ] || fail "el contenedor $DB_HOST no está corriendo"
  networks=$(docker inspect -f '{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{end}}' "$DB_HOST")
  case " $networks " in
    *" $NETWORK "*) ;;
    *) echo "aviso: $DB_HOST no está en la red $NETWORK; verificá la conectividad" ;;
  esac
  # A.SPEC 0033: la base heredada se suma a la red del hub sin abandonar la
  # suya. Idempotente: si ya está conectada no se ejecuta nada.
  case " $networks " in
    *" $DB_NETWORK "*) ;;
    *)
      if [ "$DRY_RUN" = "1" ]; then
        echo "docker network connect $DB_NETWORK $DB_HOST"
      else
        docker network connect "$DB_NETWORK" "$DB_HOST" \
          || fail "no se pudo conectar $DB_HOST a la red $DB_NETWORK"
        echo "$DB_HOST conectado a $DB_NETWORK"
      fi
      ;;
  esac
else
  echo "aviso: '$DB_HOST' no es un contenedor local; se usará como hostname"
fi

if command -v ss >/dev/null 2>&1; then
  if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    fail "el puerto $PORT ya está escuchando; elegí otro con --port"
  fi
else
  if docker ps --format '{{.Ports}}' | grep -q "127.0.0.1:$PORT->"; then
    fail "el puerto $PORT ya está ocupado; elegí otro con --port"
  fi
fi

if [ -n "$CONFIG" ] && [ ! -f "$CONFIG" ]; then
  fail "no existe el archivo de configuración: $CONFIG"
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "descargando $IMAGE..."
  docker pull "$IMAGE"
}

if [ "$DRY_RUN" = "0" ]; then
  if docker run --rm --network "$DB_NETWORK" --entrypoint getent "$IMAGE" hosts "$DB_HOST" >/dev/null 2>&1; then
    echo "ok: $DB_HOST resuelve desde la red $DB_NETWORK"
  else
    echo "aviso: no se pudo resolver $DB_HOST desde la red $DB_NETWORK; revisá el nombre o la red"
  fi
fi

run_args=(
  run -d
  --name "$NAME"
  --restart "$RESTART"
  --network "$NETWORK"
  -p "127.0.0.1:${PORT}:8080"
  -e "PMA_HOST=$DB_HOST"
  -e "PMA_PORT=$DB_PORT"
  -e "PMA_ARBITRARY=1"
  -e "PMA_ABSOLUTE_URI=$PUBLIC_URL"
  -e "APACHE_PORT=8080"
  -e "HIDE_PHP_VERSION=true"
  -e "UPLOAD_LIMIT=64M"
  -e "MEMORY_LIMIT=256M"
  -e "TZ=UTC"
  --cap-drop ALL
  --cap-add SETUID
  --cap-add SETGID
  --cap-add CHOWN
  --cap-add DAC_OVERRIDE
  --security-opt no-new-privileges:true
  --tmpfs /tmp
  --tmpfs /sessions
  --tmpfs /var/run/apache2
  --tmpfs /var/lock/apache2
  --tmpfs /var/log/apache2
  --tmpfs /var/lib/php/sessions
  --memory "$MEMORY"
  --cpus "$CPUS"
  --health-cmd "curl -fsS http://127.0.0.1:8080/ >/dev/null || exit 1"
  --health-interval 10s
  --health-timeout 5s
  --health-retries 12
  --health-start-period 5s
)

if [ -n "$CONFIG" ]; then
  run_args+=(-v "${CONFIG}:/etc/phpmyadmin/config.user.inc.php:ro")
  run_args+=(-v "${ARBITRARY_CONFIG}:/etc/phpmyadmin/conf.d/spanel-arbitrary.php:ro")
else
  run_args+=(-v "${ARBITRARY_CONFIG}:/etc/phpmyadmin/config.user.inc.php:ro")
fi

run_args+=("$IMAGE")

if [ "$DRY_RUN" = "1" ]; then
  printf 'docker'
  printf ' %q' "${run_args[@]}"
  printf '\n'
  exit 0
fi

if ! docker "${run_args[@]}" >/dev/null; then
  docker logs --tail 40 "$NAME" 2>/dev/null || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  fail "no se pudo arrancar el contenedor; se revirtió el alta"
fi

# El hub necesita ver a los MariaDB por $DB_NETWORK; conserva la red de la app
# ($NETWORK) donde el contenedor nació.
if [ "$DB_NETWORK" != "$NETWORK" ]; then
  if ! docker network connect "$DB_NETWORK" "$NAME" >/dev/null 2>&1; then
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    fail "no se pudo conectar $NAME a $DB_NETWORK; se revirtió el alta"
  fi
fi

echo -n "esperando a que phpMyAdmin responda"
status=""
for _ in $(seq 1 40); do
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$NAME" 2>/dev/null || echo "missing")
  case "$status" in
    healthy) echo " ok"; break ;;
    unhealthy|exited|dead) echo " $status"; break ;;
  esac
  echo -n "."
  sleep 1
done

if [ "$status" != "healthy" ]; then
  docker logs --tail 40 "$NAME" || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  fail "phpMyAdmin no quedó healthy (estado: ${status:-desconocido}); se revirtió el alta"
fi

cat <<EOF

phpMyAdmin listo.
  URL:       $PUBLIC_URL
  contenedor: $NAME
  MariaDB:   $DB_HOST:$DB_PORT ($NETWORK)
  red de bases: $DB_NETWORK (interna)
  escucha en 127.0.0.1:$PORT (no está expuesto a Internet)

Entrá con un usuario de la base; phpMyAdmin muestra solo las bases que ese
usuario puede ver. Para entrar desde otra máquina, usá un túnel SSH o
'tailscale serve', nunca publiques el puerto en 0.0.0.0.

Baja: $0 --name $NAME --down --yes
EOF
