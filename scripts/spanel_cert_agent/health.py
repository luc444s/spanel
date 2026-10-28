"""Estado de salud agregado de los certificados de correo.

Solo metadatos: seriales, fechas, booleanos por puerto y último error. El
módulo nunca escribe certificados ni recarga servicios (I1). La persistencia de
`last_verified_at`, `consecutive_failures` y backoff se toma del estado previo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AgentConfig
from .probe import verify_hostname
from .validation import (
    CertificateValidationError,
    _hostname_in_sans,
    safe_error,
    validate_pair,
)

MAX_BACKOFF_SECONDS = 3600


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: object) -> datetime | None:
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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def days_left(not_after: object, *, now: datetime) -> int | None:
    parsed = parse_iso(not_after)
    if parsed is None:
        return None
    return int((parsed - now).total_seconds() // 86400)


def previous_health(state: dict | None) -> dict[str, dict]:
    if not isinstance(state, dict):
        return {}
    health = state.get("health")
    if not isinstance(health, dict):
        return {}
    items = health.get("certificates")
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("hostname")): item
        for item in items
        if isinstance(item, dict) and item.get("hostname")
    }


def previous_install(state: dict | None) -> dict[str, dict]:
    if not isinstance(state, dict):
        return {}
    health = state.get("health")
    if not isinstance(health, dict):
        return {}
    install = health.get("install")
    if not isinstance(install, dict):
        return {}
    return {str(key): value for key, value in install.items() if isinstance(value, dict)}


def _entry_ok(entry: dict) -> bool:
    return str(entry.get("status")) in ("active", "expiring")


def _material_error(
    config: AgentConfig,
    hostname: str,
    entry: dict,
    *,
    runner=None,
    now: datetime,
) -> str | None:
    """Comprueba clave/cadena/SAN/vigencia del par instalado (solo lectura)."""

    if str(entry.get("status")) in ("missing", "error"):
        return safe_error(entry.get("last_error")) or "Certificado ausente o ilegible"
    cert = Path(str(entry.get("cert_path") or ""))
    key = Path(str(entry.get("key_path") or ""))
    if not cert.is_file() or not key.is_file():
        return "Faltan archivos del par instalado"
    kwargs = {"runner": runner} if runner is not None else {}
    try:
        info = validate_pair(
            cert,
            key,
            hostname,
            ca_file=config.verification.ca_file,
            now=now,
            **kwargs,
        )
    except CertificateValidationError as exc:
        return safe_error(str(exc)) or "Par inválido"
    if not _hostname_in_sans(hostname, info.sans):
        return "SAN incorrecto"
    return None


def _probe_health(
    config: AgentConfig,
    hostname: str,
    *,
    serial: str | None,
    default_serial: str | None,
    connector=None,
) -> dict[str, object]:
    return verify_hostname(
        hostname,
        expected_serial=serial,
        ca_file=config.verification.ca_file,
        ports=config.verification.ports,
        timeout=config.verification.timeout_seconds,
        no_sni_expected_serial=default_serial,
        connector=connector,
    )


def build_health(
    config: AgentConfig,
    inventory: dict,
    *,
    previous: dict | None = None,
    connector=None,
    runner=None,
    now: datetime | None = None,
) -> dict:
    reference = now or datetime.now(timezone.utc)
    stamp = now_iso()
    prior = previous_health(previous)
    entries = [
        item
        for item in inventory.get("certificates", [])
        if isinstance(item, dict) and item.get("hostname")
    ]
    default_entry = next(
        (item for item in entries if str(item.get("hostname")) == config.default_name), None
    )
    default_serial = (default_entry or {}).get("serial")

    certificates: list[dict[str, object]] = []
    for entry in entries:
        hostname = str(entry["hostname"])
        if hostname == config.default_name:
            continue
        before = prior.get(hostname) or {}
        serial = entry.get("serial")
        error = _material_error(config, hostname, entry, runner=runner, now=reference)
        probes = _probe_health(
            config,
            hostname,
            serial=serial if isinstance(serial, str) else None,
            default_serial=default_serial if isinstance(default_serial, str) else None,
            connector=connector,
        )
        probe_errors = [
            str(item.get("error"))
            for item in list(probes["details"].values()) + list(probes["no_sni_details"].values())
            if item.get("error")
        ]
        ok = (
            error is None
            and bool(probes["sni_ok"])
            and bool(probes["no_sni_ok"])
            and _entry_ok(entry)
        )
        failures = int(before.get("consecutive_failures") or 0)
        failures = 0 if ok else failures + 1
        previous_ok_at = before.get("last_ok_at")
        certificates.append(
            {
                "hostname": hostname,
                "serial": serial,
                "served_serial": probes.get("served_serial"),
                "no_sni_serial": probes.get("no_sni_serial"),
                "not_after": entry.get("not_after"),
                "days_left": days_left(entry.get("not_after"), now=reference),
                "sni": probes["sni"],
                "no_sni": bool(probes["no_sni_ok"]),
                "no_sni_ports": probes["no_sni"],
                "no_sni_ok": probes["no_sni_ok"],
                "ok": ok,
                "last_verified_at": stamp,
                "last_ok_at": stamp if ok else previous_ok_at,
                "consecutive_failures": failures,
                "last_error": error
                or (safe_error(probe_errors[0]) if probe_errors else None),
            }
        )

    default_no_sni = None
    default_ok = bool(default_entry) and _entry_ok(default_entry)
    if default_entry is not None and default_serial:
        default_probe = verify_hostname(
            config.default_name,
            expected_serial=default_serial if isinstance(default_serial, str) else None,
            ca_file=config.verification.ca_file,
            ports=(config.verification.ports[0],),
            timeout=config.verification.timeout_seconds,
            no_sni_expected_serial=default_serial if isinstance(default_serial, str) else None,
            connector=connector,
        )
        default_no_sni = default_probe.get("no_sni_serial")
        default_ok = default_ok and bool(default_probe["no_sni_ok"])

    now_ok = all(bool(item["ok"]) for item in certificates) and default_ok
    return {
        "generated_at": stamp,
        "agent": {
            "state": "ok" if now_ok else "degraded",
            "last_run_at": stamp,
            "interval_seconds": config.verification.audit_interval_seconds,
            "source": config.source,
        },
        "default": {
            "name": config.default_name,
            "serial": default_serial,
            "no_sni_serial": default_no_sni,
            "ok": default_ok,
        },
        "certificates": certificates,
        "install": previous_install(previous),
    }


def next_backoff_seconds(failures: int) -> int:
    """Backoff exponencial acotado: nunca reintentos infinitos."""

    if failures <= 0:
        return 0
    return min(MAX_BACKOFF_SECONDS, 60 * (2 ** min(failures - 1, 6)))


def register_install_failure(
    config: AgentConfig, health: dict, hostname: str, *, error: str
) -> dict:
    install = health.setdefault("install", {})
    current = install.get(hostname) if isinstance(install.get(hostname), dict) else {}
    failures = int(current.get("failures") or 0) + 1
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=next_backoff_seconds(failures))
    install[hostname] = {
        "failures": failures,
        "last_error": safe_error(error),
        "last_failed_at": now_iso(),
        "next_attempt_at": retry_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    return install[hostname]


def clear_install_failure(health: dict, hostname: str) -> None:
    install = health.get("install")
    if isinstance(install, dict):
        install.pop(hostname, None)


def in_backoff(health: dict | None, hostname: str, *, now: datetime | None = None) -> bool:
    install = (health or {}).get("install")
    if not isinstance(install, dict):
        return False
    entry = install.get(hostname)
    if not isinstance(entry, dict):
        return False
    deadline = parse_iso(entry.get("next_attempt_at"))
    if deadline is None:
        return False
    return deadline > (now or datetime.now(timezone.utc))


__all__ = [
    "build_health",
    "clear_install_failure",
    "days_left",
    "in_backoff",
    "next_backoff_seconds",
    "now_iso",
    "parse_iso",
    "previous_health",
    "previous_install",
    "register_install_failure",
]
