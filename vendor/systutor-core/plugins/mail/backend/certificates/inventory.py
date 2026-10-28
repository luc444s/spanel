"""Lectura y validación de `/var/lib/spanel/cert-manager/state.json`.

El archivo lo escribe el agente como root y solo contiene metadatos. Un estado
que incluya material de clave privada se rechaza completo: la API nunca sirve
un documento que no pasó esta comprobación.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plugins.mail.backend.certificates.config import CertificatesConfig
from plugins.mail.backend.certificates.models import Certificate, utcnow
from plugins.mail.backend.certificates.validation import (
    CertificateValidationError,
    contains_secret_material,
    normalize_hostname,
    parse_datetime,
)
from systutor.core.errors import AppError


class InventoryError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            status_code=503,
            code="mail.certificates_unavailable",
        )


@dataclass(slots=True)
class Inventory:
    certificates: list[Certificate] = field(default_factory=list)
    stale: bool = True
    generated_at: str | None = None
    agent_last_seen: str | None = None
    health: dict[str, Any] | None = None
    error: str | None = None

    def find(self, hostname: str) -> Certificate | None:
        needle = hostname.strip().lower()
        return next((cert for cert in self.certificates if cert.hostname == needle), None)


def _stale(
    payload: dict[str, Any],
    certificates: list[Certificate],
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    reference = parse_datetime(payload.get("generated_at"))
    if reference is None:
        reference = parse_datetime(payload.get("agent", {}).get("last_seen")) if isinstance(
            payload.get("agent"), dict
        ) else None
    if reference is None:
        checked = [parse_datetime(cert.last_checked_at) for cert in certificates]
        known = [item for item in checked if item is not None]
        reference = max(known) if known else None
    if reference is None:
        return True
    # El criterio de la A.SPEC 0036 es "más de 2 intervalos sin ejecutarse".
    # Si el agente publica su intervalo, ese valor manda; si no, se usa el
    # máximo configurado.
    agent = payload.get("agent")
    interval = agent.get("interval_seconds") if isinstance(agent, dict) else None
    if not isinstance(interval, int) or interval <= 0:
        health = payload.get("health")
        health_agent = health.get("agent") if isinstance(health, dict) else None
        interval = (
            health_agent.get("interval_seconds") if isinstance(health_agent, dict) else None
        )
    limit = max_age_seconds
    if isinstance(interval, int) and interval > 0:
        limit = max(limit, 2 * interval)
    return (now - reference).total_seconds() > limit


def load_inventory(
    config: CertificatesConfig,
    *,
    now: datetime | None = None,
) -> Inventory:
    reference = now or utcnow()
    path = config.state_path
    if not path.exists():
        return Inventory(stale=True, error="state_missing")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise InventoryError("No se pudo leer el estado de certificados") from None

    if contains_secret_material(text):
        # No se loguea ni se devuelve el contenido: solo el rechazo.
        raise InventoryError("El estado de certificados contiene material privado")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise InventoryError("El estado de certificados no es JSON válido") from None

    if isinstance(payload, list):
        payload = {"certificates": payload}
    if not isinstance(payload, dict):
        raise InventoryError("El estado de certificados tiene un formato inválido")

    raw_items = payload.get("certificates") or payload.get("items") or []
    if not isinstance(raw_items, list):
        raise InventoryError("El estado de certificados tiene un formato inválido")

    certificates: list[Certificate] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            hostname = normalize_hostname(
                str(item.get("hostname") or item.get("name") or "")
            )
        except CertificateValidationError:
            continue
        if hostname in seen:
            continue
        try:
            certificate = Certificate.from_payload(
                {**item, "hostname": hostname},
                default_name=config.default_name,
                now=reference,
                renew_days=config.renew_days,
            )
        except ValueError:
            continue
        seen.add(hostname)
        certificates.append(certificate)

    certificates.sort(key=lambda cert: (cert.scope != "global", cert.hostname))
    agent = payload.get("agent") if isinstance(payload.get("agent"), dict) else {}
    generated_at = payload.get("generated_at")
    last_seen = agent.get("last_seen") if isinstance(agent, dict) else None
    health = payload.get("health") if isinstance(payload.get("health"), dict) else None
    return Inventory(
        certificates=certificates,
        stale=_stale(
            payload,
            certificates,
            now=reference,
            max_age_seconds=config.state_max_age_seconds,
        ),
        generated_at=generated_at if isinstance(generated_at, str) else None,
        agent_last_seen=last_seen if isinstance(last_seen, str) else None,
        health=health,
        error=None,
    )


def state_age_seconds(path: Path) -> float | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return datetime.now(UTC).timestamp() - mtime
