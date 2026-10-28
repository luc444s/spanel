"""Utilidades compartidas por los tests 0036 (sin red, sin root, sin Docker)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from spanel_cert_agent.config import (
    AgentConfig,
    AlertConfig,
    CertbotConfig,
    DovecotConfig,
    PostfixConfig,
    VerificationConfig,
)

PUB = "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----"
OTHER_PUB = "-----BEGIN PUBLIC KEY-----\nBBBB\n-----END PUBLIC KEY-----"


def _path_arg(argv: list[str]) -> str:
    for item in argv:
        if item.endswith((".pem", ".crt")):
            return item
    return ""


class FakeCommands:
    """Runner falso: openssl, recargas y fallos inyectables; registra cada argv."""

    def __init__(self, *, key_matches: bool = True, san: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.key_matches = key_matches
        self.san = san
        self.serials: dict[str, str] = {}
        self.fingerprints: dict[str, str] = {}
        self.expired: set[str] = set()
        self.days: dict[str, int] = {}
        self.fail_commands: set[str] = set()
        self.default_serial = "CFSERIAL"
        self.default_san = "cf-mail"
        self.default_fingerprint = "CF:FP"

    def __call__(self, argv, input_data=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "openssl":
            return subprocess.CompletedProcess(argv, 0, self._openssl(argv), b"")
        name = Path(argv[0]).name if argv else ""
        if name in self.fail_commands:
            return subprocess.CompletedProcess(argv, 1, b"", b"fallo inyectado")
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    def _openssl(self, argv):
        path = _path_arg(argv)
        if "x509" in argv and "-pubkey" in argv:
            return PUB.encode()
        if "pkey" in argv:
            return (PUB if self.key_matches else OTHER_PUB).encode()
        if "-subject" in argv:
            return b"subject=CN=mail.systutor.com\n"
        if "-issuer" in argv:
            return b"issuer=CN=Test CA\n"
        if "-serial" in argv:
            serial = self.serials.get(path, "ABC123")
            if Path(path).name == "cf-mail.fullchain.crt":
                serial = self.default_serial
            return f"serial={serial}\n".encode()
        if "-fingerprint" in argv:
            fingerprint = self.fingerprints.get(path)
            if fingerprint is None:
                if Path(path).name == "cf-mail.fullchain.crt":
                    fingerprint = self.default_fingerprint
                else:
                    content = Path(path).read_bytes() if Path(path).is_file() else b""
                    fingerprint = hashlib.sha256(content).hexdigest()
            return f"SHA256 Fingerprint={fingerprint}\n".encode()
        if "-startdate" in argv and "-enddate" in argv:
            start, end = self._dates(path)
            return f"notBefore={start}\nnotAfter={end}\n".encode()
        if "-startdate" in argv:
            start, _ = self._dates(path)
            return f"notBefore={start}\n".encode()
        if "-enddate" in argv:
            _, end = self._dates(path)
            return f"notAfter={end}\n".encode()
        if "-ext" in argv:
            san = self.san
            if Path(path).name == "cf-mail.fullchain.crt":
                san = self.default_san
            if san is None:
                san = "mail.systutor.com"
            return f"X509v3 Subject Alternative Name:\n    DNS:{san}\n".encode()
        return b""

    def _dates(self, path: str) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        if path in self.days:
            start = now - timedelta(days=1)
            end = now + timedelta(days=self.days[path])
        elif path in self.expired or Path(path).name == "cf-mail.fullchain.crt":
            start = now - timedelta(days=90)
            end = now - timedelta(days=1) if path in self.expired else now + timedelta(days=90)
        else:
            start = now - timedelta(days=1)
            end = now + timedelta(days=90)
        return (
            start.strftime("%b %d %H:%M:%S %Y GMT"),
            end.strftime("%b %d %H:%M:%S %Y GMT"),
        )

    def names(self) -> list[str]:
        return [Path(call[0]).name for call in self.calls if call]

    def called(self, name: str) -> bool:
        return name in self.names()


class FakeConnector:
    """Connector TLS falso: devuelve seriales por SNI/sin SNI y falla por puerto."""

    def __init__(
        self,
        serials: dict[str, str] | None = None,
        *,
        default_serial: str = "CFSERIAL",
        fail_ports: set[int] | None = None,
        fail_hosts: set[str] | None = None,
        no_sni_serial: str | None = None,
    ) -> None:
        self.serials = serials or {}
        self.default_serial = default_serial
        self.fail_ports = fail_ports or set()
        self.fail_hosts = fail_hosts or set()
        self.no_sni_serial = no_sni_serial
        self.calls: list[tuple[str, int, str | None]] = []

    def __call__(self, host, port, servername, context, timeout):
        self.calls.append((host, port, servername))
        if port in self.fail_ports:
            raise OSError("puerto caído")
        if servername is None:
            serial = self.no_sni_serial or self.default_serial
        else:
            if servername in self.fail_hosts:
                raise OSError("hostname caído")
            serial = self.serials.get(servername)
            if serial is None:
                raise OSError("hostname desconocido")
        return {
            "serial": serial,
            "fingerprint": f"FP-{serial}",
            "not_after": None,
            "sans": [servername] if servername else ["cf-mail"],
        }


def base_config(tmp_path: Path, **overrides) -> AgentConfig:
    values = dict(  # noqa: C408 - kwargs keep the fixture readable
        allowlist=frozenset({"mail.systutor.com"}),
        default_name="cf-mail",
        default_cert_path=tmp_path / "cf-mail.fullchain.crt",
        default_key_path=tmp_path / "cf-mail.key",
        state_path=tmp_path / "state" / "state.json",
        alerts_path=tmp_path / "state" / "alerts.json",
        requests_dir=tmp_path / "requests",
        jobs_dir=tmp_path / "jobs",
        generations_root=tmp_path / "generations",
        backup_root=tmp_path / "backups",
        locks_dir=tmp_path / "locks",
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
        verification=VerificationConfig(
            ca_file=tmp_path / "ca.pem",
            audit_interval_seconds=600,
        ),
        alerts=AlertConfig(warning_days=21, critical_days=7),
    )
    values.update(overrides)
    return AgentConfig(**values)


def make_lineage(config: AgentConfig, hostname: str, content: str = "cert") -> Path:
    lineage = config.certbot.lineage_root / hostname
    lineage.mkdir(parents=True, exist_ok=True)
    (lineage / "fullchain.pem").write_text(content, encoding="utf-8")
    (lineage / "privkey.pem").write_text("key", encoding="utf-8")
    return lineage


def prepare_default(config: AgentConfig) -> None:
    config.default_cert_path.parent.mkdir(parents=True, exist_ok=True)
    config.default_cert_path.write_text("cert", encoding="utf-8")
    config.default_key_path.write_text("key", encoding="utf-8")
    config.verification.ca_file.parent.mkdir(parents=True, exist_ok=True)
    config.verification.ca_file.write_text("ca", encoding="utf-8")


def write_request(config: AgentConfig, payload: dict, request_id: str) -> Path:
    config.requests_dir.mkdir(parents=True, exist_ok=True)
    path = config.requests_dir / f"{request_id}.json"
    path.write_text(json.dumps({"id": request_id, **payload}), encoding="utf-8")
    return path
