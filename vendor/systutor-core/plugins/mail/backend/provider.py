from __future__ import annotations

import logging
import shlex
import subprocess
from abc import ABC, abstractmethod

from systutor.core.errors import AppError, NotFoundError

logger = logging.getLogger(__name__)


class MailProvider(ABC):
    @abstractmethod
    def list_accounts(self) -> list[str]: ...

    @abstractmethod
    def create_account(self, email: str, password: str) -> None: ...

    @abstractmethod
    def change_password(self, email: str, password: str) -> None: ...


def _validate_email(email: str) -> None:
    if not email or "@" not in email:
        raise AppError("Invalid email", status_code=422, code="invalid_email")
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        raise AppError("Invalid email", status_code=422, code="invalid_email")


def _validate_container(container: str) -> None:
    if not container or not container.replace("-", "").replace("_", "").isalnum():
        raise AppError("Invalid container name", status_code=500, code="invalid_config")


def _run_local(args: list[str], stdin_data: str | None = None, timeout: int = 30) -> str:
    logger.debug("Local exec: %s", args[0])
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
        if proc.returncode != 0:
            raise AppError(
                f"DMS command failed: {stderr.strip() or stdout.strip()}",
                status_code=500,
                code="dms_error",
            )
        return stdout.strip()
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise AppError("Command timed out", status_code=503, code="timeout") from exc
    except FileNotFoundError as exc:
        raise AppError("Command not found", status_code=500, code="not_found") from exc


class DockerMailServerProvider(MailProvider):
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        container: str,
        use_ssh: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._container = container
        self._use_ssh = use_ssh

    def _exec(self, docker_cmd: str, stdin_data: str | None = None) -> str:
        container = shlex.quote(self._container)

        if self._use_ssh:
            import os

            os.environ["SSHPASS"] = self._password
            remote_cmd = docker_cmd.replace("{container}", container)
            ssh_cmd = [
                "sshpass", "-e", "ssh", "-t",
                "-o", "StrictHostKeyChecking=no",
                "-p", str(self._port),
                f"{self._user}@{self._host}",
                remote_cmd,
            ]
            return _run_local(ssh_cmd, stdin_data=stdin_data)

        local_cmd = ["docker", "exec"] + (
            ["-i"] if stdin_data else []
        ) + [container] + docker_cmd.replace("{container}", "").split()
        return _run_local(local_cmd, stdin_data=stdin_data)

    def list_accounts(self) -> list[str]:
        _validate_container(self._container)
        output = self._exec("docker exec {container} setup email list")
        if not output:
            return []
        accounts = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            email = parts[1] if parts[0] == "*" else parts[0]
            if "@" in email:
                accounts.append(email)
        return accounts

    def create_account(self, email: str, password: str) -> None:
        _validate_email(email)
        _validate_container(self._container)
        try:
            if self._use_ssh:
                safe_pw = shlex.quote(password)
                command = (
                    f"echo {safe_pw} > /tmp/.dms_pw && echo {safe_pw} >> /tmp/.dms_pw"
                    f" && cat /tmp/.dms_pw | docker exec -i {{container}}"
                    f" setup email add {shlex.quote(email)} && rm -f /tmp/.dms_pw"
                )
                self._exec(command)
            else:
                self._exec(
                    "docker exec -i {container} setup email add " + shlex.quote(email),
                    stdin_data=password,
                )
        except AppError as exc:
            if "already exists" in str(exc).lower():
                raise AppError(
                    f"Account already exists: {email}",
                    status_code=409,
                    code="conflict",
                ) from exc
            raise

    def change_password(self, email: str, password: str) -> None:
        _validate_email(email)
        _validate_container(self._container)
        try:
            if self._use_ssh:
                safe_pw = shlex.quote(password)
                command = (
                    f"echo {safe_pw} > /tmp/.dms_pw && echo {safe_pw} >> /tmp/.dms_pw"
                    f" && cat /tmp/.dms_pw | docker exec -i {{container}}"
                    f" setup email update {shlex.quote(email)} && rm -f /tmp/.dms_pw"
                )
                self._exec(command)
            else:
                self._exec(
                    "docker exec -i {container} setup email update " + shlex.quote(email),
                    stdin_data=password,
                )
        except AppError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(f"Account not found: {email}") from exc
            raise
