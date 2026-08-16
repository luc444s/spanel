#!/bin/bash
# SP-0020 escenario 1: crear container docker con WP minimo (externo a Spanel)
set -euo pipefail

SSH_USER="${SPANEL_DOCKER_SSH_USER:-lucas}"
SSH_HOST="${SPANEL_DOCKER_SSH_HOST:-100.67.5.50}"
SSH_PASS="${SPANEL_DOCKER_SSH_PASSWORD:?SPANEL_DOCKER_SSH_PASSWORD required}"

sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=accept-new "$SSH_USER@$SSH_HOST" "
  docker network create spanel-test 2>/dev/null || true
  docker rm -f spanel-test-wp spanel-test-db 2>/dev/null || true
  docker volume rm -f spanel-test-db spanel-test-wp 2>/dev/null || true
  docker volume create spanel-test-db >/dev/null
  docker volume create spanel-test-wp >/dev/null
  docker run -d --name spanel-test-db --restart unless-stopped \
    --network spanel-test \
    -e MARIADB_DATABASE=wordpress -e MARIADB_USER=wp \
    -e MARIADB_PASSWORD=testpassword123 -e MARIADB_ROOT_PASSWORD=rootpw123 \
    -v spanel-test-db:/var/lib/mysql \
    mariadb:11 >/dev/null
  sleep 15
  docker run -d --name spanel-test-wp --restart unless-stopped \
    -e WORDPRESS_DB_HOST=spanel-test-db -e WORDPRESS_DB_USER=wp \
    -e WORDPRESS_DB_PASSWORD=testpassword123 -e WORDPRESS_DB_NAME=wordpress \
    -v spanel-test-wp:/var/www/html \
    --network spanel-test \
    wordpress:php8.3-apache >/dev/null
  sleep 12
  docker run --rm --network spanel-test \
    -v spanel-test-wp:/var/www/html \
    -e WORDPRESS_DB_HOST=spanel-test-db -e WORDPRESS_DB_USER=wp \
    -e WORDPRESS_DB_PASSWORD=testpassword123 -e WORDPRESS_DB_NAME=wordpress \
    wordpress:cli wp core install \
      --url=http://spanel-test-wp --title=TestWP \
      --admin_user=admin --admin_password=admin123 --admin_email=admin@test.local \
      --skip-email --allow-root
  docker ps --format '{{.Names}}' | grep -x spanel-test-wp
"
echo "[1] WP minimo spanel-test-wp creado + instalado"
