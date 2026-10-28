"""Operaciones de verificación, sincronización e instalación del agente.

`audit` es de solo lectura sobre certificados y servicios: únicamente escribe
`state.json`/`alerts.json`. `sync` detecta el cambio de huella que dejó
`certbot.timer` e instala una sola vez. Ninguna operación ejecuta `certbot` (I4).
"""

from __future__ import annotations

from .alerts import publish_alerts
from .certbot import lineage_dir
from .config import AgentConfig
from .health import (
    build_health,
    clear_install_failure,
    in_backoff,
    register_install_failure,
)
from .state import build_state, ensure_directories, read_json, write_state
from .transaction import (
    InstallBusy,
    InstallOutcome,
    rollback_hostname,
    transactional_install,
)
from .validation import CertificateValidationError, inspect_certificate


def _installed_fingerprint(config: AgentConfig, hostname: str, *, runner=None) -> str | None:
    from .installer import install_paths as stable_paths

    cert_path, _key = stable_paths(config, hostname)
    if not cert_path.is_file():
        return None
    kwargs = {"runner": runner} if runner is not None else {}
    try:
        return inspect_certificate(cert_path, **kwargs).fingerprint
    except CertificateValidationError:
        return None


def _lineage_fingerprint(config: AgentConfig, hostname: str, *, runner=None) -> str | None:
    cert_path = lineage_dir(config, hostname) / "fullchain.pem"
    if not cert_path.is_file():
        return None
    kwargs = {"runner": runner} if runner is not None else {}
    try:
        return inspect_certificate(cert_path, **kwargs).fingerprint
    except CertificateValidationError:
        return None


def _merge_health(inventory: dict, health: dict) -> None:
    by_host = {
        str(item.get("hostname")): item
        for item in health.get("certificates", [])
        if isinstance(item, dict)
    }
    for entry in inventory.get("certificates", []):
        if not isinstance(entry, dict):
            continue
        extra = by_host.get(str(entry.get("hostname")))
        if not extra:
            continue
        for key in (
            "served_serial",
            "no_sni_serial",
            "days_left",
            "sni",
            "no_sni",
            "no_sni_ok",
            "ok",
            "last_verified_at",
            "last_ok_at",
            "consecutive_failures",
        ):
            entry[key] = extra.get(key)


def run_audit(
    config: AgentConfig,
    *,
    connector=None,
    runner=None,
    sender=None,
    now=None,
) -> dict:
    """Verificación continua. No escribe certificados ni recarga servicios (I1)."""

    ensure_directories(config)
    previous = read_json(config.state_path)
    inventory = build_state(config, runner=runner)
    health = build_health(
        config,
        inventory,
        previous=previous,
        connector=connector,
        runner=runner,
        now=now,
    )
    inventory["health"] = health
    _merge_health(inventory, health)
    write_state(config, inventory)
    alerts = publish_alerts(
        config,
        health,
        previous_state=previous,
        runner=runner,
        sender=sender,
        now=now,
    )
    return {"state": inventory, "health": health, "alerts": alerts}


def verify_only(
    config: AgentConfig,
    *,
    hostname: str | None = None,
    connector=None,
    runner=None,
    now=None,
) -> dict:
    """Probes sin escribir estado (comando `verify`)."""

    previous = read_json(config.state_path)
    inventory = build_state(config, runner=runner)
    if hostname:
        config.require_hostname(hostname)
        inventory["certificates"] = [
            entry
            for entry in inventory.get("certificates", [])
            if entry.get("hostname") in (hostname, config.default_name)
        ]
    health = build_health(
        config,
        inventory,
        previous=previous,
        connector=connector,
        runner=runner,
        now=now,
    )
    return {"health": health}


def _persist_failure(
    config: AgentConfig,
    hostname: str,
    *,
    error: str,
    runner=None,
    sender=None,
) -> None:
    state = read_json(config.state_path) or {}
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    register_install_failure(config, health, hostname, error=error)
    state["health"] = health
    write_state(config, state)
    publish_alerts(config, health, previous_state=state, runner=runner, sender=sender)


def _persist_success(config: AgentConfig, hostname: str, *, connector=None, runner=None, sender=None) -> None:
    state = read_json(config.state_path) or {}
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    clear_install_failure(health, hostname)
    state["health"] = health
    write_state(config, state)
    run_audit(config, connector=connector, runner=runner, sender=sender)


def install_current(
    config: AgentConfig,
    hostname: str,
    *,
    runner,
    verifier=None,
    connector=None,
    sender=None,
) -> InstallOutcome:
    config.require_hostname(hostname)
    config.require_reload_config()
    try:
        outcome = transactional_install(
            config,
            hostname,
            lineage_dir(config, hostname),
            runner=runner,
            verifier=verifier,
            connector=connector,
        )
    except InstallBusy as exc:
        return InstallOutcome(hostname=hostname, ok=True, changed=False, error=str(exc))
    _finish(config, hostname, outcome, runner=runner, connector=connector, sender=sender)
    return outcome


def sync_hostname(
    config: AgentConfig,
    hostname: str,
    *,
    runner,
    verifier=None,
    connector=None,
    sender=None,
) -> InstallOutcome:
    """Instala solo si la huella del linaje cambió respecto de lo activo."""

    config.require_hostname(hostname)
    config.require_reload_config()
    new = _lineage_fingerprint(config, hostname, runner=runner)
    if new is None:
        outcome = InstallOutcome(
            hostname=hostname,
            ok=False,
            changed=False,
            error="El linaje de Certbot está incompleto",
        )
        _finish(config, hostname, outcome, runner=runner, connector=connector, sender=sender)
        return outcome
    if new == _installed_fingerprint(config, hostname, runner=runner):
        return InstallOutcome(hostname=hostname, ok=True, changed=False)

    state = read_json(config.state_path) or {}
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    if in_backoff(health, hostname):
        return InstallOutcome(
            hostname=hostname,
            ok=False,
            changed=False,
            error="Instalación en backoff tras un fallo previo",
        )

    try:
        outcome = transactional_install(
            config,
            hostname,
            lineage_dir(config, hostname),
            runner=runner,
            verifier=verifier,
            connector=connector,
        )
    except InstallBusy as exc:
        return InstallOutcome(hostname=hostname, ok=True, changed=False, error=str(exc))
    _finish(config, hostname, outcome, runner=runner, connector=connector, sender=sender)
    return outcome


def rollback_current(
    config: AgentConfig,
    hostname: str,
    *,
    runner,
    verifier=None,
    connector=None,
    sender=None,
) -> InstallOutcome:
    config.require_hostname(hostname)
    config.require_reload_config()
    try:
        outcome = rollback_hostname(
            config, hostname, runner=runner, verifier=verifier, connector=connector
        )
    except InstallBusy as exc:
        return InstallOutcome(hostname=hostname, ok=True, changed=False, error=str(exc))
    _finish(config, hostname, outcome, runner=runner, connector=connector, sender=sender)
    return outcome


def _finish(
    config: AgentConfig,
    hostname: str,
    outcome: InstallOutcome,
    *,
    runner=None,
    connector=None,
    sender=None,
) -> None:
    if outcome.ok:
        _persist_success(
            config, hostname, connector=connector, runner=runner, sender=sender
        )
    else:
        _persist_failure(
            config,
            hostname,
            error=outcome.error or "Instalación fallida",
            runner=runner,
            sender=sender,
        )


__all__ = [
    "install_current",
    "rollback_current",
    "run_audit",
    "sync_hostname",
    "verify_only",
]
