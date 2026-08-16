import importlib.util
import json
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_user
from systutor.kernel.auth.models import User
from systutor.sdk import PluginContext

PLUGINS_ROOT = Path(__file__).resolve().parents[2]
DOCKER_INFRA_MODULE = PLUGINS_ROOT / "docker-infra" / "backend" / "plugin.py"
DOCKER_INFRA_SYNTH = "_spanel_docker_infra_runtime"


def _docker_infra():
    if DOCKER_INFRA_SYNTH in sys.modules:
        return sys.modules[DOCKER_INFRA_SYNTH]
    spec = importlib.util.spec_from_file_location(DOCKER_INFRA_SYNTH, DOCKER_INFRA_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("docker_infra plugin no encontrado")
    module = importlib.util.module_from_spec(spec)
    sys.modules[DOCKER_INFRA_SYNTH] = module
    spec.loader.exec_module(module)
    return module


def _infer_stack(image: str) -> str:
    img = image.lower()
    if "wordpress" in img:
        return "wordpress"
    if "mariadb" in img or "mysql" in img or "postgres" in img:
        return "db"
    if "php" in img:
        return "php"
    return "static"


class AdoptRequest(BaseModel):
    container_name: str
    name: str | None = None


router = APIRouter(tags=["hosting"])


def _site_row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "branch_id": row.branch_id,
        "stack": row.stack,
        "name": row.name,
        "container_name": row.container_name,
        "domains": json.loads(row.domains_json or "[]"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/sites")
def list_sites(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = db.execute(
        text(
            "SELECT * FROM hosting_site WHERE tenant_id = :tenant ORDER BY name"
        ),
        {"tenant": user.tenant_id},
    ).mappings()
    return [_site_row_to_dict(row) for row in rows]


@router.post("/sites/adopt", status_code=201)
def adopt_site(
    payload: AdoptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    docker = _docker_infra()
    try:
        info = docker.inspect_container(payload.container_name)
    except docker.ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc

    image = (info.get("Config") or {}).get("Image", "")
    stack = _infer_stack(image)
    name = payload.name or payload.container_name

    existing = db.execute(
        text("SELECT 1 FROM hosting_site WHERE container_name = :name"),
        {"name": payload.container_name},
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="container ya adoptado")

    site_id = str(uuid.uuid4())
    domains: list[str] = []
    labels = (info.get("Config") or {}).get("Labels") or {}
    for key, value in labels.items():
        if key.startswith("traefik.http.routers.") and key.endswith(".rule"):
            if "Host(`" in value:
                domains.append(value.split("Host(`")[1].split("`")[0])
        if key == "caddy" and value:
            domains.extend(part.strip() for part in value.split(",") if part.strip())

    db.execute(
        text(
            """
            INSERT INTO hosting_site
                (id, tenant_id, branch_id, stack, name, container_name, domains_json)
            VALUES
                (:id, :tenant, :branch, :stack, :name, :container, :domains)
            """
        ),
        {
            "id": site_id,
            "tenant": user.tenant_id,
            "branch": user.branch_id,
            "stack": stack,
            "name": name,
            "container": payload.container_name,
            "domains": json.dumps(domains),
        },
    )
    db.commit()
    return {
        "id": site_id,
        "tenant_id": user.tenant_id,
        "branch_id": user.branch_id,
        "stack": stack,
        "name": name,
        "container_name": payload.container_name,
        "domains": domains,
    }


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(
        ["hosting.containers.read", "hosting.containers.manage"]
    )
    context.register_events(["hosting.site.adopted"])
