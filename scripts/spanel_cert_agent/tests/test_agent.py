"""Tests herméticos del agente: sin red, sin Docker, sin root."""

from __future__ import annotations

import json
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from spanel_cert_agent.certbot import build_argv
from spanel_cert_agent.config import (
    AgentConfig,
    AgentConfigError,
    CertbotConfig,
    DovecotConfig,
    PostfixConfig,
    load_config,
)
from spanel_cert_agent.installer import install_pair, install_paths
from spanel_cert_agent.runner import CertAgent
from spanel_cert_agent.sni import (
    apply_sni,
    render_dovecot_local_name,
    render_postfix_map,
)
from spanel_cert_agent.state import build_state, read_job, write_state
from spanel_cert_agent.validation import (
    CertificateValidationError,
    validate_request_payload,
)

PUB = "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----"
OTHER_PUB = "-----BEGIN PUBLIC KEY-----\nBBBB\n-----END PUBLIC KEY-----"


class FakeCommands:
    """Responde openssl, certbot y recargas; registra cada argv."""

    def __init__(self, *, key_matches: bool = True, san: str = "mail.systutor.com") -> None:
        self.calls: list[list[str]] = []
        self.key_matches = key_matches
        self.san = san
        self.on_certbot = None

    def __call__(self, argv, input_data=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "openssl":
            return subprocess.CompletedProcess(argv, 0, self._openssl(argv), b"")
        if argv and Path(argv[0]).name == "certbot":
            if self.on_certbot is not None:
                self.on_certbot(argv)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    def _openssl(self, argv):
        if "x509" in argv and "-pubkey" in argv:
            return PUB.encode()
        if "pkey" in argv:
            return (PUB if self.key_matches else OTHER_PUB).encode()
        if "-subject" in argv:
            return b"subject=CN=mail.systutor.com\n"
        if "-issuer" in argv:
            return b"issuer=CN=Test CA\n"
        if "-serial" in argv:
            return b"serial=ABC123\n"
        if "-fingerprint" in argv:
            return b"SHA256 Fingerprint=AA:BB:CC\n"
        if "-startdate" in argv and "-enddate" in argv:
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=1)).strftime("%b %d %H:%M:%S %Y GMT")
            end = (now + timedelta(days=90)).strftime("%b %d %H:%M:%S %Y GMT")
            return f"notBefore={start}\nnotAfter={end}\n".encode()
        if "-startdate" in argv:
            now = datetime.now(timezone.utc)
            stamp = (now - timedelta(days=1)).strftime("%b %d %H:%M:%S %Y GMT")
            return f"notBefore={stamp}\n".encode()
        if "-enddate" in argv:
            now = datetime.now(timezone.utc)
            stamp = (now + timedelta(days=90)).strftime("%b %d %H:%M:%S %Y GMT")
            return f"notAfter={stamp}\n".encode()
        if "-ext" in argv:
            return f"X509v3 Subject Alternative Name:\n    DNS:{self.san}\n".encode()
        return b""

    def certbot_calls(self):
        return [call for call in self.calls if call and Path(call[0]).name == "certbot"]


def base_config(tmp_path: Path, **overrides) -> AgentConfig:
    values = dict(  # noqa: C408 - kwargs keep the fixture readable
        allowlist=frozenset({"mail.systutor.com"}),
        default_name="cf-mail",
        default_cert_path=tmp_path / "cf-mail.fullchain.crt",
        default_key_path=tmp_path / "cf-mail.key",
        state_path=tmp_path / "state" / "state.json",
        requests_dir=tmp_path / "requests",
        jobs_dir=tmp_path / "jobs",
        install_root=tmp_path / "ssl",
        certbot=CertbotConfig(
            executable="/usr/bin/certbot",
            challenge="webroot",
            webroot_path=tmp_path / "webroot",
            lineage_root=tmp_path / "lineage",
        ),
        postfix=PostfixConfig(
            sni_map_path=tmp_path / "postfix" / "sni",
            postmap_command=("postmap", str(tmp_path / "postfix" / "sni")),
            reload_command=("postfix", "reload"),
        ),
        dovecot=DovecotConfig(
            local_name_path=tmp_path / "dovecot" / "99-spanel.conf",
            reload_command=("dovecot", "reload"),
        ),
    )
    values.update(overrides)
    return AgentConfig(**values)


def write_request(config: AgentConfig, payload: dict, request_id: str) -> Path:
    config.requests_dir.mkdir(parents=True, exist_ok=True)
    path = config.requests_dir / f"{request_id}.json"
    path.write_text(json.dumps({"id": request_id, **payload}), encoding="utf-8")
    return path


def make_lineage(config: AgentConfig, hostname: str) -> Path:
    lineage = config.certbot.lineage_root / hostname
    lineage.mkdir(parents=True, exist_ok=True)
    (lineage / "fullchain.pem").write_text("cert", encoding="utf-8")
    (lineage / "privkey.pem").write_text("key", encoding="utf-8")
    return lineage


# ---- configuración ------------------------------------------------------


def test_config_loads_toml_and_env_overrides(tmp_path):
    config_file = tmp_path / "cert-agent.toml"
    config_file.write_text(
        """
allowlist = ["mail.systutor.com", "mail.legalattorney.pe"]
default_certificate = "cf-mail"
[certbot]
executable = "/usr/bin/certbot"
challenge = "webroot"
webroot_path = "/var/www"
[postfix]
sni_map_path = "/etc/postfix/sni"
postmap_command = ["postmap", "/etc/postfix/sni"]
reload_command = ["postfix", "reload"]
[dovecot]
local_name_path = "/etc/dovecot/local.conf"
reload_command = ["dovecot", "reload"]
""",
        encoding="utf-8",
    )
    config = load_config(
        config_file,
        env={
            "SPANEL_CERT_STATE_PATH": str(tmp_path / "state.json"),
            "SPANEL_CERT_ALLOWLIST": "mail.env.example",
        },
    )
    assert config.allowlist == frozenset({"mail.env.example"})
    assert config.state_path == tmp_path / "state.json"
    assert config.certbot.webroot_path == Path("/var/www")
    assert config.postfix.postmap_command == ("postmap", "/etc/postfix/sni")
    assert config.dovecot.reload_command == ("dovecot", "reload")


def test_config_fails_closed_without_reload_commands(tmp_path):
    config = base_config(
        tmp_path,
        postfix=PostfixConfig(),
        dovecot=DovecotConfig(),
    )
    with pytest.raises(AgentConfigError):
        config.require_reload_config()


# ---- argv ---------------------------------------------------------------


def test_certbot_argv_is_fixed(tmp_path):
    config = base_config(tmp_path)
    assert build_argv(config, "mail.systutor.com", "ensure") == [
        "/usr/bin/certbot",
        "certonly",
        "--cert-name",
        "mail.systutor.com",
        "-d",
        "mail.systutor.com",
        "--webroot",
        "-w",
        str(tmp_path / "webroot"),
    ]
    make_lineage(config, "mail.systutor.com")
    assert build_argv(config, "mail.systutor.com", "ensure") == [
        "/usr/bin/certbot",
        "renew",
        "--cert-name",
        "mail.systutor.com",
    ]
    assert build_argv(config, "mail.systutor.com", "force") == [
        "/usr/bin/certbot",
        "certonly",
        "--force-renewal",
        "--cert-name",
        "mail.systutor.com",
        "-d",
        "mail.systutor.com",
        "--webroot",
        "-w",
        str(tmp_path / "webroot"),
    ]


# ---- payload ------------------------------------------------------------


def test_payload_rejects_extra_certbot_flags():
    with pytest.raises(CertificateValidationError):
        validate_request_payload(
            {
                "hostname": "mail.systutor.com",
                "action": "force",
                "--force-renewal": True,
            },
            default_name="cf-mail",
        )
    with pytest.raises(CertificateValidationError):
        validate_request_payload(
            {"hostname": "cf-mail", "action": "ensure"}, default_name="cf-mail"
        )
    assert validate_request_payload(
        {"hostname": "MAIL.systutor.com.", "action": "ensure"}, default_name="cf-mail"
    )["hostname"] == "mail.systutor.com"


def test_allowlist_rejects_unknown_hostname(tmp_path):
    config = base_config(tmp_path)
    with pytest.raises(AgentConfigError):
        config.require_hostname("mail.other.com")
    with pytest.raises(AgentConfigError):
        config.require_hostname("cf-mail")


# ---- validación e instalación -------------------------------------------


def test_wrong_key_san_or_dates_do_not_install(tmp_path):
    config = base_config(tmp_path)
    lineage = make_lineage(config, "mail.systutor.com")
    cert, key = install_paths(config, "mail.systutor.com")

    with pytest.raises(CertificateValidationError):
        install_pair(config, "mail.systutor.com", lineage, runner=FakeCommands(key_matches=False))
    assert not cert.exists() and not key.exists()

    with pytest.raises(CertificateValidationError):
        install_pair(config, "mail.systutor.com", lineage, runner=FakeCommands(san="mail.other.com"))
    assert not cert.exists() and not key.exists()

    expired = FakeCommands()
    original = expired._openssl

    def old_dates(argv):
        if "-enddate" in argv:
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=90)).strftime("%b %d %H:%M:%S %Y GMT")
            end = (now - timedelta(days=1)).strftime("%b %d %H:%M:%S %Y GMT")
            return f"notBefore={start}\nnotAfter={end}\n".encode()
        return original(argv)

    expired._openssl = old_dates  # type: ignore[method-assign]
    with pytest.raises(CertificateValidationError):
        install_pair(config, "mail.systutor.com", lineage, runner=expired)
    assert not cert.exists() and not key.exists()


def test_valid_pair_installs_with_modes(tmp_path):
    config = base_config(tmp_path)
    lineage = make_lineage(config, "mail.systutor.com")
    cert, key, info = install_pair(
        config, "mail.systutor.com", lineage, runner=FakeCommands()
    )
    assert cert.exists() and key.exists()
    assert stat.S_IMODE(cert.stat().st_mode) == 0o644
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert info.sans == ["mail.systutor.com"]


# ---- SNI ----------------------------------------------------------------


def test_sni_rendering(tmp_path):
    cert = tmp_path / "fullchain.pem"
    key = tmp_path / "privkey.pem"
    entries = {"mail.systutor.com": (cert, key)}
    assert "mail.systutor.com" in render_postfix_map(entries)
    assert str(cert) in render_postfix_map(entries)
    rendered = render_dovecot_local_name(entries)
    assert "local_name mail.systutor.com {" in rendered
    assert f"ssl_key = <{key}" in rendered


def test_sni_fails_closed_without_config(tmp_path):
    config = base_config(tmp_path, postfix=PostfixConfig(), dovecot=DovecotConfig())
    map_path = tmp_path / "postfix" / "sni"
    entries = {"mail.systutor.com": (tmp_path / "cert", tmp_path / "key")}
    with pytest.raises(AgentConfigError):
        apply_sni(config, entries, runner=FakeCommands())
    assert not map_path.exists()


# ---- estado -------------------------------------------------------------


def test_state_is_metadata_only_and_cf_mail_is_global_default(tmp_path):
    config = base_config(tmp_path)
    cert, key = install_paths(config, "mail.systutor.com")
    cert.parent.mkdir(parents=True, exist_ok=True)
    cert.write_text("cert", encoding="utf-8")
    key.write_text("-----BEGIN PRIVATE KEY-----\nsecret\n", encoding="utf-8")
    config.default_cert_path.parent.mkdir(parents=True, exist_ok=True)
    config.default_cert_path.write_text("cert", encoding="utf-8")
    config.default_key_path.write_text("key", encoding="utf-8")

    state = build_state(config, runner=FakeCommands())

    default = next(item for item in state["certificates"] if item["hostname"] == "cf-mail")
    assert default["scope"] == "global"
    assert default["domain"] is None
    assert default["deployment"] == "default"
    serialized = json.dumps(state)
    assert "PRIVATE KEY" not in serialized
    assert "secret" not in serialized
    sni = next(item for item in state["certificates"] if item["hostname"] == "mail.systutor.com")
    assert sni["deployment"] == "sni"
    assert sni["key_path"].endswith("privkey.pem")

    write_state(config, state)
    assert stat.S_IMODE(config.state_path.stat().st_mode) == 0o640
    assert "PRIVATE KEY" not in config.state_path.read_text(encoding="utf-8")


# ---- runner -------------------------------------------------------------


def test_run_once_rejects_hostname_outside_allowlist_without_certbot(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    request = write_request(
        config,
        {"hostname": "mail.other.com", "action": "ensure", "requested_by": "u1"},
        "11111111-1111-4111-8111-111111111111",
    )
    agent = CertAgent(config, runner=commands)

    assert agent.process_request(request) is False

    assert commands.certbot_calls() == []
    job = read_job(config, "11111111-1111-4111-8111-111111111111")
    assert job is not None
    assert job["state"] == "failed"
    assert "allowlist" in job["error"]
    assert not request.exists()
    assert not config.state_path.exists()


def test_run_once_rejects_extra_flags_without_certbot(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    request = write_request(
        config,
        {"hostname": "mail.systutor.com", "action": "ensure", "args": ["--force-renewal"]},
        "22222222-2222-4222-8222-222222222222",
    )
    agent = CertAgent(config, runner=commands)

    assert agent.process_request(request) is False
    assert commands.certbot_calls() == []
    job = read_job(config, "22222222-2222-4222-8222-222222222222")
    assert job is not None and job["state"] == "failed"


def test_run_once_fails_closed_without_sni_config(tmp_path):
    config = base_config(tmp_path, postfix=PostfixConfig(), dovecot=DovecotConfig())
    commands = FakeCommands()
    request = write_request(
        config,
        {"hostname": "mail.systutor.com", "action": "ensure"},
        "33333333-3333-4333-8333-333333333333",
    )
    agent = CertAgent(config, runner=commands)

    assert agent.process_request(request) is False
    assert commands.certbot_calls() == []
    job = read_job(config, "33333333-3333-4333-8333-333333333333")
    assert job is not None
    assert job["state"] == "failed"
    assert "Postfix" in job["error"]
    assert not config.state_path.exists()


def test_run_once_missing_lineage_fails_job_instead_of_staying_running(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()
    request = write_request(
        config,
        {"hostname": "mail.systutor.com", "action": "ensure", "requested_by": "u1"},
        "55555555-5555-4555-8555-555555555555",
    )
    agent = CertAgent(config, runner=commands)

    assert agent.process_request(request) is False

    job = read_job(config, "55555555-5555-4555-8555-555555555555")
    assert job is not None
    assert job["state"] == "failed"
    assert "linaje" in job["error"]
    assert not request.exists()
    assert not (config.install_root / "mail.systutor.com").exists()


def test_run_once_success_updates_state_and_job(tmp_path):
    config = base_config(tmp_path)
    commands = FakeCommands()

    def emit(argv):
        make_lineage(config, "mail.systutor.com")

    commands.on_certbot = emit
    request = write_request(
        config,
        {"hostname": "mail.systutor.com", "action": "force", "requested_by": "u1"},
        "44444444-4444-4444-8444-444444444444",
    )
    agent = CertAgent(config, runner=commands)

    assert agent.process_request(request) is True

    assert len(commands.certbot_calls()) == 1
    assert "--force-renewal" in commands.certbot_calls()[0]
    job = read_job(config, "44444444-4444-4444-8444-444444444444")
    assert job is not None and job["state"] == "succeeded"
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    sni = next(
        item for item in state["certificates"] if item["hostname"] == "mail.systutor.com"
    )
    assert sni["deployment"] == "sni"
    assert sni["status"] == "active"
    assert (config.install_root / "mail.systutor.com" / "fullchain.pem").exists()
    assert (config.postfix.sni_map_path).exists()
    assert (config.dovecot.local_name_path).exists()
    assert not request.exists()
