"""Lectura de salud y alertas publicadas por el agente (A.SPEC 0036).

La API no sondea TLS ni recarga servicios: solo proyecta `health` de
`state.json` y `alerts.json`, filtrando por tenant. Todo es metadato; cualquier
mensaje con material privado se descarta.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plugins.domains.backend.certificates.config import CertificatesConfig
from plugins.domains.backend.certificates.inventory import Inventory
from plugins.domains.backend.certificates.models import Certificate
from plugins.domains.backend.certificates.validation import (
    contains_secret_material,
    parse_datetime,
)

SEVERITIES = ("warning", "critical")


def _safe_text(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if contains_secret_material(value):
        return "Detalle no disponible"
    return value.strip()[:limit]


def _days_left(not_after: str | None) -> int | None:
    parsed = parse_datetime(not_after)
    if parsed is None:
        return None
    return int((parsed - datetime.now(UTC)).total_seconds() // 86400)


def _fallback(cert: Certificate) -> dict[str, Any]:
    return {
        "hostname": cert.hostname,
        "serial": cert.serial,
        "served_serial": None,
        "no_sni_serial": None,
        "not_after": cert.not_after,
        "days_left": _days_left(cert.not_after),
        "sni": {},
        "no_sni": None,
        "no_sni_ports": {},
        "ok": None,
        "last_verified_at": cert.last_checked_at,
        "last_ok_at": None,
        "consecutive_failures": 0,
        "last_error": cert.last_error,
    }


def _normalize_certificate(item: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    sni = item.get("sni") if isinstance(item.get("sni"), dict) else {}
    ports = item.get("no_sni_ports") if isinstance(item.get("no_sni_ports"), dict) else {}
    no_sni = item.get("no_sni")
    if isinstance(no_sni, dict):
        ports = no_sni
        no_sni = all(bool(value) for value in ports.values()) if ports else None
    return {
        "hostname": fallback["hostname"],
        "serial": _safe_text(item.get("serial")) or fallback["serial"],
        "served_serial": _safe_text(item.get("served_serial")),
        "no_sni_serial": _safe_text(item.get("no_sni_serial")),
        "not_after": _safe_text(item.get("not_after")) or fallback["not_after"],
        "days_left": (
            item.get("days_left")
            if isinstance(item.get("days_left"), int)
            else fallback["days_left"]
        ),
        "sni": {str(port): bool(ok) for port, ok in sni.items()},
        "no_sni": no_sni if isinstance(no_sni, bool) else None,
        "no_sni_ports": {str(port): bool(ok) for port, ok in ports.items()},
        "ok": item.get("ok") if isinstance(item.get("ok"), bool) else fallback["ok"],
        "last_verified_at": _safe_text(item.get("last_verified_at")),
        "last_ok_at": _safe_text(item.get("last_ok_at")),
        "consecutive_failures": (
            item.get("consecutive_failures")
            if isinstance(item.get("consecutive_failures"), int)
            else 0
        ),
        "last_error": _safe_text(item.get("last_error")),
    }


def _severity(item: dict[str, Any], config: CertificatesConfig) -> str | None:
    remaining = item.get("days_left")
    if item.get("ok") is False:
        return "critical"
    if isinstance(remaining, int):
        if remaining <= config.alert_critical_days:
            return "critical"
        if remaining <= config.alert_warning_days:
            return "warning"
    return None


def health_view(
    inventory: Inventory,
    *,
    visible_hostnames: set[str],
    is_platform: bool,
    config: CertificatesConfig,
) -> dict[str, Any]:
    raw = inventory.health if isinstance(inventory.health, dict) else {}
    raw_entries = raw.get("certificates") if isinstance(raw.get("certificates"), list) else []
    by_host = {
        str(item.get("hostname")): item
        for item in raw_entries
        if isinstance(item, dict) and item.get("hostname")
    }
    certificates: list[dict[str, Any]] = []
    for cert in inventory.certificates:
        if cert.hostname not in visible_hostnames:
            continue
        if cert.scope == "global" or cert.hostname == config.default_name:
            continue
        fallback = _fallback(cert)
        certificates.append(_normalize_certificate(by_host.get(cert.hostname) or {}, fallback))

    default = None
    if is_platform and isinstance(raw.get("default"), dict):
        source = raw["default"]
        default = {
            "name": _safe_text(source.get("name")) or config.default_name,
            "serial": _safe_text(source.get("serial")),
            "no_sni_serial": _safe_text(source.get("no_sni_serial")),
            "ok": source.get("ok") if isinstance(source.get("ok"), bool) else None,
        }

    agent_raw = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    critical = sum(1 for item in certificates if _severity(item, config) == "critical")
    warning = sum(1 for item in certificates if _severity(item, config) == "warning")
    if default is not None and default.get("ok") is False:
        critical += 1
    return {
        "generated_at": inventory.generated_at,
        "agent_state": (
            _safe_text(agent_raw.get("state")) or ("unknown" if inventory.stale else "ok")
        ),
        "agent_last_run_at": (
            _safe_text(agent_raw.get("last_run_at")) or inventory.agent_last_seen
        ),
        "interval_seconds": (
            agent_raw.get("interval_seconds")
            if isinstance(agent_raw.get("interval_seconds"), int)
            else config.state_max_age_seconds
        ),
        "stale": inventory.stale,
        "default": default,
        "certificates": certificates,
        "critical_count": critical,
        "warning_count": warning,
    }


def load_alerts(config: CertificatesConfig) -> dict[str, Any]:
    path: Path = config.alerts_path
    if not path.exists():
        return {"generated_at": None, "alerts": [], "delivery_errors": []}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"generated_at": None, "alerts": [], "delivery_errors": []}
    if contains_secret_material(text):
        return {"generated_at": None, "alerts": [], "delivery_errors": []}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"generated_at": None, "alerts": [], "delivery_errors": []}
    if not isinstance(payload, dict):
        return {"generated_at": None, "alerts": [], "delivery_errors": []}
    alerts = payload.get("alerts") if isinstance(payload.get("alerts"), list) else []
    return {
        "generated_at": payload.get("generated_at"),
        "alerts": [item for item in alerts if isinstance(item, dict)],
        "delivery_errors": payload.get("delivery_errors") or [],
    }


def alert_view(
    payload: dict[str, Any],
    *,
    visible_hostnames: set[str],
    default_name: str,
    is_platform: bool,
) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    for item in payload.get("alerts", []):
        hostname = _safe_text(item.get("hostname"))
        if hostname is None:
            if not is_platform:
                continue
        elif not is_platform:
            if hostname == default_name or hostname not in visible_hostnames:
                continue
        severity = item.get("severity") if item.get("severity") in SEVERITIES else "warning"
        alerts.append(
            {
                "code": _safe_text(item.get("code")) or "unknown_alert",
                "severity": severity,
                "hostname": hostname,
                "message": _safe_text(item.get("message")) or "Alerta sin detalle",
                "created_at": _safe_text(item.get("created_at")),
            }
        )
    errors = payload.get("delivery_errors")
    return {
        "generated_at": payload.get("generated_at"),
        "alerts": alerts,
        "delivery_errors": (
            [
                value
                for value in errors
                if isinstance(value, str)
            ]
            if isinstance(errors, list)
            else []
        )
        if is_platform
        else [],
    }
