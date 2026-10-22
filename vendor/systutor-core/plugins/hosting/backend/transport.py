"""Transport to the machine that runs Docker.

This module owns the only way this package talks to the Docker daemon: it
reads the environment file and it builds the argv. Nothing here decides what
to run; `discovery`, `database` and `reset` do.

The password of the SSH user never travels in the argv. `sshpass -e` reads it
from `SSHPASS` in the child environment, so it does not show up in `ps`, in a
traceback or in a log.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


class Fallo(Exception):
    """An error that has to be explained to a person, not a traceback."""


def load_env_file(path: Path) -> dict[str, str]:
    """Read a simple .env: key=value, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def build_docker_argv(
    remote_command: str,
    *,
    env: dict[str, str],
) -> list[str]:
    """Build the full argv to run a Docker command on the other machine.

    `remote_command` always travels as a single argv element, never
    concatenated into a text that a shell interprets. The Docker socket is
    equivalent to root on that machine, so a user input glued with `;` would
    be arbitrary execution.
    """
    transport = env.get("SPANEL_DOCKER_TRANSPORT", "ssh")

    if transport == "local":
        # The remote command ALREADY starts with "docker": callers pass
        # "docker ps ...", not "ps ...". Adding another "docker" in front made
        # ["docker", "docker", "ps"], which exits 0 with empty stdout: the
        # worst possible failure, because it looks like there are no
        # containers.
        return shlex.split(remote_command)

    if not env.get("MAIL_SERVER_PASSWORD"):
        raise ValueError("falta MAIL_SERVER_PASSWORD en el .env")

    host = env["SPANEL_DOCKER_SSH_HOST"]
    user = env["SPANEL_DOCKER_SSH_USER"]
    port = env.get("SPANEL_DOCKER_SSH_PORT", "22")
    strict = env.get("SPANEL_DOCKER_SSH_STRICT_HOST_KEY", "yes")

    # OJO: la contraseña NO va en el argv. `sshpass -e` la lee de la variable
    # SSHPASS del entorno. Si fuera con `-p`, quedaria escrita en el comando, y
    # desde ahi se escapa sola: en cualquier traceback de Python, en un `ps`, y
    # en los logs de error. Por eso `run` mete SSHPASS en el entorno del hijo.
    return [
        "sshpass",
        "-e",
        "ssh",
        "-p",
        port,
        "-o",
        f"StrictHostKeyChecking={strict}",
        "-o",
        "LogLevel=ERROR",
        f"{user}@{host}",
        remote_command,
    ]


def run(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: int = 30,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the command. The password travels in the child environment.

    `input` is for the few cases where something has to be written to the
    remote host over stdin instead of being left in the command.
    """
    child_env = dict(os.environ)
    # -e de sshpass lee de acá. Nunca se pasa por el argv.
    if "sshpass" in argv:
        child_env["SSHPASS"] = env.get("MAIL_SERVER_PASSWORD", "")
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=child_env,
            input=input,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout="",
            stderr=f"no respondio en {timeout} segundos",
        )


def redactar(texto: str, secretos: tuple[str, ...] = ()) -> str:
    for secreto in secretos:
        if secreto:
            texto = texto.replace(secreto, "<oculto>")
    return texto


def pedir(
    env: dict[str, str],
    comando: str,
    *,
    secretos: tuple[str, ...] = (),
    entrada: str | None = None,
) -> str:
    """Run a command and return stdout. Fail with a message for a person.

    `secretos` are redacted before building the message: the command may
    carry a secret, and an error that prints it is a secret in the log.
    `entrada` goes over stdin, for what must not appear in the command.
    """
    r = run(build_docker_argv(comando, env=env), env=env, input=entrada)
    if r.returncode != 0:
        raise Fallo(
            redactar(
                f"el comando fallo:\n  {comando}\n  {(r.stderr or r.stdout).strip()[:400]}",
                secretos,
            )
        )
    return r.stdout


def pedir_palabras(env: dict[str, str], comando: str) -> list[str]:
    return [line.strip() for line in pedir(env, comando).splitlines() if line.strip()]


def escribir_archivo_remoto(env: dict[str, str], contenido: str, etiqueta: str) -> str:
    """Leave content in a mode-600 file on the remote host, outside the argv.

    Careful who reads this file: it is read by the Docker DAEMON, which runs
    as root, so 600 is fine. A file mounted INSIDE a container is read by the
    container user, which in `wordpress:cli` is www-data (uid 82), and there
    600 cannot be read. That is why the password does NOT travel as a file.
    """
    from secrets import token_hex

    ruta = f"/tmp/spanel-{etiqueta}-{token_hex(6)}"
    r = run(
        build_docker_argv(f"umask 077 && cat > {ruta} && chmod 600 {ruta}", env=env),
        env=env,
        input=contenido,
    )
    if r.returncode != 0:
        raise Fallo(
            f"no se pudo escribir {etiqueta} en el host remoto: {(r.stderr or '').strip()[:200]}"
        )
    return ruta


def borrar_archivo_remoto(env: dict[str, str], *rutas: str) -> None:
    for ruta in rutas:
        run(build_docker_argv(f"rm -f {ruta}", env=env), env=env)
