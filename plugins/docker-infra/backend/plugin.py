import json
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Query

from systutor.kernel.auth.dependencies import get_current_user
from systutor.sdk import PluginContext

SSH_USER = os.getenv("SPANEL_DOCKER_SSH_USER", "")
SSH_HOST = os.getenv("SPANEL_DOCKER_SSH_HOST", "")
SSH_PORT = os.getenv("SPANEL_DOCKER_SSH_PORT", "22")
SSH_PASSWORD = os.getenv("SPANEL_DOCKER_SSH_PASSWORD", "")


class DockerAdapterError(RuntimeError):
    pass


class ContainerNotFoundError(RuntimeError):
    pass


def _run_remote_docker(args: list[str], timeout: int = 30) -> str:
    missing = [
        var
        for var in (
            "SPANEL_DOCKER_SSH_USER",
            "SPANEL_DOCKER_SSH_HOST",
            "SPANEL_DOCKER_SSH_PASSWORD",
        )
        if not os.getenv(var)
    ]
    if missing:
        raise DockerAdapterError(
            f"configuracion docker remota incompleta en .env: {', '.join(missing)}"
        )
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
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise DockerAdapterError("timeout en docker remoto") from exc
    if proc.returncode != 0:
        raise DockerAdapterError(proc.stderr.strip() or "docker remoto fallo")
    return proc.stdout


def _parse_json_lines(out: str) -> list[dict]:
    rows: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def list_containers(all_containers: bool = False) -> list[dict[str, str]]:
    args = ["ps", "--format", "json"]
    if all_containers:
        args.append("--all")
    return [
        {
            "name": raw.get("Names", ""),
            "image": raw.get("Image", ""),
            "status": raw.get("Status", ""),
        }
        for raw in _parse_json_lines(_run_remote_docker(args))
    ]


def inspect_container(name: str) -> dict:
    try:
        out = _run_remote_docker(["inspect", name])
    except DockerAdapterError as exc:
        if "No such" in str(exc):
            raise ContainerNotFoundError(name) from exc
        raise
    rows = json.loads(out)
    if not rows:
        raise ContainerNotFoundError(name)
    return rows[0]


def start_container(name: str) -> None:
    _run_remote_docker(["start", name])


def stop_container(name: str) -> None:
    _run_remote_docker(["stop", name])


def restart_container(name: str) -> None:
    _run_remote_docker(["restart", name])


def logs_container(name: str, tail: int = 100) -> str:
    return _run_remote_docker(["logs", "--tail", str(tail), name])


router = APIRouter(tags=["docker-infra"])


@router.get("/containers")
def containers_list(
    all_containers: bool = Query(default=False),
    _=Depends(get_current_user),
):
    try:
        return list_containers(all_containers=all_containers)
    except DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


@router.get("/containers/{name}/inspect")
def containers_inspect(name: str, _=Depends(get_current_user)):
    try:
        return inspect_container(name)
    except ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(
        ["docker_infra.containers.read", "docker_infra.containers.manage"]
    )
    context.register_events(
        ["docker_infra.container.discovered", "docker_infra.container.state_changed"]
    )
