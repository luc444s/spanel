#!/bin/bash
# SP-0020 escenario 3: crear docker-mailserver externo + buzones de prueba
set -euo pipefail

SSH_USER="${SPANEL_DOCKER_SSH_USER:-lucas}"
SSH_HOST="${SPANEL_DOCKER_SSH_HOST:-100.67.5.50}"
SSH_PASS="${SPANEL_DOCKER_SSH_PASSWORD:?SPANEL_DOCKER_SSH_PASSWORD required}"

sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=accept-new "$SSH_USER@$SSH_HOST" "
  docker rm -f spanel-test-mail 2>/dev/null || true
  docker run -d --name spanel-test-mail --restart unless-stopped \
    --hostname mail.spanel-test.local \
    -e ENABLE_FAIL2BAN=0 -e ENABLE_POP3=1 -e ONE_DIR=1 \
    -e POSTMASTER_ADDRESS=postmaster@spanel-test.local \
    ghcr.io/docker-mailserver/docker-mailserver:latest >/dev/null
"
echo "[3] mailserver spanel-test-mail creado"
