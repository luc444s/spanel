"""Operator CLI contract tests for ``scripts/spanel_dns.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

from plugins.domains.backend.dns.client import PowerDnsClient
from plugins.domains.backend.dns.config import PowerDnsConfig
from plugins.domains.backend.dns.errors import PowerDnsNotFound
from plugins.domains.backend.dns.models import DnsZone
from plugins.domains.backend.dns.validation import zone_version

SPEC = importlib.util.spec_from_file_location(
    "spanel_dns_cli", CORE / "plugins/domains/backend/dns/cli.py"
)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


class FakePowerDns:
    def __init__(self) -> None:
        self.zones: dict[str, DnsZone] = {}
        self.fail: Exception | None = None

    def _zone(self, name: str) -> DnsZone:
        if self.fail is not None:
            raise self.fail
        try:
            return self.zones[name]
        except KeyError as exc:
            raise PowerDnsNotFound("PowerDNS resource not found") from exc

    def create_zone(self, zone, *, kind="Native", nameservers=None):
        if zone in self.zones:
            raise RuntimeError("duplicate")
        self.zones[zone] = DnsZone(zone, kind)
        return self.zones[zone]

    def get_zone(self, zone):
        return self._zone(zone)

    def patch_rrsets(self, zone, rrsets, *, delete=None):
        current = self._zone(zone)
        state = {(item.name, item.type): item for item in current.rrsets}
        for rrset in rrsets:
            state[(rrset.name, rrset.type)] = rrset
        for name, type_ in delete or []:
            state.pop((name, type_), None)
        self.zones[zone] = DnsZone(zone, current.kind, tuple(state.values()))

    def delete_rrset(self, zone, name, type_):
        self.patch_rrsets(zone, [], delete=[(name, type_)])

    def delete_zone(self, zone):
        self._zone(zone)
        del self.zones[zone]

    def list_zones(self):
        return [{"name": f"{name}.", "kind": value.kind} for name, value in self.zones.items()]


def run(argv, fake, capsys):
    code = cli.run(argv, client=fake)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_help_exposes_the_declared_tree(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.run(["--help"])
    assert caught.value.code == 0
    out = capsys.readouterr().out
    for command in ("doctor", "nameservers", "zones", "rrsets", "zone"):
        assert command in out
    with pytest.raises(SystemExit):
        cli.run(["zones", "--help"])
    out = capsys.readouterr().out
    for command in ("list", "create", "show", "delete"):
        assert command in out


def test_zone_create_is_plan_then_apply(capsys) -> None:
    fake = FakePowerDns()
    code, out, _err = run(
        ["zones", "create", "example.com", "--json"], fake, capsys
    )
    assert code == 3
    payload = json.loads(out)
    assert payload["drift"] is True
    assert "example.com" not in fake.zones

    code, out, _err = run(
        [
            "zones",
            "create",
            "example.com",
            "--ns1",
            "ns1.example.com",
            "--ns2",
            "ns2.example.com",
            "--ns1-ipv4",
            "192.0.2.53",
            "--json",
            "--apply",
        ],
        fake,
        capsys,
    )
    assert code == 0, out
    assert '"applied": true' in out
    assert fake.zones["example.com"].rrset("example.com", "SOA") is not None
    assert fake.zones["example.com"].rrset("ns1.example.com", "A").values() == (
        "192.0.2.53",
    )


def test_rrset_plan_apply_stale_and_delete(capsys) -> None:
    fake = FakePowerDns()
    run(
        [
            "zones",
            "create",
            "example.com",
            "--ns1",
            "ns1.example.com",
            "--ns2",
            "ns2.example.com",
            "--apply",
        ],
        fake,
        capsys,
    )
    version = zone_version(fake.zones["example.com"])
    code, out, _err = run(
        [
            "rrsets",
            "plan",
            "example.com",
            "--name",
            "www",
            "--type",
            "A",
            "--ttl",
            "300",
            "--value",
            "192.0.2.10",
            "--json",
        ],
        fake,
        capsys,
    )
    assert code == 3
    assert json.loads(out)["drift"] is True

    apply_args = [
        "rrsets",
        "apply",
        "example.com",
        "--name",
        "www",
        "--type",
        "A",
        "--ttl",
        "300",
        "--value",
        "192.0.2.10",
        "--expected-version",
        version,
        "--apply",
        "--json",
    ]
    code, out, _err = run(apply_args, fake, capsys)
    assert code == 0, out
    assert fake.zones["example.com"].rrset("www.example.com", "A").values() == (
        "192.0.2.10",
    )

    stale = list(apply_args)
    stale[stale.index("--value") + 1] = "192.0.2.11"
    code, out, _err = run(stale, fake, capsys)
    assert code == 4
    assert "192.0.2.11" not in json.dumps(fake.zones["example.com"].to_dict())

    current = zone_version(fake.zones["example.com"])
    missing_confirm = [
        "rrsets",
        "delete",
        "example.com",
        "--name",
        "www",
        "--type",
        "A",
        "--expected-version",
        current,
        "--apply",
    ]
    code, _out, _err = run(missing_confirm, fake, capsys)
    assert code == 2
    with_confirm = [*missing_confirm, "--confirm-name", "www.example.com"]
    code, _out, _err = run(with_confirm, fake, capsys)
    assert code == 0
    assert fake.zones["example.com"].rrset("www.example.com", "A") is None


def test_zone_export_import_round_trip_is_deterministic(tmp_path: Path, capsys) -> None:
    fake = FakePowerDns()
    run(
        [
            "zones",
            "create",
            "example.com",
            "--ns1",
            "ns1.example.com",
            "--ns2",
            "ns2.example.com",
            "--apply",
        ],
        fake,
        capsys,
    )
    exported = tmp_path / "example.zone"
    code, out, _err = run(["zone", "export", "example.com"], fake, capsys)
    assert code == 0
    first = out
    assert first.startswith("$ORIGIN example.com.")
    code, _out, _err = run(
        ["zone", "export", "example.com", "--output", str(exported)], fake, capsys
    )
    assert code == 0
    assert exported.read_text(encoding="utf-8") == first
    assert "$ORIGIN example.com." in first
    assert "$INCLUDE" not in first

    fake.delete_zone("example.com")
    code, out, _err = run(
        [
            "zone",
            "import",
            "example.com",
            "--file",
            str(exported),
            "--apply",
            "--json",
        ],
        fake,
        capsys,
    )
    assert code == 0, out
    second_path = tmp_path / "second.zone"
    run(["zone", "export", "example.com", "--output", str(second_path)], fake, capsys)
    assert second_path.read_text(encoding="utf-8") == first


def test_zone_delete_requires_exact_confirmation(capsys) -> None:
    fake = FakePowerDns()
    run(
        [
            "zones",
            "create",
            "example.com",
            "--ns1",
            "ns1.example.com",
            "--ns2",
            "ns2.example.com",
            "--apply",
        ],
        fake,
        capsys,
    )
    version = zone_version(fake.zones["example.com"])
    code, _out, _err = run(
        [
            "zones",
            "delete",
            "example.com",
            "--expected-version",
            version,
            "--apply",
        ],
        fake,
        capsys,
    )
    assert code == 2
    code, _out, _err = run(
        [
            "zones",
            "delete",
            "example.com",
            "--expected-version",
            version,
            "--confirm-zone",
            "example.com",
            "--apply",
        ],
        fake,
        capsys,
    )
    assert code == 0
    assert fake.zones == {}


def test_no_shell_or_api_shortcut_exists() -> None:
    for path in (
        Path(cli.__file__),
        Path(cli.__file__).with_name("cli_commands.py"),
    ):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "curl", "pdnsutil", "psql"):
            assert forbidden not in text


def test_doctor_reports_safe_configuration_without_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    key = tmp_path / "key"
    key.write_text("doctor-secret-value", encoding="utf-8")
    key.chmod(0o600)
    monkeypatch.setenv("SPANEL_PDNS_API_URL", "http://pdns.internal:8081")
    monkeypatch.setenv("SPANEL_PDNS_API_KEY_FILE", str(key))
    config = PowerDnsConfig.from_env()

    def transport(method, url, headers, body, timeout):
        return 200, {}, b'{"id": "localhost"}'

    client = PowerDnsClient(config, transport=transport)
    code = cli.run(["doctor", "--json"], client=client)
    captured = capsys.readouterr()
    assert code == 0
    assert "doctor-secret-value" not in captured.out
    assert "pdns.internal" not in captured.out
    assert json.loads(captured.out)["authenticated"] is True


def test_missing_or_public_key_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("SPANEL_PDNS_API_URL", "http://pdns.internal:8081")
    monkeypatch.setenv("SPANEL_PDNS_API_KEY_FILE", str(tmp_path / "missing"))
    code = cli.run(["doctor"])
    assert code == 2
    captured = capsys.readouterr()
    assert "missing" in captured.err

    public = tmp_path / "public"
    public.write_text("x", encoding="utf-8")
    public.chmod(0o644)
    monkeypatch.setenv("SPANEL_PDNS_API_KEY_FILE", str(public))
    config = PowerDnsConfig(api_url="http://pdns.internal:8081", api_key_file=public)

    def transport(method, url, headers, body, timeout):
        return 200, {}, b'{"id": "localhost"}'

    code = cli.run(
        ["doctor"], client=PowerDnsClient(config, transport=transport)
    )
    assert code == 2
    assert "owner-only" in capsys.readouterr().err


def test_nameserver_apply_requires_explicit_zone_set_and_allows_bootstrap(
    tmp_path: Path, capsys
) -> None:
    import systutor.kernel.models  # noqa: F401
    from plugins.domains.backend.dns.store import get_public_settings
    from plugins.domains.backend.models import DnsPublicSettings  # noqa: F401
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from systutor.core.database import Base

    database = tmp_path / "nameservers.db"
    url = f"sqlite+pysqlite:///{database}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    fake = FakePowerDns()
    base_args = [
        "nameservers",
        "apply",
        "--db-url",
        url,
        "--ns1",
        "ns1.example.com",
        "--ns2",
        "ns2.example.com",
        "--soa-contact",
        "hostmaster@example.com",
        "--apply",
        "--json",
    ]
    try:
        code, out, _err = run(base_args, fake, capsys)
        assert code == 2
        assert "omitted zones" in out
        with Session(engine) as db:
            assert get_public_settings(db) is None

        code, out, _err = run([*base_args, "--no-zones"], fake, capsys)
        assert code == 0, out
        with Session(engine) as db:
            settings = get_public_settings(db)
            assert settings is not None
            assert settings.ns1_hostname == "ns1.example.com"
        assert fake.zones == {}

        code, out, _err = run(
            [*base_args, "--no-zones", "--zone-version", "badformat"], fake, capsys
        )
        assert code == 2
        assert "ZONE=VERSION" in out
    finally:
        engine.dispose()


def test_zonefile_rejects_include_paths_and_outside_records() -> None:
    from plugins.domains.backend.dns.errors import DnsValidationError
    from plugins.domains.backend.dns.zonefile import parse_zonefile

    with pytest.raises(DnsValidationError):
        parse_zonefile("$INCLUDE other.zone\n", origin="example.com")
    with pytest.raises(DnsValidationError):
        parse_zonefile(
            "$TTL 300\nwww.example.com. 300 IN A 192.0.2.1\n"
            "bad.test. 300 IN A 192.0.2.2\n",
            origin="example.com",
        )


def test_exit_codes_are_declared_for_powerdns_errors() -> None:
    from plugins.domains.backend.dns.errors import (
        DnsConflictError,
        DnsValidationError,
        PowerDnsTimeout,
        PowerDnsUnavailable,
    )

    assert DnsValidationError.exit_code == 2
    assert DnsConflictError.exit_code == 4
    assert PowerDnsUnavailable.exit_code == 5
    assert PowerDnsTimeout.exit_code == 5
