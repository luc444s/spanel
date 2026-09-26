"""Tests for the SSH transport of DockerMailServerProvider.

A.SPEC 0006. The part worth testing is the argv that gets built and the conditions under
which multiplexing is skipped: a wrong flag order or a world-writable socket directory are
both invisible until production, and the second one is a security problem.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from plugins.mail.backend.provider import (
    DockerMailServerProvider,
    _prepare_control_dir,
    build_ssh_command,
)
from plugins.mail.backend.settings import MailSettings

BASE = {"user": "lucas", "host": "100.67.5.50", "port": 22, "remote_cmd": "true"}


# --- I1: sin control path, el argv es el de siempre ----------------------------


def test_ssh_command_unchanged_without_control_path():
    cmd = build_ssh_command(**BASE)

    assert cmd == [
        "ssh",
        "-t",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        "22",
        "lucas@100.67.5.50",
        "true",
    ]


def test_ssh_command_default_control_path_is_off():
    """The default of the pure function must be the pre-0006 behaviour, not a happy accident."""
    assert build_ssh_command(**BASE, control_path="") == build_ssh_command(**BASE)
    assert build_ssh_command(**BASE, control_path=None) == build_ssh_command(**BASE)


# --- I2: con control path, los flags van antes del destino --------------------


def test_ssh_command_includes_control_flags():
    cmd = build_ssh_command(
        **BASE, control_path="/tmp/ssh-cm/cm", control_persist=90
    )

    assert cmd == [
        "ssh",
        "-t",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        "22",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=/tmp/ssh-cm/cm",
        "-o",
        "ControlPersist=90",
        "lucas@100.67.5.50",
        "true",
    ]


def test_ssh_command_uses_auto_master_so_a_dead_socket_recovers():
    """ControlMaster=auto must never be a hard requirement on a live socket."""
    cmd = build_ssh_command(**BASE, control_path="/tmp/ssh-cm/cm")
    assert "ControlMaster=auto" in cmd
    assert "ControlMaster=yes" not in cmd


def test_destination_is_the_last_argument():
    """ssh parses the destination positionally, so it has to come last."""
    for control in (None, "/tmp/ssh-cm/cm"):
        cmd = build_ssh_command(**BASE, control_path=control)
        assert cmd[-2] == "lucas@100.67.5.50"
        assert cmd[-1] == "true"


# --- I3: el directorio del socket es privado -----------------------------------


def test_control_path_directory_is_private(tmp_path: Path):
    control = str(tmp_path / "nested" / "cm")
    # A permissive umask must not decide the mode of the socket directory.
    old = os.umask(0o000)
    try:
        assert _prepare_control_dir(control) == control
    finally:
        os.umask(old)

    mode = stat.S_IMODE(os.stat(os.path.dirname(control)).st_mode)
    assert mode == 0o700


def test_control_path_directory_stays_private_when_it_already_exists(tmp_path: Path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o777)
    os.chmod(directory, 0o777)

    _prepare_control_dir(str(directory / "cm"))

    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700


# --- I4: si no se puede crear, degrada y no lanza ------------------------------


def test_unwritable_control_path_degrades_gracefully(tmp_path: Path):
    # A file where the directory should be: makedirs cannot win.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    assert _prepare_control_dir(str(blocker / "cm")) is None


def test_control_path_without_directory_degrades():
    assert _prepare_control_dir("cm") is None


def test_provider_executes_even_when_control_dir_is_unavailable(monkeypatch, tmp_path: Path):
    """The optimization must not be able to break a call."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    captured: dict[str, list[str]] = {}

    def fake_run(args, stdin_data=None, timeout=30):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr("plugins.mail.backend.provider._run_local", fake_run)
    monkeypatch.setenv("SSHPASS", "secret")

    provider = DockerMailServerProvider(
        host="100.67.5.50",
        port=22,
        user="lucas",
        password="secret",
        container="spanel-mail",
        use_ssh=True,
        ssh_control_path=str(blocker / "cm"),
    )
    assert provider.list_accounts.__self__ is provider  # sanity: bound method
    provider._exec("docker exec {container} setup email list")

    assert "ControlPath" not in captured["args"]
    assert captured["args"][-2] == "lucas@100.67.5.50"


# --- I5: la contraseña no aparece en el argv -----------------------------------


def test_password_never_appears_in_argv(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str]] = {}

    def fake_run(args, stdin_data=None, timeout=30):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr("plugins.mail.backend.provider._run_local", fake_run)
    monkeypatch.setenv("SSHPASS", "hunter2")

    provider = DockerMailServerProvider(
        host="100.67.5.50",
        port=22,
        user="lucas",
        password="hunter2",
        container="spanel-mail",
        use_ssh=True,
        ssh_control_path=str(tmp_path / "cm"),
    )
    provider._exec("docker exec {container} setup email list")

    assert "hunter2" not in " ".join(captured["args"])
    # It travels through the environment, which is what sshpass -e reads.
    assert os.environ["SSHPASS"] == "hunter2"
    assert captured["args"][:3] == ["sshpass", "-e", "ssh"]


# --- I6: el camino local de produccion no se toca ------------------------------


def test_local_docker_exec_command_unchanged(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(args, stdin_data=None, timeout=30):
        captured["args"] = args
        return ""

    monkeypatch.setattr("plugins.mail.backend.provider._run_local", fake_run)

    provider = DockerMailServerProvider(
        host="mail",
        port=22,
        user="root",
        password="x",
        container="mailserver",
        use_ssh=False,
        ssh_control_path="/tmp/ignored/cm",
    )
    provider._exec("docker exec {container} setup email list")

    assert captured["args"][:3] == ["docker", "exec", "mailserver"]


# --- I9: el default del TTL cambió ---------------------------------------------


def test_default_cache_ttl_is_300():
    assert MailSettings().mail_accounts_cache_ttl == 300


def test_frontend_stale_time_is_narrower_than_the_cache_ttl():
    """
    The whole point of the 300: the client refetches at 60 s and the backend still has the
    list, so the refetch is a Redis hit instead of a trip to the mail server.
    """
    mail_plugin = Path(__file__).resolve().parents[2]
    frontend_source = mail_plugin / "frontend" / "pages" / "MailAccountsPage.tsx"
    source = frontend_source.read_text(encoding="utf-8")
    stale_ms = int(
        next(
            line for line in source.splitlines() if "ACCOUNTS_STALE_TIME_MS =" in line
        ).split("=")[1].strip().rstrip(";")
    )

    assert stale_ms / 1000 < MailSettings().mail_accounts_cache_ttl


# --- el provider acepta los dos argumentos nuevos ------------------------------


def test_provider_defaults_disable_multiplexing():
    provider = DockerMailServerProvider(
        host="h", port=22, user="u", password="p", container="c", use_ssh=True
    )
    assert provider._ssh_control_path is None
    assert provider._ssh_control_persist == 60


@pytest.mark.parametrize("persist", [0, 30, 600])
def test_control_persist_is_passed_through(persist: int):
    cmd = build_ssh_command(**BASE, control_path="/tmp/cm", control_persist=persist)
    assert f"ControlPersist={persist}" in cmd
