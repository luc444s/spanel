#!/bin/sh
# Deploy hook de Certbot para el agente de Spanel (A.SPEC 0036).
#
# Certbot lo invoca después de renovar; solo sincroniza el linaje renovado.
# No ejecuta `certbot renew` ni `certbot certonly`, no usa eval y no acepta
# entrada del usuario: el hostname sale de RENEWED_LINEAGE y se valida aquí.
set -eu

if [ -z "${RENEWED_LINEAGE:-}" ]; then
    exit 0
fi

hostname=$(basename "$RENEWED_LINEAGE")
case "$hostname" in
    "" | *[!a-z0-9.-]* | .* | *..* | -* | .-* | *- | *.-)
        # Nombre inesperado: no se pasa nada al agente.
        exit 0
        ;;
esac

PYTHONPATH="${SPANEL_AGENT_PYTHONPATH:-/opt/spanel/scripts}"
export PYTHONPATH

exec /usr/bin/python3 -m spanel_cert_agent sync \
    --hostname "$hostname" \
    --config "${SPANEL_CERT_AGENT_CONFIG:-/etc/spanel/cert-agent.toml}"
