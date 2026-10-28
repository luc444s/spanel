"""Validación de hostnames, fechas y payloads del gestor de certificados.

Ningún dato que venga de la web llega a un argv: esta capa normaliza y rechaza
antes de que la solicitud se escriba. El agente repite la validación por su
cuenta, porque el directorio de solicitudes es escribible por la web.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)
JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SECRET_MARKERS = ("PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN EC PRIVATE KEY")
ACTION_VALUES = ("ensure", "force")
REQUEST_KEYS = frozenset(
    {"id", "hostname", "action", "requested_by", "requested_at", "reason"}
)
REFRESH_KEYS = frozenset({"id", "action", "requested_by", "requested_at", "reason"})


class CertificateValidationError(ValueError):
    """Dato inválido en un hostname, job o payload."""


def normalize_hostname(value: str) -> str:
    candidate = (value or "").strip().lower().rstrip(".")
    if not candidate or len(candidate) > 253 or not HOSTNAME_RE.match(candidate):
        raise CertificateValidationError("Hostname inválido")
    return candidate


def is_valid_job_id(value: str) -> bool:
    return bool(JOB_ID_RE.match((value or "").strip().lower()))


def normalize_job_id(value: str) -> str:
    candidate = (value or "").strip().lower()
    if not is_valid_job_id(candidate):
        raise CertificateValidationError("Job inválido")
    return candidate


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def contains_secret_material(text: str) -> bool:
    """Detecta contenido de clave privada sin inspeccionar su valor."""

    upper = text.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def validate_emission_request(
    payload: object,
    *,
    allowed_keys: frozenset[str] = REQUEST_KEYS,
    default_name: str = "cf-mail",
) -> dict[str, Any]:
    """Valida una solicitud de emisión leída de disco.

    Rechaza claves extra (por ejemplo `args`, `--force-renewal` o `env`) y
    cualquier hostname fuera de la forma esperada. La allowlist se aplica en
    otra capa, contra el inventario del agente.
    """

    if not isinstance(payload, dict):
        raise CertificateValidationError("La solicitud debe ser un objeto JSON")
    unknown = set(payload) - set(allowed_keys)
    if unknown:
        raise CertificateValidationError(
            f"La solicitud contiene campos no permitidos: {sorted(unknown)}"
        )
    hostname = normalize_hostname(str(payload.get("hostname", "")))
    if hostname == default_name:
        raise CertificateValidationError("El certificado global no se regenera desde la web")
    action = str(payload.get("action", "")).strip().lower()
    if action not in ACTION_VALUES:
        raise CertificateValidationError("Acción inválida")
    reason = payload.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or len(reason) > 200:
            raise CertificateValidationError("Motivo inválido")
        if any(ord(char) < 32 for char in reason):
            raise CertificateValidationError("Motivo inválido")
    request_id = payload.get("id")
    if request_id is not None and not isinstance(request_id, str):
        raise CertificateValidationError("Identificador de solicitud inválido")
    return {
        "id": request_id,
        "hostname": hostname,
        "action": action,
        "requested_by": payload.get("requested_by"),
        "requested_at": payload.get("requested_at"),
        "reason": reason,
    }


def validate_refresh_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CertificateValidationError("La solicitud debe ser un objeto JSON")
    unknown = set(payload) - set(REFRESH_KEYS)
    if unknown:
        raise CertificateValidationError(
            f"La solicitud contiene campos no permitidos: {sorted(unknown)}"
        )
    if str(payload.get("action", "")).strip().lower() != "refresh":
        raise CertificateValidationError("Acción inválida")
    return dict(payload)
