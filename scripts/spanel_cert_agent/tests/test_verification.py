"""Probes, salud y alertas del agente (A.SPEC 0036)."""

from __future__ import annotations

from datetime import datetime, timezone

from spanel_cert_agent.alerts import evaluate_alerts, publish_alerts
from spanel_cert_agent.health import build_health, days_left
from spanel_cert_agent.installer import install_paths
from spanel_cert_agent.operations import run_audit
from spanel_cert_agent.probe import verify_hostname
from spanel_cert_agent.state import build_state, write_state
from support import (
    FakeCommands,
    FakeConnector,
    base_config,
    prepare_default,
)


def _installed(config, hostname="mail.systutor.com"):
    cert, key = install_paths(config, hostname)
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    return cert, key


def _state_with_health(config, commands, connector):
    prepare_default(config)
    _installed(config)
    inventory = build_state(config, runner=commands)
    health = build_health(config, inventory, connector=connector, runner=commands)
    inventory["health"] = health
    write_state(config, inventory)
    return inventory, health


# ---- probes -------------------------------------------------------------


def test_sni_and_no_sni_return_expected_serials(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    connector = FakeConnector(
        {"mail.systutor.com": "HOSTSERIAL"}, default_serial="CFSERIAL"
    )
    result = verify_hostname(
        "mail.systutor.com",
        expected_serial="HOSTSERIAL",
        ca_file=config.verification.ca_file,
        no_sni_expected_serial="CFSERIAL",
        connector=connector,
    )
    assert result["sni_ok"] is True
    assert result["no_sni_ok"] is True
    assert result["sni"] == {"993": True, "465": True, "587": True}
    assert result["served_serial"] == "HOSTSERIAL"
    assert result["no_sni_serial"] == "CFSERIAL"
    # Sin SNI nunca se envía server_hostname.
    no_sni_calls = [call for call in connector.calls if call[2] is None]
    assert len(no_sni_calls) == 3


def test_sni_serial_mismatch_is_failure(tmp_path):
    config = base_config(tmp_path)
    connector = FakeConnector({"mail.systutor.com": "OTRO"}, default_serial="CFSERIAL")
    result = verify_hostname(
        "mail.systutor.com",
        expected_serial="ESPERADO",
        ca_file=config.verification.ca_file,
        no_sni_expected_serial="CFSERIAL",
        connector=connector,
    )
    assert result["sni_ok"] is False


def test_no_sni_returning_sni_certificate_is_failure(tmp_path):
    config = base_config(tmp_path)
    connector = FakeConnector(
        {"mail.systutor.com": "HOSTSERIAL"}, default_serial="CFSERIAL"
    )
    result = verify_hostname(
        "mail.systutor.com",
        expected_serial="HOSTSERIAL",
        ca_file=config.verification.ca_file,
        no_sni_expected_serial="HOSTSERIAL",
        connector=connector,
    )
    assert result["no_sni_ok"] is False


# ---- salud --------------------------------------------------------------


def test_health_persists_counters_and_served_serial(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    connector = FakeConnector({"mail.systutor.com": "ABS123"}, default_serial="CFSERIAL")
    commands.serials[str(install_paths(config, "mail.systutor.com")[0])] = "ABS123"

    _inventory, health = _state_with_health(config, commands, connector)
    entry = health["certificates"][0]
    assert entry["ok"] is True
    assert entry["served_serial"] == "ABS123"
    assert entry["sni"] == {"993": True, "465": True, "587": True}
    assert entry["no_sni"] is True
    assert entry["consecutive_failures"] == 0
    assert entry["last_verified_at"] == entry["last_ok_at"]

    failed = FakeConnector({"mail.systutor.com": "ABS123"}, fail_ports={993})
    inventory = build_state(config, runner=commands)
    degraded = build_health(config, inventory, previous={"health": health}, connector=failed)
    entry = degraded["certificates"][0]
    assert entry["ok"] is False
    assert entry["consecutive_failures"] == 1
    assert entry["last_ok_at"] == health["certificates"][0]["last_ok_at"]
    assert entry["last_error"]


def test_health_detects_san_mismatch(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands(san="mail.otro.com")
    connector = FakeConnector({"mail.systutor.com": "ABS123"}, default_serial="CFSERIAL")
    prepare_default(config)
    _installed(config)
    inventory = build_state(config, runner=commands)
    health = build_health(config, inventory, connector=connector, runner=commands)
    entry = health["certificates"][0]
    assert entry["ok"] is False
    assert "SAN" in (entry["last_error"] or "")
    assert health["agent"]["state"] == "degraded"


# ---- alertas ------------------------------------------------------------


def _alert_codes(health, config, previous_generated_at=None):
    return {
        alert["code"]
        for alert in evaluate_alerts(
            health, config, previous_generated_at=previous_generated_at
        )
    }


def test_expiry_thresholds_and_serial_mismatch(tmp_path):
    config = base_config(tmp_path)
    now = datetime.now(timezone.utc)
    base_entry = {
        "hostname": "mail.systutor.com",
        "serial": "A",
        "served_serial": "A",
        "days_left": 30,
        "sni": {"993": True, "465": True, "587": True},
        "ok": True,
        "no_sni_ok": True,
    }
    health = {"certificates": [dict(base_entry)], "default": {"ok": True}}
    generated = now.isoformat()
    assert _alert_codes(health, config, generated) == set()

    warning = {**base_entry, "days_left": 15}
    assert "certificate_expiry_warning" in _alert_codes(
        {"certificates": [warning]}, config, generated
    )
    critical = {**base_entry, "days_left": 3}
    assert "certificate_expiry_critical" in _alert_codes(
        {"certificates": [critical]}, config, generated
    )
    expired = {**base_entry, "days_left": -1}
    assert "certificate_expired" in _alert_codes(
        {"certificates": [expired]}, config, generated
    )
    mismatch = {**base_entry, "served_serial": "B"}
    assert "certificate_serial_mismatch" in _alert_codes(
        {"certificates": [mismatch]}, config, generated
    )


def test_postfix_dovecot_and_default_alerts(tmp_path):
    config = base_config(tmp_path)
    now = datetime.now(timezone.utc)
    entry = {
        "hostname": "mail.systutor.com",
        "serial": "A",
        "served_serial": "A",
        "days_left": 60,
        "sni": {"993": False, "465": True, "587": True},
        "ok": False,
        "no_sni_ok": True,
    }
    codes = _alert_codes({"certificates": [entry]}, config, now.isoformat())
    assert "dovecot_failure" in codes

    postfix = {**entry, "sni": {"993": True, "465": False, "587": False}}
    assert "postfix_failure" in _alert_codes(
        {"certificates": [postfix]}, config, now.isoformat()
    )
    broken_default = {**entry, "no_sni_ok": False, "sni": {"993": True, "465": True, "587": True}}
    assert "default_certificate_mismatch" in _alert_codes(
        {"certificates": [broken_default]}, config, now.isoformat()
    )


def test_stale_agent_alert(tmp_path):
    config = base_config(tmp_path)
    old = "2020-01-01T00:00:00Z"
    codes = _alert_codes({"certificates": []}, config, old)
    assert "agent_stale" in codes
    assert "agent_stale" not in _alert_codes(
        {"certificates": []}, config, datetime.now(timezone.utc).isoformat()
    )


def test_alert_channel_is_external_and_independent_from_smtp(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    commands.days[str(install_paths(config, "mail.systutor.com")[0])] = 5
    commands.serials[str(install_paths(config, "mail.systutor.com")[0])] = "ABS123"
    connector = FakeConnector({"mail.systutor.com": "ABS123"}, default_serial="CFSERIAL")
    prepare_default(config)
    _installed(config)

    delivered: list[dict] = []
    run_audit(config, connector=connector, runner=commands, sender=delivered.append)

    assert delivered, "el canal externo debe recibir la alerta"
    assert any(alert["severity"] == "critical" for alert in delivered)
    assert "postfix" not in commands.names()
    assert "dovecot" not in commands.names()
    assert "certbot" not in commands.names()
    assert "sendmail" not in commands.names()
    stored = config.alerts_path.read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in stored


def test_alerts_are_deduplicated_until_resend_window(tmp_path):
    config = base_config(tmp_path)
    health = {
        "certificates": [
            {
                "hostname": "mail.systutor.com",
                "days_left": 2,
                "sni": {"993": True, "465": True, "587": True},
                "ok": True,
                "no_sni_ok": True,
            }
        ]
    }
    delivered: list[dict] = []
    state = {"generated_at": datetime.now(timezone.utc).isoformat()}
    publish_alerts(
        config, health, previous_state=state, sender=delivered.append
    )
    assert len(delivered) == 1
    publish_alerts(
        config, health, previous_state=state, sender=delivered.append
    )
    assert len(delivered) == 1


def test_sin_canal_no_intenta_entregar_ni_falla(tmp_path):
    config = base_config(tmp_path)
    health = {
        "certificates": [
            {
                "hostname": "mail.systutor.com",
                "days_left": 2,
                "sni": {"993": True, "465": True, "587": True},
                "ok": True,
                "no_sni_ok": True,
            }
        ]
    }

    document = publish_alerts(
        config,
        health,
        previous_state={"generated_at": datetime.now(timezone.utc).isoformat()},
    )

    assert document["alerts"]
    assert document["delivery_errors"] == []


def test_days_left_helper():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert days_left("2026-11-27T00:00:00Z", now=now) == 31
    assert days_left(None, now=now) is None


def test_audit_writes_nothing_but_state_and_alerts(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    connector = FakeConnector({"mail.systutor.com": "ABS123"})
    commands.serials[str(install_paths(config, "mail.systutor.com")[0])] = "ABS123"
    prepare_default(config)
    cert, _key = _installed(config)
    before = cert.read_bytes()

    run_audit(config, connector=connector, runner=commands, sender=lambda _a: None)

    assert cert.read_bytes() == before
    assert "postfix" not in commands.names()
    assert "dovecot" not in commands.names()
    assert config.state_path.exists()
    assert config.alerts_path.exists()
