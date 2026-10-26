"""Configuración y transporte del CLI estático.

Se apoya en las mismas variables que el resto de SPanel (`SPANEL_PROXY_*`,
`SPANEL_DOCKER_*`) para que el laboratorio y producción se comporten igual. El
CLI se puede ejecutar en el host Docker (transporte local) o desde otra máquina
por SSH.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
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
        stdin: str | None = None,
        timeout: int | None = 120,
        cwd: Path | None = None,
    ) -> CommandResult: ...


class LocalRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: int | None = 120,
        cwd: Path | None = None,
    ) -> CommandResult:
        result = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
        return CommandResult(list(argv), result.returncode, result.stdout, result.stderr)


class SshRunner:
    """Ejecuta el argv en el host Docker; el stdin viaja por la misma sesión."""

    def __init__(self, host: str, user: str | None, port: int, password: str | None) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.password = password

    def run(
        self,
        argv: Sequence[str],
        *,
        stdin: str | None = None,
        timeout: int | None = 120,
        cwd: Path | None = None,
    ) -> CommandResult:
        target = f"{self.user}@{self.host}" if self.user else self.host
        remote = shlex.join(list(argv))
        if cwd:
            remote = f"cd {shlex.quote(str(cwd))} && {remote}"
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
        return CommandResult(list(argv), result.returncode, result.stdout, result.stderr)


@dataclass(frozen=True)
class StaticSettings:
    repo_root: Path
    profile: str = "internal"
    transport: str = "auto"
    network: str = "spanel-traefik-lab"
    image: str = "nginx:alpine"
    proxy_script: Path = Path()
    python: str = "python3"
    wait_seconds: int = 60
    command_timeout: int = 180
    remote_repo: Path | None = None
    ssh_host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_password: str = field(default="", repr=False)

    def runner(self) -> Runner:
        if self.transport in ("local", "docker"):
            return LocalRunner()
        if self.transport == "ssh":
            return SshRunner(self.ssh_host, self.ssh_user, self.ssh_port, self.ssh_password)
        # auto: si hay daemon local se usa; si no, SSH configurado.
        probe = LocalRunner().run(["docker", "info"], timeout=10)
        if probe.ok:
            return LocalRunner()
        if self.ssh_host:
            return SshRunner(self.ssh_host, self.ssh_user, self.ssh_port, self.ssh_password)
        return LocalRunner()


_ENV_CANDIDATES = (
    Path("vendor/systutor-core/.env"),
    Path(".env"),
)


def load_env_files(repo_root: Path) -> None:
    """Carga los `.env` del repo sin pisar variables ya exportadas."""
    for relative in _ENV_CANDIDATES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip())


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def build_settings(
    *,
    repo_root: Path | None = None,
    profile: str | None = None,
    transport: str | None = None,
    network: str | None = None,
    image: str | None = None,
    wait_seconds: int | None = None,
    remote_repo: str | None = None,
) -> StaticSettings:
    root = (repo_root or repo_root_from_script()).resolve()
    load_env_files(root)

    resolved_profile = (
        profile
        or os.environ.get("SPANEL_STATIC_PROFILE", "").strip()
        or os.environ.get("SPANEL_PROXY_PROFILE", "").strip()
        or os.environ.get("SPANEL_DOMAINS_PROFILE", "").strip()
        or "internal"
    ).lower()
    if resolved_profile not in {"internal", "public"}:
        raise ValueError(f"Perfil desconocido: {resolved_profile}")

    internal = resolved_profile == "internal"
    resolved_transport = (
        transport
        or os.environ.get("SPANEL_STATIC_TRANSPORT", "").strip()
        or os.environ.get("SPANEL_DOCKER_TRANSPORT", "").strip()
        or "auto"
    ).lower()
    if resolved_transport not in {"auto", "local", "docker", "ssh"}:
        raise ValueError(f"Transporte desconocido: {resolved_transport}")

    default_network = "spanel-traefik-lab" if internal else "proxy"

    remote_text = (
        (remote_repo or "").strip()
        or os.environ.get("SPANEL_STATIC_REMOTE_REPO", "").strip()
    )
    if not remote_text and resolved_transport == "ssh":
        # El laboratorio suele tener SPANEL_PROXY_CONFIG_DIR=.../traefik-lab/dynamic;
        # de ahí se deduce la raíz del repo en el host Docker.
        config_dir = os.environ.get("SPANEL_PROXY_CONFIG_DIR", "").strip()
        if config_dir:
            candidate = Path(config_dir)
            if candidate.name == "dynamic" and candidate.parent.name == "traefik-lab":
                remote_text = str(candidate.parents[2])
    remote_root = Path(remote_text).expanduser() if remote_text else None
    script_root = remote_root or root

    return StaticSettings(
        repo_root=root,
        profile=resolved_profile,
        transport=resolved_transport,
        network=network
        or os.environ.get("SPANEL_STATIC_NETWORK", "").strip()
        or os.environ.get("SPANEL_PROXY_NETWORK", "").strip()
        or default_network,
        image=image
        or os.environ.get("SPANEL_STATIC_IMAGE", "").strip()
        or os.environ.get("SPANEL_HOSTING_STATIC_IMAGE", "").strip()
        or "nginx:alpine",
        proxy_script=script_root / "scripts" / "proxy_domain.py",
        python=os.environ.get("SPANEL_STATIC_PYTHON", "").strip()
        or ("python3" if resolved_transport == "ssh" or remote_root else sys.executable),
        wait_seconds=(
            wait_seconds
            if wait_seconds is not None
            else _int_env("SPANEL_STATIC_WAIT", _int_env("SPANEL_PROXY_WAIT", 60))
        ),
        command_timeout=_int_env("SPANEL_STATIC_TIMEOUT", 180),
        remote_repo=remote_root,
        ssh_host=_env("SPANEL_DOCKER_SSH_HOST", "SPANEL_PROXY_SSH_HOST"),
        ssh_user=_env("SPANEL_DOCKER_SSH_USER", "SPANEL_PROXY_SSH_USER"),
        ssh_port=_int_env(
            "SPANEL_DOCKER_SSH_PORT",
            _int_env("SPANEL_PROXY_SSH_PORT", 22),
        ),
        ssh_password=_env(
            "SPANEL_STATIC_SSH_PASSWORD",
            "SPANEL_PROXY_SSH_PASSWORD",
            "SPANEL_DOCKER_SSH_PASSWORD",
            "MAIL_SERVER_PASSWORD",
        ),
    )
