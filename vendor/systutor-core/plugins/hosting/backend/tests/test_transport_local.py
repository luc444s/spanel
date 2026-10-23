"""The `local` transport has to behave like the SSH one, minus the connection.

Both transports receive a whole command line, not an argv. The SSH path sends
that line to the remote shell, which interprets `&&`, `>` and `umask`; the
local path used to `shlex.split` it, so those operators arrived as arguments
of `mkdir` or `cat` and the per-site env file was never written. Running the
line through `sh -c` makes the two paths equivalent.
"""

from __future__ import annotations

from plugins.hosting.backend.transport import build_docker_argv

LOCAL = {"SPANEL_DOCKER_TRANSPORT": "local", "MAIL_SERVER_PASSWORD": "x"}


def test_local_runs_the_line_through_a_shell() -> None:
    linea = "mkdir -p /opt/spanel/sites/x && umask 077 && cat > /opt/spanel/sites/x/wp.env"

    argv = build_docker_argv(linea, env=LOCAL)

    assert argv == ["sh", "-c", linea]


def test_local_keeps_the_docker_command_whole() -> None:
    """The old bug: a second "docker" in front exits 0 with empty stdout."""
    argv = build_docker_argv("docker ps -a --format '{{json .}}'", env=LOCAL)

    assert argv == ["sh", "-c", "docker ps -a --format '{{json .}}'"]


def test_local_never_uses_ssh_or_needs_the_password() -> None:
    argv = build_docker_argv("docker ps", env={"SPANEL_DOCKER_TRANSPORT": "local"})

    assert "sshpass" not in argv
    assert all("ssh" not in elemento for elemento in argv)
