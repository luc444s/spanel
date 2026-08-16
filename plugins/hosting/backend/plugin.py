import importlib.util
import json
import re
import secrets
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


PROTECTED_CONTAINERS = {"orquestador_ardi_postgres"}


class AdoptRequest(BaseModel):
    container_name: str
    name: str | None = None


class ProvisionWordpressRequest(BaseModel):
    name: str
    admin_email: str
    admin_user: str | None = None


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


def _get_own_site(db: Session, site_id: str, user: User):
    row = db.execute(
        text(
            "SELECT * FROM hosting_site WHERE id = :id AND tenant_id = :tenant"
        ),
        {"id": site_id, "tenant": user.tenant_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="site no encontrado")
    return row


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


@router.post("/sites/provision/wordpress", status_code=201)
def provision_wordpress(
    payload: ProvisionWordpressRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    name = payload.name.strip().lower()
    if not re.fullmatch(r"[a-z0-9-]{3,32}", name):
        raise HTTPException(
            status_code=422,
            detail="name invalido: 3-32 chars, solo a-z 0-9 guiones",
        )
    if "@" not in payload.admin_email:
        raise HTTPException(status_code=422, detail="admin_email invalido")

    wp_container = f"spanel-{name}-wp"
    db_container = f"spanel-{name}-db"
    network = f"spanel-{name}"
    db_volume = f"spanel-{name}-db"
    wp_volume = f"spanel-{name}-wp"

    existing = db.execute(
        text("SELECT 1 FROM hosting_site WHERE container_name = :name"),
        {"name": wp_container},
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="site con ese nombre ya existe")

    docker = _docker_infra()
    db_name = "wordpress"
    db_user = "wp"
    db_password = secrets.token_urlsafe(18)
    admin_user = payload.admin_user or "admin"
    admin_password = secrets.token_urlsafe(18)

    created: list[tuple[str, str]] = []
    try:
        docker.network_create(network)
        created.append(("network", network))
        docker.volume_create(db_volume)
        created.append(("volume", db_volume))
        docker.volume_create(wp_volume)
        created.append(("volume", wp_volume))
        docker.run_container(
            db_container,
            "mariadb:11",
            env={
                "MARIADB_DATABASE": db_name,
                "MARIADB_USER": db_user,
                "MARIADB_PASSWORD": db_password,
                "MARIADB_ROOT_PASSWORD": secrets.token_urlsafe(18),
            },
            volumes=[f"{db_volume}:/var/lib/mysql"],
            network=network,
        )
        created.append(("container", db_container))
        docker.run_container(
            wp_container,
            "wordpress:php8.3-apache",
            env={
                "WORDPRESS_DB_HOST": db_container,
                "WORDPRESS_DB_NAME": db_name,
                "WORDPRESS_DB_USER": db_user,
                "WORDPRESS_DB_PASSWORD": db_password,
            },
            volumes=[f"{wp_volume}:/var/www/html"],
            network=network,
        )
        created.append(("container", wp_container))
    except docker.DockerAdapterError as exc:
        _cleanup_provision(docker, created)
        raise HTTPException(
            status_code=502,
            detail=f"provision fallo, rollback ejecutado: {exc}",
        ) from exc

    site_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO hosting_site
                (id, tenant_id, branch_id, stack, name, container_name,
                 db_container_name, db_password, domains_json)
            VALUES
                (:id, :tenant, :branch, 'wordpress', :name, :container,
                 :db_container, :db_password, '[]')
            """
        ),
        {
            "id": site_id,
            "tenant": user.tenant_id,
            "branch": user.branch_id,
            "name": name,
            "container": wp_container,
            "db_container": db_container,
            "db_password": db_password,
        },
    )
    db.commit()

    return {
        "id": site_id,
        "name": name,
        "stack": "wordpress",
        "container_name": wp_container,
        "db_container_name": db_container,
        "network": network,
        "admin_email": payload.admin_email,
        "admin_user": admin_user,
        "admin_password": admin_password,
        "note": "credenciales admin entregadas una sola vez",
    }


def _cleanup_provision(docker, created: list[tuple[str, str]]) -> None:
    for kind, resource in reversed(created):
        try:
            if kind == "container":
                docker.rm_container(resource, force=True)
            elif kind == "network":
                docker.network_remove(resource)
            elif kind == "volume":
                docker.volume_remove(resource)
        except docker.DockerAdapterError:
            pass


@router.post("/sites/{site_id}/backups", status_code=201)
def create_backup(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="backups solo para wordpress")
    if not site.db_container_name or not site.db_password:
        raise HTTPException(
            status_code=409,
            detail="site adoptado sin credenciales de db: configurar antes",
        )

    docker = _docker_infra()
    docker.volume_ensure("spanel-backups")
    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d%H%M%S")
    tar_name = f"{site.container_name}-files-{timestamp}.tar.gz"
    dump_name = f"{site.container_name}-db-{timestamp}.sql"

    backup_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO hosting_backup (id, site_id, kind, path, status)
            VALUES (:id, :site, :kind, :path, 'running')
            """
        ),
        {"id": backup_id, "site": site.id, "kind": "full", "path": tar_name},
    )
    db.commit()

    try:
        docker.run_once_container(
            "alpine:3.20",
            f"tar czf /b/{tar_name} -C /wp wp-content",
            volumes=[
                "spanel-backups:/b",
                f"spanel-{site.name}-wp:/wp",
            ],
        )
    except docker.DockerAdapterError as exc:
        db.execute(
            text("UPDATE hosting_backup SET status = 'failed' WHERE id = :id"),
            {"id": backup_id},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=f"backup files fallo: {exc}") from exc

    try:
        docker.run_once_container(
            "mariadb:11",
            (
                f"mariadb-dump -h {site.db_container_name} -u wp "
                f"-p{site.db_password} wordpress > /b/{dump_name}"
            ),
            volumes=["spanel-backups:/b"],
            network=f"spanel-{site.name}",
        )
    except docker.DockerAdapterError as exc:
        db.execute(
            text("UPDATE hosting_backup SET status = 'failed' WHERE id = :id"),
            {"id": backup_id},
        )
        db.commit()
        raise HTTPException(status_code=502, detail=f"backup db fallo: {exc}") from exc

    db.execute(
        text(
            """
            UPDATE hosting_backup SET status = 'ok', path = :path WHERE id = :id
            """
        ),
        {"id": backup_id, "path": f"{tar_name},{dump_name}"},
    )
    db.commit()
    return {
        "id": backup_id,
        "site_id": site.id,
        "kind": "full",
        "files": tar_name,
        "db": dump_name,
        "status": "ok",
    }


@router.get("/sites/{site_id}/backups")
def list_backups(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    rows = db.execute(
        text(
            "SELECT * FROM hosting_backup WHERE site_id = :site "
            "ORDER BY created_at DESC"
        ),
        {"site": site.id},
    ).mappings()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "path": row.path,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(
        ["hosting.containers.read", "hosting.containers.manage"]
    )
    context.register_events(["hosting.site.adopted"])


def _guard_protected(site) -> None:
    if site.container_name in PROTECTED_CONTAINERS:
        raise HTTPException(
            status_code=403,
            detail=f"container protegido (operativo): {site.container_name}",
        )


def _lifecycle_action(action: str):
    def endpoint(
        site_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db_session),
    ):
        site = _get_own_site(db, site_id, user)
        _guard_protected(site)
        docker = _docker_infra()
        try:
            getattr(docker, f"{action}_container")(site.container_name)
        except docker.DockerAdapterError as exc:
            raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc
        return {"status": action, "container": site.container_name}

    return endpoint


for action in ("start", "stop", "restart"):
    router.add_api_route(
        f"/sites/{{site_id}}/{action}",
        _lifecycle_action(action),
        methods=["POST"],
    )


@router.get("/sites/{site_id}/logs")
def site_logs(
    site_id: str,
    tail: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    docker = _docker_infra()
    try:
        return {"lines": docker.logs_container(site.container_name, tail=tail)}
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


@router.get("/sites/{site_id}")
def site_detail(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    docker = _docker_infra()
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
        exposed = []
        for binding in ports.values():
            if binding and binding[0] and binding[0].get("HostPort"):
                exposed.append(binding[0]["HostPort"])
        origin["backend"] = (
            f"{site.container_name}:{private_ports[0]}"
            if private_ports
            else site.container_name
        )
    except docker.ContainerNotFoundError:
        pass
    except docker.DockerAdapterError:
        origin["container_status"] = "unreachable"

    return {**_site_row_to_dict(site), "origin": origin}
