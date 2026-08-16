from backend import docker_adapter
from fastapi import APIRouter, Depends, HTTPException

from systutor.kernel.auth.dependencies import get_current_user

router = APIRouter(tags=["hosting"])


@router.get("/containers")
def list_containers(_=Depends(get_current_user)):
    try:
        return docker_adapter.list_containers()
    except docker_adapter.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc
