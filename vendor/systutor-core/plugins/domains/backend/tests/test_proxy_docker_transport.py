"""Cableado de ProxyOperations con el transporte Docker (A.SPEC 0029, V2/I5)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from plugins.domains.backend.proxy.config import Settings
from plugins.domains.backend.proxy.operations import ProxyOperations
from plugins.domains.backend.proxy.transport import CommandResult

STATIC = """
providers:
  file:
    directory: /config
certificatesResolvers:
  letsencrypt:
    acme: {}
"""

SITE = "spanel-lucas-wp"


class FakeDockerRunner:
    """Detección + filesystem del contenedor en un solo runner determinista."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.files: dict[str, str] = {}
        self.networks = {
            "spanel-lucas-wp": {"proxy"},
            "traefik": {"proxy"},
            "spanel-app-1": {"proxy"},
        }

    def run(self, argv, *, check=False, stdin=None, timeout=None):
        argv = list(argv)
        self.calls.append((argv, stdin))

        def result(code: int = 0, out: str = "", err: str = "") -> CommandResult:
            return CommandResult(argv, code, out, err)

        if argv[:2] == ["docker", "info"]:
            return result(0, "ok\n")
        if argv[:2] == ["docker", "ps"]:
            return result(0, "traefik\ttraefik:latest\n")
        if argv[:2] == ["docker", "port"]:
            return result(0, "0.0.0.0:9443\n")
        if argv[:2] == ["docker", "inspect"]:
            if len(argv) == 3:
                return result(0, "{}\n")
            fmt, name = argv[3], argv[-1]
            if "State.Running" in fmt:
                return result(0, "true\n")
            if ".Args" in fmt:
                return result(0, "[]\n")
            if ".Mounts" in fmt:
                return result(0, json.dumps([{"Destination": "/config", "RW": True}]))
            if ".Gateway" in fmt:
                return result(0, "172.17.0.1\n")
            if "NetworkSettings.Networks" in fmt:
                return result(0, json.dumps({item: {} for item in self.networks[name]}))
            return result(0, "{}\n")
        if argv[:2] == ["docker", "network"]:
            return result(0)
        if argv[:2] == ["docker", "exec"]:
            rest = argv[2:]
            stdin_flag = rest[:1] == ["-i"]
            if stdin_flag:
                rest = rest[1:]
            container = rest[0]
            command = rest[1:]
            if container != "traefik":
                return result(1, "", "contenedor inesperado")
            if command[:1] == ["cat"]:
                path = command[1]
                if path in ("/traefik.yml", "/etc/traefik/traefik.yml"):
                    return result(0, STATIC)
                if path in self.files:
                    return result(0, self.files[path])
                return result(1, "", "no such file")
            if command[:1] == ["test"]:
                return result(0)
            if command[:2] == ["sh", "-c"]:
                script = command[2]
                if script.startswith("ls -1"):
                    names = sorted(
                        path for path in self.files if path.endswith(".yml")
                    )
                    return result(0, "".join(f"{name}\n" for name in names))
                tmp = re.search(r"cat > (\S+)", script)
                move = re.search(r"&& mv (\S+) (\S+)$", script)
                assert tmp and move, script
                self.files[move.group(2)] = (stdin or "").replace(
                    "certificatesResolvers", "certificatesResolvers"
                )
                return result(0)
            if command[:1] == ["rm"]:
                self.files.pop(command[-1], None)
                return result(0)
        return result(0)


def make_settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        "public",
        env={
            "SPANEL_PROXY_TRANSPORT": "docker",
            "SPANEL_PROXY_STATIC_CONFIG": str(tmp_path / "traefik.yml"),
            "SPANEL_PROXY_BACKUPS_DIR": str(tmp_path / "backups"),
            "SPANEL_PROXY_LOG": str(tmp_path / "proxy.log"),
            "SPANEL_PROXY_WAIT": "0",
            "HOSTNAME": "spanel-app-1",
        },
    )


def test_add_and_remove_roundtrip_inside_traefik(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = FakeDockerRunner()
    ops = ProxyOperations(settings, runner)

    assert ops.add(SITE, "lucas.example.com", wait=0) == 0
    path = "/config/spanel-lucas.yml"
    assert path in runner.files
    text = runner.files[path]
    assert text.startswith("# managed-by: spanel")
    assert "lucas.example.com" in text
    assert "letsencrypt" in text

    # Nada se escribe en el filesystem del proceso: sólo dentro del contenedor.
    assert not (tmp_path / "dynamic").exists()
    assert not list(tmp_path.rglob("spanel-*.yml"))
    # El backup se omite con docker (no se escribe fuera del provider).
    assert not (tmp_path / "backups").exists()

    rows = ops.managed_domains()
    assert [(row.domain, row.container, row.file) for row in rows] == [
        ("lucas.example.com", SITE, "spanel-lucas.yml")
    ]

    assert ops.remove("lucas.example.com") == 0
    assert runner.files == {}

    log = (tmp_path / "proxy.log").read_text(encoding="utf-8")
    assert "transport=docker" in log
    assert "/config" not in log


def test_operations_only_use_read_and_exec_commands(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = FakeDockerRunner()
    ops = ProxyOperations(settings, runner)
    ops.add(SITE, "lucas.example.com", wait=0)
    ops.remove("lucas.example.com")

    forbidden = {"run", "restart", "stop", "kill", "start", "recreate"}
    for argv, _ in runner.calls:
        if argv[:2] == ["docker", "exec"]:
            rest = argv[2:]
            if rest[:1] == ["-i"]:
                rest = rest[1:]
            assert rest[0] == "traefik", "nunca se cambia de contenedor"
        elif argv[:2] == ["docker", "inspect"]:
            assert argv[-1] in {"traefik", SITE, "spanel-app-1"}
        elif argv[:2] == ["docker", "port"]:
            assert argv[2] == "traefik"
        assert not (set(argv[1:2]) & forbidden), argv


def test_certificate_without_probe_host_is_pending_cert(tmp_path: Path) -> None:
    settings = Settings.from_env(
        "public",
        env={
            "SPANEL_PROXY_TRANSPORT": "docker",
            "SPANEL_PROXY_STATIC_CONFIG": str(tmp_path / "traefik.yml"),
            "SPANEL_PROXY_BACKUPS_DIR": str(tmp_path / "backups"),
            "SPANEL_PROXY_LOG": str(tmp_path / "proxy.log"),
            "HOSTNAME": "spanel-app-1",
        },
    )
    runner = FakeDockerRunner()
    runner.networks["traefik"] = {"proxy"}
    runner.networks["spanel-app-1"] = {"spanel_default"}
    ops = ProxyOperations(settings, runner)
    # Sin puerto publicado el sondeo es imposible; se informa, nunca "active".
    runner.run = _without_port(runner.run)  # type: ignore[method-assign]
    info = ops.certificate("lucas.example.com")
    assert info.state == "pending_cert"
    assert info.verified is False


def _without_port(inner):
    def run(argv, **kwargs):
        if argv[:2] == ["docker", "port"]:
            return CommandResult(list(argv), 1, "", "no port")
        return inner(argv, **kwargs)

    return run
