"""Alertas de salud con canal externo independiente del correo vigilado.

Las alertas se evalúan desde el estado de salud (solo metadatos) y se publican
por un webhook configurado (`alerts.webhook_command` o `alerts.webhook_url`).
Nunca se envía nada por el SMTP vigilado (I6).
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig
from .health import now_iso, parse_iso
from .state import atomic_write_json, read_json
from .validation import safe_error

SEVERITY_RANK = {"warning": 1, "critical": 2}
RESEND_AFTER_SECONDS = 6 * 3600
IMAP_PORT = 993
logger = logging.getLogger(__name__)


class AlertChannelError(Exception):
    """El canal externo de alertas no pudo entregar el mensaje."""


def _alert(code: str, severity: str, message: str, hostname: str | None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "hostname": hostname,
        "message": message,
        "created_at": now_iso(),
    }


def _expiry_alert(entry: dict, config: AgentConfig) -> dict | None:
    remaining = entry.get("days_left")
    hostname = str(entry.get("hostname") or "")
    if not isinstance(remaining, int):
        return None
    if remaining <= 0:
        return _alert("certificate_expired", "critical", "El certificado está vencido", hostname)
    if remaining <= config.alerts.critical_days:
        return _alert(
            "certificate_expiry_critical",
            "critical",
            f"Vence en {remaining} días",
            hostname,
        )
    if remaining <= config.alerts.warning_days:
        return _alert(
            "certificate_expiry_warning",
            "warning",
            f"Vence en {remaining} días",
            hostname,
        )
    return None


def _service_alert(entry: dict) -> dict | None:
    hostname = str(entry.get("hostname") or "")
    sni = entry.get("sni") if isinstance(entry.get("sni"), dict) else {}
    failed = {str(port) for port, ok in sni.items() if not ok}
    if not failed:
        return None
    imap_failed = str(IMAP_PORT) in failed
    smtp_failed = bool(failed - {str(IMAP_PORT)})
    if imap_failed and smtp_failed:
        return _alert(
            "mail_tls_failure",
            "critical",
            "Fallan Postfix y Dovecot; se requiere rollback de ambos",
            hostname,
        )
    if imap_failed:
        return _alert(
            "dovecot_failure", "critical", "Falló la verificación de Dovecot (993)", hostname
        )
    return _alert(
        "postfix_failure", "critical", "Falló la verificación de Postfix (465/587)", hostname
    )


def _entry_alerts(entry: dict, config: AgentConfig) -> list[dict]:
    hostname = str(entry.get("hostname") or "")
    alerts: list[dict] = []
    expiry = _expiry_alert(entry, config)
    if expiry is not None:
        alerts.append(expiry)

    error = str(entry.get("last_error") or "")
    lowered = error.lower()
    if "san" in lowered:
        alerts.append(
            _alert("certificate_san_mismatch", "critical", error, hostname)
        )
    if "clave" in lowered or "key" in lowered:
        alerts.append(
            _alert("certificate_key_mismatch", "critical", error or "Clave incompatible", hostname)
        )
    served = entry.get("served_serial")
    stored = entry.get("serial")
    if served and stored and served != stored:
        alerts.append(
            _alert(
                "certificate_serial_mismatch",
                "critical",
                "El serial servido no coincide con el almacenado",
                hostname,
            )
        )
    service = _service_alert(entry)
    if service is not None:
        alerts.append(service)
    if entry.get("no_sni") is False or entry.get("no_sni_ok") is False:
        alerts.append(
            _alert(
                "default_certificate_mismatch",
                "critical",
                "El certificado sin SNI no coincide con cf-mail",
                hostname,
            )
        )
    if not entry.get("ok") and not alerts:
        alerts.append(
            _alert("certificate_unhealthy", "critical", error or "Certificado no saludable", hostname)
        )
    return alerts


def evaluate_alerts(
    health: dict,
    config: AgentConfig,
    *,
    previous_generated_at: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    reference = now or datetime.now(timezone.utc)
    alerts: list[dict] = []
    for entry in health.get("certificates", []):
        if isinstance(entry, dict):
            alerts.extend(_entry_alerts(entry, config))

    default = health.get("default") if isinstance(health.get("default"), dict) else {}
    if default and not default.get("ok"):
        alerts.append(
            _alert(
                "default_certificate_mismatch",
                "critical",
                "El certificado predeterminado cf-mail no se sirve sin SNI",
                str(default.get("name") or config.default_name),
            )
        )

    generated = parse_iso(previous_generated_at)
    interval = config.verification.audit_interval_seconds
    if generated is None:
        alerts.append(
            _alert("agent_stale", "critical", "El agente no publicó estado previo", None)
        )
    elif (reference - generated).total_seconds() > 2 * interval:
        alerts.append(
            _alert(
                "agent_stale",
                "critical",
                "El agente lleva más de dos intervalos sin ejecutarse",
                None,
            )
        )

    install = health.get("install") if isinstance(health.get("install"), dict) else {}
    for hostname, entry in install.items():
        if not isinstance(entry, dict) or int(entry.get("failures") or 0) <= 0:
            continue
        alerts.append(
            _alert(
                "install_failed",
                "critical",
                safe_error(entry.get("last_error")) or "La instalación falló y se reintentará con backoff",
                str(hostname),
            )
        )
    return alerts


def _default_sender(alert: dict, config: AgentConfig, runner=None) -> None:
    if not config.alerts.configured():
        raise AlertChannelError("Canal de alertas no configurado")
    payload = json.dumps({"source": "spanel-cert-agent", "alert": alert}, sort_keys=True).encode()
    if config.alerts.webhook_command:
        call = runner or _run
        process = call(list(config.alerts.webhook_command), payload)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or b"").decode("utf-8", "replace")
            raise AlertChannelError(safe_error(detail) or "El webhook falló")
        return
    url = config.alerts.webhook_url
    if not url:
        raise AlertChannelError("Canal de alertas no configurado")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.alerts.timeout_seconds):
            pass
    except (urllib.error.URLError, OSError) as exc:
        raise AlertChannelError("No se pudo publicar la alerta externa") from exc


def _run(argv: Sequence[str], input_data: bytes | None = None):
    return subprocess.run(list(argv), input=input_data, capture_output=True, check=False)


Sender = Callable[[dict], None]


def _due(alerts: list[dict], sent: dict, reference: datetime) -> list[dict]:
    due: list[dict] = []
    for alert in alerts:
        key = f"{alert.get('code')}:{alert.get('hostname') or 'global'}"
        previous = sent.get(key) if isinstance(sent, dict) else None
        stamp = parse_iso(previous.get("sent_at") if isinstance(previous, dict) else None)
        severity = previous.get("severity") if isinstance(previous, dict) else None
        changed = severity != alert.get("severity")
        expired = stamp is None or (reference - stamp).total_seconds() >= RESEND_AFTER_SECONDS
        if changed or expired:
            due.append(alert)
    return due


def publish_alerts(
    config: AgentConfig,
    health: dict,
    *,
    previous_state: dict | None = None,
    runner=None,
    sender: Sender | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> dict:
    """Evalúa, persiste y entrega alertas. Devuelve el documento escrito."""

    reference = now or datetime.now(timezone.utc)
    previous_alerts = read_json(path or config.alerts_path) or {}
    sent = previous_alerts.get("last_sent") if isinstance(previous_alerts, dict) else {}
    sent = sent if isinstance(sent, dict) else {}
    previous_generated = (previous_state or {}).get("generated_at")
    alerts = evaluate_alerts(
        health,
        config,
        previous_generated_at=previous_generated if isinstance(previous_generated, str) else None,
        now=reference,
    )

    delivery: list[dict] = []
    failures: list[str] = []
    channel_ready = sender is not None or config.alerts.configured()
    deliver = sender or (lambda alert: _default_sender(alert, config, runner=runner))
    if channel_ready:
        for alert in _due(alerts, sent, reference):
            try:
                deliver(alert)
            except AlertChannelError as exc:
                failures.append(str(exc))
                logger.warning(
                    "cert_alert_delivery_failed",
                    extra={"code": alert.get("code"), "hostname": alert.get("hostname")},
                )
                continue
            key = f"{alert.get('code')}:{alert.get('hostname') or 'global'}"
            sent[key] = {"sent_at": now_iso(), "severity": alert.get("severity")}
            delivery.append(alert)

    document = {
        "generated_at": now_iso(),
        "alerts": alerts,
        "last_sent": sent,
        "delivery_errors": failures,
    }
    atomic_write_json(path or config.alerts_path, document, mode=0o640)
    return document
