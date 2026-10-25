"""Pruebas unitarias del CLI de dominios (sin Docker)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from proxy_domain import build_parser
from spanel_proxy.config import Settings
from spanel_proxy.core import (
    EXIT_VALIDATION,
    CommandResult,
    ProxyError,
    document_container,
    document_domains,
    document_resolver,
    normalize_domain,
    parse_site_document,
    render_site_yaml,
    site_file_name,
    slug_for,
)
from spanel_proxy.operations import ProxyOperations


class FakeRunner:
    """Runner determinista para las pruebas, sin tocar Docker."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv, *, check=False, stdin=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)

        if argv[:2] == ["docker", "inspect"] and any(
            arg in argv for arg in ("{{.State.Running}}",)
        ):
            return CommandResult(argv, 0, "true\n", "")
        if argv[:2] == ["docker", "inspect"] and any(
            arg.startswith("{{range $k, $v := .NetworkSettings.Networks}}") for arg in argv
        ):
            return CommandResult(argv, 0, "spanel-traefik-lab\n", "")
        if argv[:2] == ["docker", "inspect"] and any(
            arg.startswith('{{(index .NetworkSettings.Networks "spanel-traefik-lab")') for arg in argv
        ):
            return CommandResult(argv, 0, "172.18.0.10\n", "")
        if argv[:2] == ["docker", "inspect"]:
            return CommandResult(argv, 0, "site\n", "")
        if argv[:2] == ["docker", "port"]:
            return CommandResult(argv, 0, "127.0.0.1:9443\n", "")
        if argv[:2] == ["docker", "info"]:
            return CommandResult(argv, 0, "ok\n", "")
        if "network" in argv and "connect" in argv:
            return CommandResult(argv, 0, "", "")
        if argv[:2] == ["docker", "kill"]:
            return CommandResult(argv, 0, "", "")
        if joined.startswith("docker run"):
            return CommandResult(argv, 0, "", "")
        return CommandResult(argv, 0, "", "")


def make_settings(tmp_path: Path) -> Settings:
    env = {
        "SPANEL_PROXY_CONFIG_DIR": str(tmp_path / "dynamic"),
        "SPANEL_PROXY_STATIC_CONFIG": str(tmp_path / "traefik.yml"),
        "SPANEL_PROXY_DNS_HOSTS": str(tmp_path / "dns" / "hosts"),
        "SPANEL_PROXY_CA_FILE": str(tmp_path / "ca" / "root_ca.crt"),
        "SPANEL_PROXY_BACKUPS_DIR": str(tmp_path / "backups"),
        "SPANEL_PROXY_LOG": str(tmp_path / "proxy-domain.log"),
        "SPANEL_PROXY_WAIT": "0",
    }
    return Settings.from_env("internal", env=env)


# ---- dominio --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["lucas.spanel", "lucas.test.spanel", "a-b.spanel"],
)
def test_normalize_internal_accepts(raw: str) -> None:
    assert normalize_domain(raw, "internal") == raw


@pytest.mark.parametrize(
    "raw",
    ["Lucas.Spanel", "lucas.spanel.", " lucas.spanel "],
)
def test_normalize_lowercases_and_trims(raw: str) -> None:
    assert normalize_domain(raw, "internal") == "lucas.spanel"


@pytest.mark.parametrize(
    "raw",
    [
        "https://lucas.spanel",
        "lucas.spanel:9443",
        "lucas.spanel/path",
        "*.spanel",
        "lucas",
        "lucas..spanel",
        "lucas_.spanel",
        "-lucas.spanel",
        "lucas.123",
        "",
    ],
)
def test_normalize_rejects_invalid(raw: str) -> None:
    with pytest.raises(ProxyError) as exc:
        normalize_domain(raw, "internal")
    assert exc.value.exit_code == EXIT_VALIDATION


def test_public_profile_rejects_internal_tld() -> None:
    # `spanel` no es un TLD público: el perfil público lo rechaza.
    with pytest.raises(ProxyError):
        normalize_domain("lucas.spanel", "public")
    assert normalize_domain("lucas.example.com", "public") == "lucas.example.com"


def test_slug_for() -> None:
    assert slug_for("spanel-lucas-test-wp") == "lucas-test"
    assert slug_for("spanel_lucas_wp") == "lucas"
    assert slug_for("demo-wordpress") == "demo-wordpress"
    assert site_file_name("lucas-test") == "spanel-lucas-test.yml"


# ---- YAML -----------------------------------------------------------------


def test_render_and_parse_roundtrip() -> None:
    text = render_site_yaml(
        "spanel-lucas-test-wp",
        ["lucas.spanel", "www.lucas.spanel"],
        "internal",
    )
    assert text.startswith("# managed-by: spanel")
    data = parse_site_document(text)
    assert document_domains(data) == ["lucas.spanel", "www.lucas.spanel"]
    assert document_container(data) == "spanel-lucas-test-wp"
    assert document_resolver(data) == "internal"

    routers = data["http"]["routers"]
    assert routers["spanel-lucas-test-http"]["priority"] == 200
    assert routers["spanel-lucas-test"]["priority"] == 100
    assert routers["spanel-lucas-test-http"]["middlewares"] == ["spanel-lucas-test-redirect"]
    assert (
        data["http"]["middlewares"]["spanel-lucas-test-redirect"]["redirectScheme"]["scheme"]
        == "https"
    )
    assert (
        data["http"]["services"]["spanel-lucas-test"]["loadBalancer"]["servers"][0]["url"]
        == "http://spanel-lucas-test-wp:80"
    )


def test_parse_rejects_unmanaged() -> None:
    with pytest.raises(ProxyError):
        parse_site_document("http: {}\n")


# ---- operaciones ----------------------------------------------------------


def test_add_is_idempotent_and_writes_dns(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = FakeRunner()
    ops = ProxyOperations(settings, runner)

    assert ops.add("spanel-lucas-test-wp", "lucas.spanel", wait=0) == 0
    path = settings.config_dir / "spanel-lucas-test.yml"
    first = path.read_text(encoding="utf-8")
    hosts = (tmp_path / "dns" / "hosts").read_text(encoding="utf-8")
    assert "172.18.0.10 lucas.spanel" in hosts

    assert ops.add("spanel-lucas-test-wp", "lucas.spanel", wait=0) == 0
    assert path.read_text(encoding="utf-8") == first
    hosts_again = (tmp_path / "dns" / "hosts").read_text(encoding="utf-8")
    assert hosts_again.count("lucas.spanel") == 1


def test_add_dry_run_does_not_write(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = FakeRunner()
    ops = ProxyOperations(settings, runner)
    assert ops.add("spanel-lucas-test-wp", "lucas.spanel", dry_run=True) == 0
    assert not (settings.config_dir / "spanel-lucas-test.yml").exists()
    assert not (tmp_path / "dns" / "hosts").exists()


def test_add_rejects_domain_owned_by_other_site(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ops = ProxyOperations(settings, FakeRunner())
    ops.add("spanel-lucas-test-wp", "lucas.spanel", wait=0)
    with pytest.raises(ProxyError) as exc:
        ops.add("spanel-otro-wp", "lucas.spanel", wait=0)
    assert exc.value.exit_code == EXIT_VALIDATION


def test_remove_rewrites_then_deletes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ops = ProxyOperations(settings, FakeRunner())
    ops.add("spanel-lucas-test-wp", "lucas.spanel", wait=0)
    ops.add("spanel-lucas-test-wp", "otro.spanel", wait=0)

    path = settings.config_dir / "spanel-lucas-test.yml"
    assert ops.remove("lucas.spanel") == 0
    remaining = document_domains(parse_site_document(path.read_text(encoding="utf-8")))
    assert remaining == ["otro.spanel"]
    hosts = (tmp_path / "dns" / "hosts").read_text(encoding="utf-8")
    assert "lucas.spanel" not in hosts
    assert "otro.spanel" in hosts

    assert ops.remove("otro.spanel") == 0
    assert not path.exists()


def test_remove_unknown_domain(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    ops = ProxyOperations(settings, FakeRunner())
    with pytest.raises(ProxyError):
        ops.remove("nadie.spanel")


def test_list_domains(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_settings(tmp_path)
    ops = ProxyOperations(settings, FakeRunner())
    ops.add("spanel-lucas-test-wp", "lucas.spanel", wait=0)
    ops.list_domains()
    output = capsys.readouterr().out
    assert "lucas.spanel" in output
    assert "spanel-lucas-test-wp" in output
    assert "internal" in output


def test_doctor_fails_without_lab(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = make_settings(tmp_path)
    settings = Settings.from_env(
        "internal",
        env={
            "SPANEL_PROXY_CONFIG_DIR": str(tmp_path / "dynamic"),
            "SPANEL_PROXY_STATIC_CONFIG": str(tmp_path / "traefik.yml"),
            "SPANEL_PROXY_DNS_HOSTS": str(tmp_path / "dns" / "hosts"),
            "SPANEL_PROXY_CA_FILE": str(tmp_path / "ca" / "root_ca.crt"),
            "SPANEL_PROXY_BACKUPS_DIR": str(tmp_path / "backups"),
            "SPANEL_PROXY_LOG": str(tmp_path / "proxy-domain.log"),
        },
    )
    ops = ProxyOperations(settings, FakeRunner())
    code = ops.doctor()
    assert code == 4
    out = capsys.readouterr().out
    assert "docker" in out
    assert "FALLA" in out


def test_cli_accepts_profile_before_and_after_command() -> None:
    parser = build_parser()
    before = parser.parse_args(["--profile", "public", "status", "--domain", "a.example.com"])
    after = parser.parse_args(["status", "--domain", "a.example.com", "--profile", "public"])
    assert before.profile == "public"
    assert after.profile == "public"


def test_update_wordpress_passes_env_without_argv_secrets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = FakeRunner()
    ops = ProxyOperations(settings, runner)
    ops._update_wordpress("spanel-lucas-wp", "lucas.spanel")
    script = runner.calls[-1][2]
    assert "--env-file" in script
    assert "option update home" in script
    assert "option update siteurl" in script
    assert "WORDPRESS_DB_PASSWORD" not in script
