import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import require_permission
from systutor.kernel.auth.models import User

from spanel_hosting_shared import (
    docker_infra,
    get_own_site,
    guard_protected,
    site_row_to_dict,
)

router = APIRouter(tags=["hosting"])
REQUIRE_SITES_READ = Depends(require_permission("hosting.sites.read"))
REQUIRE_RUNTIME_READ = Depends(require_permission("hosting.runtime.read"))
REQUIRE_RUNTIME_MANAGE = Depends(require_permission("hosting.runtime.manage"))


def lifecycle_action(action: str):
    def endpoint(
        site_id: str,
        user: User = REQUIRE_RUNTIME_MANAGE,
        db: Session = Depends(get_db_session),
    ):
        site = get_own_site(db, site_id, user)
        guard_protected(site)
        docker = docker_infra()
        try:
            getattr(docker, f"{action}_container")(site.container_name)
        except docker.DockerAdapterError as exc:
            raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc
        return {"status": action, "container": site.container_name}

    return endpoint


for action in ("start", "stop", "restart"):
    router.add_api_route(
        f"/sites/{{site_id}}/{action}",
        lifecycle_action(action),
        methods=["POST"],
    )


@router.get("/sites/{site_id}/logs")
def site_logs(
    site_id: str,
    tail: int = 100,
    user: User = REQUIRE_RUNTIME_READ,
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
    docker = docker_infra()
    try:
        return {"lines": docker.logs_container(site.container_name, tail=tail)}
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


@router.get("/sites/{site_id}")
def site_detail(
    site_id: str,
    user: User = REQUIRE_SITES_READ,
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
    docker = docker_infra()
    origin = {
        "public_urls": [f"https://{d}" for d in json.loads(site.domains_json or "[]")],
        "backend": None,
        "network": None,
        "host_ip": docker.SSH_HOST,
        "container_status": "missing",
    }
    try:
        info = docker.inspect_container(site.container_name)
        state = info.get("State") or {}
        network_settings = info.get("NetworkSettings") or {}
        networks = network_settings.get("Networks") or {}
        origin["container_status"] = state.get("Status", "unknown")
        origin["network"] = list(networks.keys())[0] if networks else None
        ports = network_settings.get("Ports") or {}
        private_ports = sorted(
            {
                str(binding[0]["HostPort"])
                for binding in ports.values()
                if binding and binding[0] and binding[0].get("HostPort")
            }
        ) if ports else []
        origin["backend"] = (
            f"{site.container_name}:{private_ports[0]}"
            if private_ports
            else site.container_name
        )
    except docker.ContainerNotFoundError:
        pass
    except docker.DockerAdapterError:
        origin["container_status"] = "unreachable"

    return {
        **site_row_to_dict(site, container_status=origin["container_status"]),
        "origin": origin,
    }
