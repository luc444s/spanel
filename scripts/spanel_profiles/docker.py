"""Operaciones Docker con web root y comando de perfil parametrizados.

El transporte (local o SSH con sshpass) vive en `scripts/spanel_static`
y se reutiliza tal cual. Aquí solo argv determinista: los datos de usuario
viajan por stdin y nunca se pegan a un string de shell.
"""

from __future__ import annotations

import time

from spanel_static.config import CommandResult, Runner

from .profiles import ProfileSpec

PULL_TIMEOUT = 600


class DockerError(RuntimeError):
    """Un comando Docker falló; el mensaje ya viene limpio."""


class ProfileDocker:
    def __init__(self, runner: Runner, timeout: int = 180) -> None:
        self.runner = runner
        self.timeout = timeout

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

    def container_running(self, name: str) -> bool:
        result = self.run("inspect", "-f", "{{.State.Running}}", name, check=False)
        return result.ok and result.stdout.strip() == "true"

    def label(self, container: str, key: str) -> str:
        template = '{{index .Config.Labels "' + key + '"}}'
        result = self.run("inspect", "-f", template, container, check=False)
        return result.stdout.strip() if result.ok else ""

    def image_exists(self, image: str) -> bool:
        return self.run("image", "inspect", image, check=False).ok

    def volume_exists(self, name: str) -> bool:
        return self.run("volume", "inspect", name, check=False).ok

    # ---- escritura -------------------------------------------------------

    def create_volume(self, spec: ProfileSpec) -> None:
        self.run("volume", "create", *spec.label_args(), spec.volume)

    def create_container(
        self,
        spec: ProfileSpec,
        network: str,
        *,
        traefik: bool = True,
    ) -> None:
        args = [
            "run",
            "-d",
            "--name",
            spec.container,
            "--network",
            network,
            "--restart",
            "unless-stopped",
            *spec.label_args(),
            *(spec.traefik_args(spec.domain) if traefik else []),
        ]
        if spec.workdir:
            args += ["-w", spec.workdir]
        args += ["-v", f"{spec.volume}:{spec.web_root}"]
        args += [spec.image]
        if spec.command:
            args += ["sh", "-c", spec.command]
        self.run(*args, check=True, timeout=PULL_TIMEOUT)

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
        retries: int = 3,
    ) -> CommandResult:
        """Ejecuta `sh -c script` dentro del contenedor con argumentos citados.

        Con un contenedor recién creado, el daemon ocasionalmente responde
        "No such container" en los primeros exec; son instante-fracasos del
        registro. Reintenta con pausa fija; determinista: 3 intentos, 1s cada uno.
        """
        last = CommandResult(["docker"], 1, "", "")  # placeholder de resultado
        for attempt in range(retries):
            last = self.run(
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                script,
                "spanel-profiles",
                *args,
                stdin=stdin,
                check=False,
            )
            if last.ok and "No such container" not in last.stderr:
                break
            if "No such container" not in last.stderr:
                break
            time.sleep(1)
        if check and not last.ok:
            raise DockerError(f"docker exec falló: {last.text().strip()[:400]}")
        return last

    def write_file(self, container: str, destination: str, content: str) -> None:
        # mkdir + cat por stdin; el destino viaja como argv citado.
        result = self.exec_in(
            container,
            'mkdir -p "$(dirname "$1")" && cat > "$1"',
            destination,
            stdin=content,
            check=False,
        )
        if not result.ok:
            raise DockerError(f"no se pudo escribir {destination}: {result.text().strip()[:200]}")

    def read_file(self, container: str, path: str) -> str | None:
        result = self.exec_in(container, 'cat "$1"', path, check=False)
        return result.stdout if result.ok else None

    def chown(self, container: str, owner: str, path: str) -> None:
        self.exec_in(container, 'chown -R "$1" "$2"', owner, path)

    def http_mark(self, container: str, port: int, marker: str) -> bool:
        """True cuando el contenedor sirve HTTP con el marcador del perfil."""
        result = self.exec_in(
            container,
            'wget -q -O- -T 5 "$1"',
            f"http://127.0.0.1:{port}/",
            check=False,
        )
        return result.ok and marker in result.stdout

    def wait_http(self, spec: ProfileSpec, seconds: int) -> None:
        deadline = time.monotonic() + max(1, seconds)
        while time.monotonic() < deadline:
            if self.container_exists(spec.container) and self.http_mark(
                spec.container, 80, spec.marker
            ):
                return
            time.sleep(2)
        raise DockerError(
            f"El contenedor {spec.container} no sirvió el marcador del perfil "
            f"en {seconds}s; revisá 'docker logs {spec.container}': "
            f"{self.logs(spec.container)[-400:]}"
        )

    def logs(self, container: str, lines: int = 30) -> str:
        result = self.run("logs", "--tail", str(lines), container, check=False)
        return result.text()
