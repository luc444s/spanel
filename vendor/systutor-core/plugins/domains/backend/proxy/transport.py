"""Transporte de comandos: local o por SSH."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .config import Settings
from .core import EXIT_TRANSPORT, ProxyError


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def text(self) -> str:
        return self.stdout if self.stdout.strip() else self.stderr


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult: ...


class LocalRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult:
        result = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        command = CommandResult(list(argv), result.returncode, result.stdout, result.stderr)
        if check and not command.ok:
            raise ProxyError(
                f"falló {' '.join(argv)}: {command.text().strip()}", EXIT_TRANSPORT
            )
        return command


class SshRunner:
    def __init__(self, host: str, user: str | None, port: int, password: str | None) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.password = password

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = False,
        stdin: str | None = None,
        timeout: int | None = 120,
    ) -> CommandResult:
        target = f"{self.user}@{self.host}" if self.user else self.host
        remote = shlex.join(argv)
        command = [
            "ssh",
            "-o",
            "BatchMode=no" if self.password else "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(self.port),
            target,
            remote,
        ]
        env = None
        if self.password:
            command = ["sshpass", "-e", *command]
            env = dict(os.environ, SSHPASS=self.password)
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        output = CommandResult(list(argv), result.returncode, result.stdout, result.stderr)
        if check and not output.ok:
            raise ProxyError(
                f"falló {' '.join(argv)} en {self.host}: {output.text().strip()}",
                EXIT_TRANSPORT,
            )
        return output


def build_runner(settings: Settings) -> Runner:
    if settings.is_remote:
        return SshRunner(
            settings.ssh_host or "",
            settings.ssh_user,
            settings.ssh_port,
            settings.ssh_password,
        )
    return LocalRunner()
