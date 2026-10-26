"""Operaciones Docker mínimas para un sitio estático.

No usa la API de Docker: habla con el CLI a través del `Runner`, que puede ser
local o SSH. Todo se construye como argv (nunca un string de shell con datos
del usuario) para que el transporte remoto lo cite correctamente.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CommandResult, Runner
from .names import SiteNames, WEB_ROOT


class DockerError(RuntimeError):
    """Un comando Docker falló; el mensaje ya viene limpio."""


@dataclass
class Docker:
    runner: Runner
    timeout: int = 180

    def run(
        self,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = ["docker", *args]
        result = self.runner.run(argv, stdin=stdin, timeout=timeout or self.timeout)
        if check and not result.ok:
            raise DockerError(f"docker {args[0]} falló: {result.text().strip()[:400]}")
        return result

    # ---- lectura ---------------------------------------------------------

    def network_exists(self, name: str) -> bool:
        return self.run("network", "inspect", name, check=False).ok

    def container_exists(self, name: str) -> bool:
        return self.run("inspect", name, check=False).ok

    def volume_exists(self, name: str) -> bool:
        return self.run("volume", "inspect", name, check=False).ok

    def container_running(self, name: str) -> bool:
        result = self.run("inspect", "-f", "{{.State.Running}}", name, check=False)
        return result.ok and result.stdout.strip() == "true"

    def label(self, container: str, key: str) -> str:
        template = '{{index .Config.Labels "' + key + '"}}'
        result = self.run("inspect", "-f", template, container, check=False)
        return result.stdout.strip() if result.ok else ""

    def image_exists(self, image: str) -> bool:
        return self.run("image", "inspect", image, check=False).ok

    # ---- escritura -------------------------------------------------------

    def create_volume(self, name: str, names: SiteNames, domain: str | None = None) -> None:
        args = ["volume", "create", *names.label_args(domain), name]
        self.run(*args)

    def create_container(
        self,
        names: SiteNames,
        domain: str,
        network: str,
        image: str,
    ) -> None:
        args = [
            "run",
            "-d",
            "--name",
            names.container,
            "--network",
            network,
            "--restart",
            "unless-stopped",
            *names.label_args(domain),
            "-v",
            f"{names.volume}:{WEB_ROOT}",
            image,
        ]
        self.run(*args)

    def remove_container(self, name: str) -> None:
        self.run("rm", "-f", name, check=False)

    def remove_volume(self, name: str) -> None:
        self.run("volume", "rm", name, check=False)

    def exec_in(
        self,
        container: str,
        script: str,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
    ) -> CommandResult:
        """Ejecuta `sh -c script` dentro del contenedor con argumentos citados."""
        return self.run(
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            script,
            "spanel-static",
            *args,
            stdin=stdin,
            check=check,
        )

    def file_exists(self, container: str, path: str) -> bool:
        return self.exec_in(container, 'test -e "$1"', path, check=False).ok

    def write_file(self, container: str, path: str, content: str) -> None:
        self.exec_in(container, 'cat > "$1"', path, stdin=content)

    def read_file(self, container: str, path: str) -> str | None:
        result = self.exec_in(container, 'cat "$1"', path, check=False)
        return result.stdout if result.ok else None

    def chown(self, container: str) -> None:
        self.exec_in(container, "chown -R nginx:nginx \"$1\"", WEB_ROOT)

    def http_ok(self, container: str, marker: str = "") -> bool:
        result = self.exec_in(
            container,
            "wget -q -O - http://127.0.0.1/",
            check=False,
        )
        return result.ok and (not marker or marker in result.stdout)


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise DockerError(message)
