"""Local and SSH command transport."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .types import AppDeployError, Completed


class Runner:
    """Run argv locally or through SSH without placing passwords in argv."""

    def __init__(self, transport: str = "local", environ: dict[str, str] | None = None):
        self.transport = transport
        self.environ = dict(os.environ if environ is None else environ)
        if transport == "ssh":
            self.host = self.environ.get("SPANEL_DOCKER_SSH_HOST", "").strip()
            self.user = self.environ.get("SPANEL_DOCKER_SSH_USER", "").strip()
            self.port = self.environ.get("SPANEL_DOCKER_SSH_PORT", "22").strip()
            self.strict = self.environ.get(
                "SPANEL_DOCKER_SSH_STRICT_HOST_KEY", "yes"
            ).strip()
            self.password = self.environ.get("SPANEL_DOCKER_SSH_PASSWORD", "").strip()
            if not self.password:
                self.password = self.environ.get("MAIL_SERVER_PASSWORD", "").strip()
            if not self.host or not self.user or not self.port.isdigit():
                raise AppDeployError(
                    "SSH requires valid SPANEL_DOCKER_SSH_HOST/USER/PORT"
                )

    def _ssh_argv(self, command: str) -> tuple[list[str], dict[str, str]]:
        argv: list[str] = []
        env = dict(self.environ)
        if self.password:
            binary = shutil.which("sshpass")
            if not binary:
                raise AppDeployError(
                    "sshpass is required for configured SSH password authentication"
                )
            argv.extend([binary, "-e"])
            env["SSHPASS"] = self.password
        argv.extend(
            [
                "ssh",
                "-p",
                self.port,
                "-o",
                f"StrictHostKeyChecking={self.strict}",
                "--",
                f"{self.user}@{self.host}",
                command,
            ]
        )
        return argv, env

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin: bytes | None = None,
        timeout: int = 600,
        check: bool = True,
    ) -> Completed:
        if self.transport == "ssh":
            if cwd is not None:
                command = f"cd {shlex.quote(str(cwd))} && {shlex.join(list(argv))}"
            else:
                command = shlex.join(list(argv))
            actual, env = self._ssh_argv(command)
            actual_cwd = None
        else:
            actual, env, actual_cwd = list(argv), self.environ, cwd
        try:
            result = subprocess.run(
                actual,
                cwd=actual_cwd,
                input=stdin,
                capture_output=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppDeployError(f"command failed to execute: {argv[0]}") from exc
        completed = Completed(
            result.returncode,
            result.stdout.decode("utf-8", "replace"),
            result.stderr.decode("utf-8", "replace"),
        )
        if check and completed.returncode:
            # Command output may contain expanded environment values, so do not echo it.
            raise AppDeployError(f"command failed ({completed.returncode}): {argv[0]}")
        return completed
