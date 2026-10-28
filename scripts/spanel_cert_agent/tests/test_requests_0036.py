"""Solicitudes 0036 procesadas por el agente: nunca invocan Certbot (I4)."""

from __future__ import annotations

import json

from spanel_cert_agent.installer import install_paths
from spanel_cert_agent.runner import CertAgent
from spanel_cert_agent.state import read_job
from support import (
    FakeCommands,
    FakeConnector,
    base_config,
    make_lineage,
    prepare_default,
    write_request,
)

HOST = "mail.systutor.com"
JOB = "66666666-6666-4666-8666-666666666666"


def _agent(config, commands, connector=None):
    return CertAgent(
        config,
        runner=commands,
        connector=connector,
        verifier=lambda _stage: True,
        alert_sender=lambda _alert: None,
    )


def _connector(serial="ABC"):
    return FakeConnector({HOST: serial}, default_serial="CFSERIAL")


def test_verify_request_is_processed_without_certbot(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    cert, key = install_paths(config, HOST)
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    commands.serials[str(cert)] = "ABC"

    request = write_request(
        config,
        {"hostname": HOST, "action": "verify", "requested_by": "admin"},
        JOB,
    )
    ok = _agent(config, commands, _connector()).process_request(request)

    assert ok is True
    assert commands.names().count("certbot") == 0
    assert not commands.called("postfix")
    assert not commands.called("dovecot")
    job = read_job(config, JOB)
    assert job is not None and job["state"] == "succeeded"
    assert job["action"] == "verify"
    assert not request.exists()


def test_sync_request_installs_without_certbot(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    commands.serials[str(install_paths(config, HOST)[0])] = "ABC"
    make_lineage(config, HOST, content="nuevo")

    request = write_request(
        config,
        {"hostname": HOST, "action": "sync", "requested_by": "admin"},
        JOB,
    )
    ok = _agent(config, commands, _connector()).process_request(request)

    assert ok is True
    assert commands.names().count("certbot") == 0
    assert commands.called("postfix") and commands.called("dovecot")
    job = read_job(config, JOB)
    assert job is not None and job["state"] == "succeeded"
    assert install_paths(config, HOST)[0].read_text(encoding="utf-8") == "nuevo"


def test_install_then_rollback_requests(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    commands.serials[str(install_paths(config, HOST)[0])] = "ABC"
    make_lineage(config, HOST, content="v1")
    agent = _agent(config, commands, _connector())

    first = write_request(
        config, {"hostname": HOST, "action": "install"}, "11111111-1111-4111-8111-111111111111"
    )
    assert agent.process_request(first) is True
    second = write_request(
        config, {"hostname": HOST, "action": "install"}, "22222222-2222-4222-8222-222222222222"
    )
    assert agent.process_request(second) is True

    make_lineage(config, HOST, content="v1")
    rollback = write_request(
        config, {"hostname": HOST, "action": "rollback"}, "33333333-3333-4333-8333-333333333333"
    )
    assert agent.process_request(rollback) is True
    assert commands.names().count("certbot") == 0
    job = read_job(config, "33333333-3333-4333-8333-333333333333")
    assert job is not None and job["state"] == "succeeded"
    assert job["action"] == "rollback"


def test_unknown_action_is_rejected_without_certbot(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    request = write_request(
        config, {"hostname": HOST, "action": "renew"}, JOB
    )
    agent = _agent(config, commands, _connector())

    assert agent.process_request(request) is False
    assert commands.names().count("certbot") == 0
    job = read_job(config, JOB)
    assert job is not None and job["state"] == "failed"


def test_request_payload_is_metadata_only(tmp_path):
    config = base_config(tmp_path)
    prepare_default(config)
    commands = FakeCommands()
    cert, key = install_paths(config, HOST)
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    commands.serials[str(cert)] = "ABC"
    request = write_request(
        config, {"hostname": HOST, "action": "verify"}, JOB
    )
    _agent(config, commands, _connector()).process_request(request)

    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    serialized = json.dumps(state)
    assert "PRIVATE KEY" not in serialized
