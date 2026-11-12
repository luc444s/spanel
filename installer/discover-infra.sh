#!/usr/bin/env bash
# Inventaría una instalación Docker existente para poder diseñar o validar su
# réplica. Es de solo lectura: no copia datos de volúmenes ni valores secretos.
set -euo pipefail

OUTPUT=""
COPY_COMPOSE=0
MAKE_ARCHIVE=0

usage() {
  cat <<'EOF'
Descubre la infraestructura de un servidor PanelTutor/SPanel (solo lectura).

Uso:
  sudo ./installer/discover-infra.sh [opciones]

Opciones:
  -o, --output DIR   Directorio del informe. Por defecto:
                     ./spanel-infra-AAAAMMDD-HHMMSS
  --copy-compose     Copia los YAML de Compose detectados. Pueden contener
                     secretos inline; el directorio queda con permisos 0700.
                     Nunca copia .env automáticamente.
  --archive          Crea DIR.tar.gz al terminar (modo 0600).
  -h, --help         Muestra esta ayuda.

El informe incluye host, Docker, Compose, contenedores, imágenes, puertos,
mounts, redes, volúmenes, nombres de variables de entorno, labels saneados,
Traefik, docker-mailserver, phpMyAdmin, certificados públicos, systemd y
firewall. No ejecuta docker compose config porque ese comando puede revelar
los valores resueltos de secretos.
EOF
}

fail() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*" >&2
}

warn() {
  echo "warning: $*" >&2
}

have() {
  command -v "$1" >/dev/null 2>&1
}

safe_name() {
  printf '%s' "$1" | tr -cs 'A-Za-z0-9_.-' '_'
}

is_sensitive_name() {
  local value
  value=$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')
  case "$value" in
    *PASSWORD*|*PASSWD*|*SECRET*|*TOKEN*|*PRIVATE*|*KEY*|*AUTH*|*CREDENTIAL*|*COOKIE*|*HASH*) return 0 ;;
    *) return 1 ;;
  esac
}

capture() {
  # Un comando de diagnóstico que falla no debe impedir el resto del informe.
  local destination=$1
  local status
  shift
  if "$@" >"$destination" 2>&1; then
    return 0
  else
    status=$?
  fi
  printf '\n[command failed with exit code %s]\n' "$status" >>"$destination"
  return 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -o|--output)
      [ "$#" -ge 2 ] || fail "$1 requires a directory"
      OUTPUT=$2
      shift 2
      ;;
    --copy-compose) COPY_COMPOSE=1; shift ;;
    --archive) MAKE_ARCHIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1 (use --help)" ;;
  esac
done

have docker || fail "docker is not in PATH"
docker info >/dev/null 2>&1 || fail "cannot talk to the Docker daemon; try sudo"

if [ -z "$OUTPUT" ]; then
  OUTPUT="./spanel-infra-$(date -u +%Y%m%d-%H%M%S)"
fi
[ ! -e "$OUTPUT" ] || fail "output already exists: $OUTPUT"

OLD_UMASK=$(umask)
umask 077
mkdir -p "$OUTPUT"/{host,docker/containers,docker/env-keys,docker/labels,services,replication}
OUTPUT=$(cd "$OUTPUT" && pwd)

cleanup() {
  umask "$OLD_UMASK"
}
trap cleanup EXIT

info "collecting host information"
{
  echo "generated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "hostname=$(hostname -f 2>/dev/null || hostname)"
  echo "kernel=$(uname -srmo)"
  echo "user=$(id -un)"
  echo "uid=$(id -u)"
  if [ -r /etc/os-release ]; then
    sed -n 's/^\(ID\|VERSION_ID\|PRETTY_NAME\)=/os_\1=/p' /etc/os-release
  fi
} >"$OUTPUT/host/summary.env"

capture "$OUTPUT/host/disk.txt" df -hT
capture "$OUTPUT/host/memory.txt" sh -c 'free -h 2>/dev/null || vm_stat 2>/dev/null || true'
capture "$OUTPUT/host/listening-ports.txt" sh -c 'ss -lntup 2>/dev/null || netstat -lntup 2>/dev/null || true'
capture "$OUTPUT/host/ip-addresses.txt" sh -c 'ip -brief address 2>/dev/null || ifconfig 2>/dev/null || true'
capture "$OUTPUT/host/routes.txt" sh -c 'ip route 2>/dev/null || route -n 2>/dev/null || true'
if have dpkg-query; then
  capture "$OUTPUT/host/packages.txt" dpkg-query -W '-f=${binary:Package}\t${Version}\n'
elif have rpm; then
  capture "$OUTPUT/host/packages.txt" rpm -qa
elif have apk; then
  capture "$OUTPUT/host/packages.txt" apk info -vv
fi

if have systemctl; then
  capture "$OUTPUT/host/systemd-enabled.txt" systemctl list-unit-files --state=enabled --no-pager
  capture "$OUTPUT/host/systemd-active-services.txt" systemctl list-units --type=service --state=running --no-pager
fi
if have ufw; then
  capture "$OUTPUT/host/firewall-ufw.txt" ufw status verbose
fi
if have nft; then
  capture "$OUTPUT/host/firewall-nftables.txt" nft list ruleset
fi
if have iptables-save; then
  capture "$OUTPUT/host/firewall-iptables.txt" iptables-save
fi

info "collecting Docker inventory"
capture "$OUTPUT/docker/version.txt" docker version
{
  printf 'server_version='; docker info --format '{{.ServerVersion}}' 2>/dev/null || true
  printf 'operating_system='; docker info --format '{{.OperatingSystem}}' 2>/dev/null || true
  printf 'os_type='; docker info --format '{{.OSType}}' 2>/dev/null || true
  printf 'architecture='; docker info --format '{{.Architecture}}' 2>/dev/null || true
  printf 'cpus='; docker info --format '{{.NCPU}}' 2>/dev/null || true
  printf 'memory_bytes='; docker info --format '{{.MemTotal}}' 2>/dev/null || true
  printf 'docker_root_dir='; docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
  printf 'storage_driver='; docker info --format '{{.Driver}}' 2>/dev/null || true
  printf 'cgroup_driver='; docker info --format '{{.CgroupDriver}}' 2>/dev/null || true
  printf 'swarm_state='; docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || true
} >"$OUTPUT/docker/info.txt"
docker ps -a --no-trunc \
  --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Networks}}' \
  >"$OUTPUT/docker/containers.tsv"
docker image ls --no-trunc --digests \
  --format '{{.Repository}}\t{{.Tag}}\t{{.Digest}}\t{{.ID}}\t{{.Size}}' \
  >"$OUTPUT/docker/images.tsv"
docker network ls --no-trunc \
  --format '{{.Name}}\t{{.Driver}}\t{{.Scope}}\t{{.Internal}}\t{{.IPv6}}' \
  >"$OUTPUT/docker/networks.tsv"
docker volume ls --format '{{.Name}}\t{{.Driver}}\t{{.Scope}}' \
  >"$OUTPUT/docker/volumes.tsv"
printf 'name\tdriver\tmountpoint\tscope\n' >"$OUTPUT/docker/volume-details.tsv"
while IFS= read -r volume; do
  [ -n "$volume" ] || continue
  docker volume inspect --format '{{.Name}}\t{{.Driver}}\t{{.Mountpoint}}\t{{.Scope}}' "$volume" \
    >>"$OUTPUT/docker/volume-details.tsv" 2>/dev/null || true
done < <(docker volume ls -q)
printf 'name\tdriver\tinternal\tipv6\tsubnet\tgateway\n' >"$OUTPUT/docker/network-details.tsv"
while IFS= read -r network; do
  [ -n "$network" ] || continue
  docker network inspect --format '{{range .IPAM.Config}}{{$.Name}}\t{{$.Driver}}\t{{$.Internal}}\t{{$.EnableIPv6}}\t{{.Subnet}}\t{{.Gateway}}{{println}}{{end}}' "$network" \
    >>"$OUTPUT/docker/network-details.tsv" 2>/dev/null || true
done < <(docker network ls -q)
capture "$OUTPUT/docker/swarm-secrets.txt" docker secret ls --format '{{.Name}}'
capture "$OUTPUT/docker/swarm-configs.txt" docker config ls --format '{{.Name}}'
printf 'container\ttype\tsource\tdestination\tmode\trw\n' >"$OUTPUT/docker/mounts.tsv"
printf 'container\tproject\tservice\tworking_dir\tconfig_files\n' >"$OUTPUT/docker/compose-projects.tsv"

: >"$OUTPUT/replication/required-secrets.txt"
: >"$OUTPUT/replication/compose-source-files.txt"

while IFS= read -r container; do
  [ -n "$container" ] || continue
  safe=$(safe_name "$container")

  docker inspect --format \
    '{{.Name}}\t{{.Config.Image}}\t{{.Config.Hostname}}\t{{.HostConfig.RestartPolicy.Name}}\t{{.HostConfig.NetworkMode}}\t{{.State.Status}}\t{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "$container" 2>/dev/null | sed 's#^/##' >"$OUTPUT/docker/containers/${safe}.summary.tsv" || true
  docker port "$container" >"$OUTPUT/docker/containers/${safe}.ports.txt" 2>&1 || true
  docker inspect --format '{{range $name, $net := .NetworkSettings.Networks}}{{printf "%s\t%s\t%s\t%s\n" $name $net.IPAddress $net.Gateway $net.MacAddress}}{{end}}' \
    "$container" >"$OUTPUT/docker/containers/${safe}.networks.tsv" 2>/dev/null || true

  docker inspect --format '{{range .Mounts}}{{printf "%s\t%s\t%s\t%s\t%s\n" .Type .Source .Destination .Mode .RW}}{{end}}' \
    "$container" 2>/dev/null | while IFS=$'\t' read -r type source destination mode rw; do
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$container" "$type" "$source" "$destination" "$mode" "$rw"
    done >>"$OUTPUT/docker/mounts.tsv"

  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null \
    | sed 's/=.*//' | sed '/^$/d' | sort -u >"$OUTPUT/docker/env-keys/${safe}.txt"
  while IFS= read -r env_name; do
    if is_sensitive_name "$env_name"; then
      printf '%s:%s=<provide securely>\n' "$container" "$env_name"
    fi
  done <"$OUTPUT/docker/env-keys/${safe}.txt" >>"$OUTPUT/replication/required-secrets.txt"

  docker inspect --format '{{range $key, $value := .Config.Labels}}{{printf "%s\t%s\n" $key $value}}{{end}}' \
    "$container" 2>/dev/null | while IFS=$'\t' read -r key value; do
      if is_sensitive_name "$key"; then
        printf '%s=<redacted>\n' "$key"
      else
        printf '%s=%s\n' "$key" "$value"
      fi
    done | sort >"$OUTPUT/docker/labels/${safe}.txt"

  project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$container" 2>/dev/null || true)
  service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$container" 2>/dev/null || true)
  working_dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$container" 2>/dev/null || true)
  config_files=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$container" 2>/dev/null || true)
  if [ -n "$project$service$working_dir$config_files" ]; then
    printf '%s\t%s\t%s\t%s\t%s\n' "$container" "$project" "$service" "$working_dir" "$config_files" \
      >>"$OUTPUT/docker/compose-projects.tsv"
    while IFS= read -r config_file; do
      [ -n "$config_file" ] || continue
      case "$config_file" in
        /*) printf '%s\n' "$config_file" ;;
        *)
          if [ -n "$working_dir" ]; then
            printf '%s/%s\n' "${working_dir%/}" "$config_file"
          else
            printf '%s\n' "$config_file"
          fi
          ;;
      esac
    done < <(printf '%s\n' "$config_files" | tr ',' '\n') \
      >>"$OUTPUT/replication/compose-source-files.txt"
  fi
done < <(docker ps -a --format '{{.Names}}')

sort -u -o "$OUTPUT/replication/required-secrets.txt" "$OUTPUT/replication/required-secrets.txt"
sort -u -o "$OUTPUT/replication/compose-source-files.txt" "$OUTPUT/replication/compose-source-files.txt"

info "identifying platform services"
for role in traefik mailserver phpmyadmin spanel; do
  : >"$OUTPUT/services/${role}-containers.txt"
done
while IFS=$'\t' read -r name image _rest; do
  [ "$name" = "" ] && continue
  haystack=$(printf '%s %s' "$name" "$image" | tr '[:upper:]' '[:lower:]')
  case "$haystack" in *traefik*) echo "$name" >>"$OUTPUT/services/traefik-containers.txt" ;; esac
  case "$haystack" in *mailserver*|*docker-mailserver*) echo "$name" >>"$OUTPUT/services/mailserver-containers.txt" ;; esac
  case "$haystack" in *phpmyadmin*) echo "$name" >>"$OUTPUT/services/phpmyadmin-containers.txt" ;; esac
  case "$haystack" in *spanel*|*paneltutor*) echo "$name" >>"$OUTPUT/services/spanel-containers.txt" ;; esac
done <"$OUTPUT/docker/containers.tsv"

grep -RhoE 'Host\(`[^`]+`\)' "$OUTPUT/docker/labels" 2>/dev/null \
  | sed 's/^Host(`//; s/`)$//' | sort -u >"$OUTPUT/services/traefik-hostnames.txt" || true

{
  echo -e 'container\tserver_name\tnot_before\tnot_after\tissuer'
  while IFS= read -r container; do
    [ -n "$container" ] || continue
    while IFS=$'\t' read -r type source destination _mode _rw; do
      [ "$type" = "bind" ] || continue
      case "$source $destination" in
        *letsencrypt*|*cert*|*ssl*)
          if [ -d "$source" ] && have openssl; then
            while IFS= read -r cert; do
              subject=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null || true)
              [ -n "$subject" ] || continue
              start=$(openssl x509 -in "$cert" -noout -startdate 2>/dev/null | sed 's/^notBefore=//')
              end=$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null | sed 's/^notAfter=//')
              issuer=$(openssl x509 -in "$cert" -noout -issuer 2>/dev/null | sed 's/^issuer=//')
              printf '%s\t%s\t%s\t%s\t%s\n' "$container" "$subject" "$start" "$end" "$issuer"
            done < <(find "$source" -xdev -type f \( -name '*.pem' -o -name '*.crt' \) -size -2M 2>/dev/null)
          fi
          ;;
      esac
    done < <(awk -F '\t' -v c="$container" '$1 == c {print $2"\t"$3"\t"$4"\t"$5"\t"$6}' "$OUTPUT/docker/mounts.tsv")
  done < <(docker ps -a --format '{{.Names}}')
} >"$OUTPUT/services/public-certificates.tsv"

if [ "$COPY_COMPOSE" = "1" ]; then
  info "copying explicitly requested Compose files"
  mkdir -p "$OUTPUT/compose-files"
  while IFS= read -r source; do
    [ -n "$source" ] || continue
    if [ -f "$source" ]; then
      checksum=$(printf '%s' "$source" | cksum | awk '{print $1}')
      base="${checksum}-$(safe_name "$(basename "$source")")"
      cp -p -- "$source" "$OUTPUT/compose-files/$base"
      printf '%s\t%s\n' "$source" "compose-files/$base" >>"$OUTPUT/replication/copied-compose-files.tsv"
    else
      printf '%s\tmissing-or-not-readable\n' "$source" >>"$OUTPUT/replication/copied-compose-files.tsv"
    fi
  done <"$OUTPUT/replication/compose-source-files.txt"
  warn "review copied Compose files for inline secrets before sharing the report"
fi

cat >"$OUTPUT/replication/README.md" <<'EOF'
# Plan de réplica

Este paquete es un **inventario**, no un backup. No contiene valores de entorno,
claves privadas, correo, bases de datos ni contenido de volúmenes.

## Orden recomendado

1. Preparar un host con la misma arquitectura, versión compatible de Docker,
   puertos y reglas de firewall descritos en `host/`.
2. Recrear las redes de `docker/networks.tsv`, especialmente las marcadas como
   externas en los proyectos Compose.
3. Recuperar los YAML originales indicados por
   `replication/compose-source-files.txt`; si se usó `--copy-compose`, revisar
   `compose-files/` y eliminar secretos inline antes de transportarlos.
4. Suministrar por un canal seguro las variables listadas en
   `replication/required-secrets.txt`. No reutilizar secretos débiles.
5. Crear los volúmenes de `docker/volumes.tsv` y restaurar sus datos desde un
   backup consistente. PostgreSQL/MariaDB deben respaldarse con su herramienta
   nativa o detenerse antes de copiar sus volúmenes.
6. Copiar los bind mounts de `docker/mounts.tsv` conservando propietario,
   permisos, ACL y atributos extendidos. No copiar `/var/run/docker.sock`.
7. Restaurar Traefik y PanelTutor; comprobar HTTPS. Después restaurar
   docker-mailserver y comprobar SMTP/Submission/IMAPS.
8. Cambiar DNS solo después de ejecutar las verificaciones del host nuevo.

## Datos especialmente sensibles

- `.env`, Docker secrets y credenciales de bases de datos.
- `/etc/letsencrypt`, `acme.json`, claves TLS y claves DKIM.
- volúmenes de correo y bases de datos.
- claves de cifrado de Apps/PanelTutor.

Transfiéralos cifrados. `acme.json` debe conservar permisos `0600`. Antes del
cambio definitivo, valide MX, SPF, DKIM, DMARC y PTR/reverse DNS.
EOF

cat >"$OUTPUT/replication/verify-target.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
command -v docker >/dev/null 2>&1 || { echo 'FAIL: docker missing'; exit 1; }
docker info >/dev/null 2>&1 || { echo 'FAIL: Docker daemon unavailable'; exit 1; }
echo 'Docker containers:'
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
echo
echo 'Listening TCP ports:'
ss -lnt 2>/dev/null || true
echo
echo 'Expected public services: HTTP 80/443; mail 25/465/587/993 as configured.'
echo 'Run external SMTP, IMAP, HTTPS, DNS and certificate checks before changing DNS.'
EOF
chmod 700 "$OUTPUT/replication/verify-target.sh"

cat >"$OUTPUT/README.txt" <<EOF
SPanel/PanelTutor infrastructure inventory
Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Host: $(hostname -f 2>/dev/null || hostname)

This directory is private (0700). It contains no environment values by design,
but hostnames, paths, IP addresses and topology may still be sensitive.
Start with replication/README.md.
EOF

chmod -R go-rwx "$OUTPUT"

if [ "$MAKE_ARCHIVE" = "1" ]; then
  info "creating private archive"
  parent=$(dirname "$OUTPUT")
  leaf=$(basename "$OUTPUT")
  tar -C "$parent" -czf "$OUTPUT.tar.gz" "$leaf"
  chmod 600 "$OUTPUT.tar.gz"
  echo "archive: $OUTPUT.tar.gz"
fi

echo "inventory: $OUTPUT"
echo "containers: $(wc -l <"$OUTPUT/docker/containers.tsv" | tr -d ' ')"
echo "volumes:    $(wc -l <"$OUTPUT/docker/volumes.tsv" | tr -d ' ')"
echo "next:       read $OUTPUT/replication/README.md"
