"""Detección de transporte con runner falso (A.SPEC 0029, V1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.domains.backend.proxy.config import Settings
from plugins.domains.backend.proxy.core import EXIT_TRANSPORT, ProxyError
from plugins.domains.backend.proxy.detect import (
    choose_container,
    detect_docker,
    directory_from_args,
    parse_ps,
    parse_static_config,
    select_transport,
)
from plugins.domains.backend.proxy.transport import CommandResult

STATIC_OK = """
providers:
  file:
    directory: /config
    watch: true
certificatesResolvers:
  letsencrypt:
    acme: {}
"""


class FakeDocker:
    def __init__(
        self,
        *,
        info: bool = True,
        ps: tuple[tuple[str, str], ...] = (),
        static: str | None = STATIC_OK,
        args: list[str] | None = None,
        mounts: list[dict] | None = None,
        running: bool = True,
        writable: bool = True,
        networks: tuple[str, ...] = ("proxy",),
        own_networks: tuple[str, ...] = ("proxy",),
        published: str = "0.0.0.0:9443\n",
        hostname: str = "spanel-app-1",
        gateway: str = "172.17.0.1",
    ) -> None:
        self.info = info
        self.ps = ps
        self.static = static
        self.args = args or []
        self.mounts = mounts if mounts is not None else [
            {"Destination": "/config", "RW": True}
        ]
        self.running = running
        self.writable = writable
        self.networks = networks
        self.own_networks = own_networks
        self.published = published
        self.hostname = hostname
        self.gateway = gateway
        self.calls: list[list[str]] = []

    def run(self, argv, *, check=False, stdin=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)

        def result(code: int = 0, out: str = "", err: str = "") -> CommandResult:
            return CommandResult(argv, code, out, err)

        if argv[:2] == ["docker", "info"]:
            return result(0 if self.info else 1, "", "" if self.info else "daemon caído")
        if argv[:2] == ["docker", "ps"]:
            rows = "".join(f"{name}\t{image}\n" for name, image in self.ps)
            return result(0, rows)
        if argv[:2] == ["docker", "inspect"]:
            fmt = argv[3]
            name = argv[-1]
            if "State.Running" in fmt:
                return result(0, ("true" if self.running else "false") + "\n")
            if ".Args" in fmt:
                return result(0, json.dumps(self.args))
            if ".Mounts" in fmt:
                return result(0, json.dumps(self.mounts))
            if ".Gateway" in fmt:
                return result(0, f"{self.gateway}\n" if self.gateway else "")
            if "NetworkSettings.Networks" in fmt:
                members = self.own_networks if name == self.hostname else self.networks
                return result(0, json.dumps({item: {} for item in members}))
            return result(0, "{}\n")
        if argv[:2] == ["docker", "exec"]:
            rest = argv[2:]
            if rest[:1] == ["-i"]:
                rest = rest[1:]
            command = rest[1:]
            if command[:1] == ["cat"]:
                if self.static is None:
                    return result(1, "", "no such file")
                return result(0, self.static)
            if command[:1] == ["test"]:
                return result(0 if self.writable else 1)
            if command[:2] == ["sh", "-c"]:
                return result(0, "")
            return result(0, "")
        if argv[:2] == ["docker", "port"]:
            return result(0, self.published) if self.published else result(1, "", "no port")
        return result(0, "")


def make_settings(tmp_path: Path, config_dir: str | None = None, **overrides: str):
    env = {
        "SPANEL_PROXY_STATIC_CONFIG": str(tmp_path / "traefik.yml"),
        "SPANEL_PROXY_BACKUPS_DIR": str(tmp_path / "backups"),
        "SPANEL_PROXY_LOG": str(tmp_path / "proxy.log"),
        "HOSTNAME": "spanel-app-1",
    }
    if config_dir is not None:
        env["SPANEL_PROXY_CONFIG_DIR"] = config_dir
    env.update(overrides)
    return Settings.from_env("public", env=env), env


# ---- parsing puro ----------------------------------------------------------


def test_parse_ps_and_preference() -> None:
    rows = parse_ps("b\ttraefik:v3\na\ttraefik:latest\nweb\tnginx\n")
    assert rows == [("b", "traefik:v3"), ("a", "traefik:latest"), ("web", "nginx")]
    assert choose_container(rows, "elegido") == "elegido"
    assert choose_container([("spanel-traefik-lab", "traefik"), ("traefik", "traefik")], None) == "traefik"


def test_parse_static_config_and_args() -> None:
    directory, resolvers = parse_static_config(STATIC_OK)
    assert directory == "/config"
    assert resolvers == ("letsencrypt",)
    assert directory_from_args(["--providers.file.directory=/srv/dyn"]) == "/srv/dyn"
    assert directory_from_args(["--providers.file.directory", "/srv/dyn"]) == "/srv/dyn"
    assert directory_from_args(["--log.level=INFO"]) is None


# ---- selección automática --------------------------------------------------


def test_auto_prefers_ssh_when_defined(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_SSH_HOST="host.example")
    runner = FakeDocker(info=False)
    detection = select_transport(settings, runner, env=env)
    assert detection.transport == "ssh"
    assert detection.probe_host == "host.example"
    assert runner.calls == [], "SSH definido no debe ni consultar Docker"


def test_auto_falls_back_to_local_when_docker_is_down(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, config_dir=str(tmp_path / "dynamic"))
    detection = select_transport(settings, FakeDocker(info=False), env=env)
    assert detection.transport == "local"
    assert detection.config_dir == settings.config_dir


def test_auto_without_transport_reports_every_check(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "dynamic"
    settings, env = make_settings(tmp_path, SPANEL_PROXY_CONFIG_DIR=str(missing))
    with pytest.raises(ProxyError) as exc:
        select_transport(settings, FakeDocker(info=False), env=env)
    assert exc.value.exit_code == EXIT_TRANSPORT
    message = str(exc.value)
    assert "ssh:" in message
    assert "docker:" in message
    assert "local:" in message


def test_auto_selects_docker_with_single_traefik(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="auto")
    runner = FakeDocker(ps=(("traefik", "traefik:latest"),))
    detection = select_transport(settings, runner, env=env)
    assert detection.transport == "docker"
    assert detection.traefik_container == "traefik"
    assert detection.config_dir == Path("/config")
    assert detection.probe_host == "traefik"
    assert detection.probe_port == 443
    assert detection.profile == "public"
    assert detection.cert_resolver == "letsencrypt"


def test_ambiguous_traefik_fails_and_override_wins(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("a-traefik", "traefik:v3"), ("b-traefik", "traefik:latest"))
    )
    with pytest.raises(ProxyError) as exc:
        detect_docker(settings, runner, env=env)
    assert "ambiguos" in str(exc.value)
    assert "SPANEL_PROXY_TRAEFIK_CONTAINER" in str(exc.value)

    chosen, chosen_env = make_settings(
        tmp_path,
        SPANEL_PROXY_TRANSPORT="docker",
        SPANEL_PROXY_TRAEFIK_CONTAINER="b-traefik",
    )
    runner = FakeDocker(ps=(("a-traefik", "traefik:v3"), ("b-traefik", "traefik:latest")))
    detection = detect_docker(chosen, runner, env=chosen_env)
    assert detection.traefik_container == "b-traefik"
    assert ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"] not in runner.calls


@pytest.mark.parametrize("running,writable", [(False, True), (True, False)])
def test_docker_not_chosen_when_unusable(tmp_path: Path, running: bool, writable: bool) -> None:
    settings, env = make_settings(tmp_path, config_dir=str(tmp_path / "dynamic"))
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),), running=running, writable=writable
    )
    detection = select_transport(settings, runner, env=env)
    assert detection.transport == "local"


def test_read_only_mount_is_not_a_destination(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        mounts=[{"Destination": "/config", "RW": False}],
    )
    with pytest.raises(ProxyError):
        detect_docker(settings, runner, env=env)


def test_static_directory_wins_over_default(tmp_path: Path) -> None:
    static = """
providers:
  file:
    directory: /etc/traefik/dynamic/
certificatesResolvers:
  internal:
    acme: {}
"""
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        static=static,
        mounts=[{"Destination": "/etc/traefik/dynamic", "RW": True}],
    )
    detection = detect_docker(settings, runner, env=env)
    assert detection.config_dir == Path("/etc/traefik/dynamic")
    assert detection.profile == "internal"
    assert detection.cert_resolver == "internal"


def test_args_directory_used_when_static_is_unreadable(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        static=None,
        args=["--providers.file.directory=/srv/dynamic", "--log.level=INFO"],
        mounts=[{"Destination": "/srv/dynamic", "RW": True}],
    )
    detection = detect_docker(settings, runner, env=env)
    assert detection.config_dir == Path("/srv/dynamic")


def test_probe_uses_shared_network_name(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        networks=("proxy",),
        own_networks=("proxy", "spanel_default"),
    )
    detection = detect_docker(settings, runner, env=env)
    assert (detection.probe_host, detection.probe_port) == ("traefik", 443)


def test_probe_without_shared_network_uses_published_port(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        networks=("proxy",),
        own_networks=("spanel_default",),
        published="0.0.0.0:9443\n",
    )
    detection = detect_docker(settings, runner, env=env)
    assert (detection.probe_host, detection.probe_port) == ("172.17.0.1", 9443)


def test_probe_from_host_process_uses_loopback(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker", HOSTNAME="")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        networks=("proxy",),
        published="0.0.0.0:9443\n",
    )
    detection = detect_docker(settings, runner, env=env)
    assert (detection.probe_host, detection.probe_port) == ("127.0.0.1", 9443)


def test_probe_host_override_when_no_shared_network(tmp_path: Path) -> None:
    settings, env = make_settings(
        tmp_path,
        SPANEL_PROXY_TRANSPORT="docker",
        SPANEL_PROXY_PROBE_HOST="proxy.example",
    )
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        networks=("proxy",),
        own_networks=("spanel_default",),
        published="0.0.0.0:9443\n",
    )
    detection = detect_docker(settings, runner, env=env)
    assert (detection.probe_host, detection.probe_port) == ("proxy.example", 9443)


def test_unreachable_probe_leaves_pending_target(tmp_path: Path) -> None:
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(
        ps=(("traefik", "traefik:latest"),),
        networks=("proxy",),
        own_networks=("spanel_default",),
        published="",
    )
    detection = detect_docker(settings, runner, env=env)
    assert detection.probe_host is None
    assert detection.probe_port is None


@pytest.mark.parametrize(
    "resolvers,profile,resolver",
    [
        ({"internal": {}}, "internal", "internal"),
        ({"letsencrypt": {}}, "public", "letsencrypt"),
        ({}, "public", "letsencrypt"),
        ({"internal": {}, "letsencrypt": {}}, "public", "letsencrypt"),
    ],
)
def test_profile_and_resolver_from_static_config(
    tmp_path: Path,
    resolvers: dict,
    profile: str,
    resolver: str,
) -> None:
    static = {
        "providers": {"file": {"directory": "/config"}},
        "certificatesResolvers": resolvers,
    }
    settings, env = make_settings(tmp_path, SPANEL_PROXY_TRANSPORT="docker")
    runner = FakeDocker(ps=(("traefik", "traefik:latest"),), static=json.dumps(static))
    detection = detect_docker(settings, runner, env=env)
    assert detection.profile == profile
    assert detection.cert_resolver == resolver


def test_explicit_resolver_and_profile_env_win(tmp_path: Path) -> None:
    settings, env = make_settings(
        tmp_path,
        SPANEL_PROXY_TRANSPORT="docker",
        SPANEL_PROXY_PROFILE="public",
        SPANEL_PROXY_CERT_RESOLVER="letsencrypt-staging",
    )
    runner = FakeDocker(ps=(("traefik", "traefik:latest"),))
    detection = detect_docker(settings, runner, env=env)
    assert detection.profile == "public"
    assert detection.cert_resolver == "letsencrypt-staging"


def test_explicit_modes_do_not_fallback(tmp_path: Path) -> None:
    settings, env = make_settings(
        tmp_path,
        SPANEL_PROXY_TRANSPORT="ssh",
        SPANEL_PROXY_CONFIG_DIR=str(tmp_path / "dynamic"),
    )
    with pytest.raises(ProxyError):
        select_transport(settings, FakeDocker(info=False), env=env)
    with pytest.raises(ProxyError):
        docker_settings, docker_env = make_settings(
            tmp_path, SPANEL_PROXY_TRANSPORT="docker"
        )
        select_transport(docker_settings, FakeDocker(info=False), env=docker_env)
