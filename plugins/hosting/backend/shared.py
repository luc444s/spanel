import importlib.util
import json
import sys
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User

PLUGINS_ROOT = Path(__file__).resolve().parents[2]
DOCKER_INFRA_MODULE = PLUGINS_ROOT / "docker_infra" / "backend" / "plugin.py"
DOCKER_INFRA_SYNTH = "_spanel_docker_infra_runtime"
PROTECTED_CONTAINERS = {"orquestador_ardi_postgres"}


class AdoptRequest(BaseModel):
    container_name: str
    name: str | None = None


class PatchSiteRequest(BaseModel):
    admin_email: str | None = None


class ProvisionWordpressRequest(BaseModel):
    name: str
    admin_email: str
    admin_user: str | None = None
    domain: str | None = None


def docker_infra():
    if DOCKER_INFRA_SYNTH in sys.modules:
        return sys.modules[DOCKER_INFRA_SYNTH]
    spec = importlib.util.spec_from_file_location(DOCKER_INFRA_SYNTH, DOCKER_INFRA_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("docker_infra plugin no encontrado")
    module = importlib.util.module_from_spec(spec)
    sys.modules[DOCKER_INFRA_SYNTH] = module
    spec.loader.exec_module(module)
    return module


def infer_stack(image: str) -> str:
    img = image.lower()
    if "wordpress" in img:
        return "wordpress"
    if "mariadb" in img or "mysql" in img or "postgres" in img:
        return "db"
    if "php" in img:
        return "php"
    return "static"


def site_row_to_dict(row, *, container_status: str | None = None) -> dict:
    payload = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "branch_id": row.branch_id,
        "stack": row.stack,
        "name": row.name,
        "container_name": row.container_name,
        "domains": json.loads(row.domains_json or "[]"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if container_status is not None:
        payload["container_status"] = container_status
    return payload


def site_status_map(docker, rows: list) -> dict[str, str]:
    try:
        containers = docker.list_containers(all_containers=True)
    except docker.DockerAdapterError:
        return {row.container_name: "unreachable" for row in rows}

    by_name = {
        container.get("name", ""): (
            container.get("state")
            or container.get("status", "").split(" ", 1)[0].lower()
            or "unknown"
        )
        for container in containers
    }
    return {
        row.container_name: by_name.get(row.container_name, "missing")
        for row in rows
    }


def get_own_site(db: Session, site_id: str, user: User):
    row = db.execute(
        text(
            "SELECT * FROM hosting_site WHERE id = :id AND tenant_id = :tenant"
        ),
        {"id": site_id, "tenant": user.tenant_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="site no encontrado")
    return row


def guard_protected(site) -> None:
    if site.container_name in PROTECTED_CONTAINERS:
        raise HTTPException(
            status_code=403,
            detail=f"container protegido (operativo): {site.container_name}",
        )
