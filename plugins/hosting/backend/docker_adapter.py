import os
import subprocess

SSH_USER = os.getenv("SPANEL_DOCKER_SSH_USER", "lucas")
SSH_HOST = os.getenv("SPANEL_DOCKER_SSH_HOST", "100.67.5.50")
SSH_PORT = os.getenv("SPANEL_DOCKER_SSH_PORT", "22")
SSH_PASSWORD = os.getenv("SPANEL_DOCKER_SSH_PASSWORD", "")


class DockerAdapterError(RuntimeError):
    pass


def _run_remote_docker(args: list[str], timeout: int = 20) -> str:
    cmd = [
        "sshpass",
        "-p",
        SSH_PASSWORD,
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-p",
        SSH_PORT,
        f"{SSH_USER}@{SSH_HOST}",
        "docker",
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise DockerAdapterError(proc.stderr.strip() or "docker remoto fallo")
    return proc.stdout


def list_containers() -> list[dict[str, str]]:
    """Lista containers en vivo del docker remoto (solo lectura)."""
    out = _run_remote_docker(["ps", "--format", "json"])
    containers: list[dict[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        import json

        raw = json.loads(line)
        containers.append(
            {
                "name": raw.get("Names", ""),
                "image": raw.get("Image", ""),
                "status": raw.get("Status", ""),
            }
        )
    return containers
