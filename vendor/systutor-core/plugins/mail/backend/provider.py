from __future__ import annotations

import logging
import os
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


def build_ssh_command(
    *,
    user: str,
    host: str,
    port: int,
    remote_cmd: str,
    control_path: str | None = None,
    control_persist: int = 0,
) -> list[str]:
    """Builds the `ssh` argv for one remote command.

    Extracted from `_exec` so the transport is verifiable without opening a connection.

    `control_path` empty means "no multiplexing": the argv is then exactly the one this
    provider has always used, which is what makes turning the feature on safe. When it is
    set, ControlMaster=auto reuses a session across calls, and ControlPersist keeps the
    master alive briefly after the last one. `auto` also falls back to a fresh connection
    when the cached one is dead, so a stale socket cannot break a call.
    """
    cmd = ["ssh", "-t", "-o", "StrictHostKeyChecking=no", "-p", str(port)]
    if control_path:
        cmd += [
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={control_path}",
            "-o",
            f"ControlPersist={control_persist}",
        ]
    cmd += [f"{user}@{host}", remote_cmd]
    return cmd


def _prepare_control_dir(control_path: str) -> str | None:
    """Returns a usable ControlPath, or None if multiplexing must be skipped.

    The socket lives in a directory that only its owner can enter. A world-writable
    directory would let any local user pre-create the socket and hijack the SSH session,
    which means speaking to the mail server with our credentials. So the mode is set
    explicitly instead of trusting the umask.

    Any failure (read-only filesystem, missing permissions) returns None and the caller
    falls back to a connection per call. The optimization must never break startup.
    """
    directory = os.path.dirname(control_path)
    if not directory:
        return None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError as exc:
        logger.warning("ssh: ControlPath no disponible (%s), conexión por llamada", exc)
        return None
    return control_path


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
        ssh_control_path: str | None = None,
        ssh_control_persist: int = 60,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._container = container
        self._use_ssh = use_ssh
        self._ssh_control_path = ssh_control_path
        self._ssh_control_persist = ssh_control_persist

    def _exec(self, docker_cmd: str, stdin_data: str | None = None) -> str:
        container = shlex.quote(self._container)

        if self._use_ssh:
            os.environ["SSHPASS"] = self._password
            remote_cmd = docker_cmd.replace("{container}", container)
            control_path = None
            if self._ssh_control_path:
                control_path = _prepare_control_dir(self._ssh_control_path)
            ssh_cmd = [
                "sshpass",
                "-e",
                *build_ssh_command(
                    user=self._user,
                    host=self._host,
                    port=self._port,
                    remote_cmd=remote_cmd,
                    control_path=control_path,
                    control_persist=self._ssh_control_persist,
                ),
            ]
            return _run_local(ssh_cmd, stdin_data=stdin_data)

        inner_cmd = docker_cmd.replace("{container}", "").replace("docker exec", "").strip()
        local_cmd = ["docker", "exec"] + (
            ["-i"] if stdin_data else []
        ) + [self._container] + inner_cmd.split()
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
                    "docker exec {container} setup email add " + shlex.quote(email),
                    stdin_data=f"{password}\n{password}\n",
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
                    "docker exec {container} setup email update " + shlex.quote(email),
                    stdin_data=f"{password}\n{password}\n",
                )
        except AppError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(f"Account not found: {email}") from exc
            raise
