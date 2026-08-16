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
DOCKER_INFRA_MODULE = PLUGINS_ROOT / "docker_infra" / "backend" / "plugin.py"
DOCKER_INFRA_SYNTH = "_spanel_docker_infra_proxy"


def _docker():
    if DOCKER_INFRA_SYNTH in sys.modules:
        return sys.modules[DOCKER_INFRA_SYNTH]
    spec = importlib.util.spec_from_file_location(DOCKER_INFRA_SYNTH, DOCKER_INFRA_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("docker_infra plugin no encontrado")
    module = importlib.util.module_from_spec(spec)
    sys.modules[DOCKER_INFRA_SYNTH] = module
    spec.loader.exec_module(module)
    return module


TRAEFIK_CONTAINER = "spanel-traefik"
TRAEFIK_CONF_VOLUME = "spanel-traefik-conf"
TRAEFIK_ACME_VOLUME = "spanel-traefik-acme"
TRAEFIK_LOGS_VOLUME = "spanel-traefik-logs"

router = APIRouter(tags=["proxy"])


class DomainCreateRequest(BaseModel):
    fqdn: str
    site_id: str | None = None


class DomainUpdateRequest(BaseModel):
    fqdn: str


def _traefik_flags() -> list[str]:
    return [
        "--entrypoints.web.address=:80",
        "--entrypoints.websecure.address=:443",
        "--providers.file.directory=/etc/traefik/dynamic",
        "--providers.file.watch=true",
        "--accesslog.filepath=/var/log/traefik/access.log",
        "--accesslog.format=json",
        "--certificatesresolvers.letsencrypt.acme.email=admin@example.com",
        "--certificatesresolvers.letsencrypt.acme.storage=/acme.json",
        "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web",
    ]


def _ensure_traefik(docker) -> None:
    docker.volume_ensure(TRAEFIK_CONF_VOLUME)
    docker.volume_ensure(TRAEFIK_ACME_VOLUME)
    docker.volume_ensure(TRAEFIK_LOGS_VOLUME)
    try:
        info = docker.inspect_container(TRAEFIK_CONTAINER)
        args = (info.get("Args") or [])
        if "--accesslog.filepath=/var/log/traefik/access.log" in args:
            return
        docker.rm_container(TRAEFIK_CONTAINER, force=True)
    except docker.ContainerNotFoundError:
        pass
    docker.run_container(
        TRAEFIK_CONTAINER,
        "traefik:v3",
        command=_traefik_flags(),
        volumes=[
            f"{TRAEFIK_CONF_VOLUME}:/etc/traefik/dynamic",
            f"{TRAEFIK_ACME_VOLUME}:/acme.json",
            f"{TRAEFIK_LOGS_VOLUME}:/var/log/traefik",
        ],
        ports=["80:80", "443:443"],
    )


def _write_route(docker, site_name: str, fqdns: list[str], backend: str, net_name: str | None = None) -> None:
    host_rules = " || ".join(f'Host("{fqdn}")' for fqdn in fqdns)
    yaml = (
        "http:\n"
        "  routers:\n"
        f"    {site_name}:\n"
        f"      rule: {host_rules}\n"
        f"      service: {site_name}\n"
        "      entryPoints: [web]\n"
        "  services:\n"
        f"    {site_name}:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        f"          - url: http://{backend}:80\n"
    )
    heredoc = (
        f"cat > /c/{site_name}.yml <<YAMLEOF\n{yaml}YAMLEOF\n"
        "ls /c/"
    )
    docker.run_once_container(
        "alpine:3.20",
        heredoc,
        volumes=[f"{TRAEFIK_CONF_VOLUME}:/c"],
    )


def _remove_route(docker, site_name: str) -> None:
    docker.run_once_container(
        "alpine:3.20",
        f"rm -f /c/{site_name}.yml",
        volumes=[f"{TRAEFIK_CONF_VOLUME}:/c"],
    )


def _sync_site_route(docker, site) -> None:
    all_domains = json.loads(site.domains_json or "[]")
    if all_domains:
        _write_route(docker, site.name, all_domains, site.container_name)
    else:
        _remove_route(docker, site.name)


@router.get("/traefik/status")
def traefik_status(_=Depends(get_current_user)):
    docker = _docker()
    try:
        info = docker.inspect_container(TRAEFIK_CONTAINER)
        return {
            "provisioned": True,
            "status": (info.get("State") or {}).get("Status", "unknown"),
        }
    except docker.ContainerNotFoundError:
        return {"provisioned": False, "status": "missing"}
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


@router.post("/domains", status_code=201)
def create_domain(
    payload: DomainCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    fqdn = payload.fqdn.strip().lower()
    if "." not in fqdn or "/" in fqdn:
        raise HTTPException(status_code=422, detail="fqdn invalido")

    if payload.site_id:
        site = db.execute(
            text(
                "SELECT * FROM hosting_site WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": payload.site_id, "tenant": user.tenant_id},
        ).mappings().first()
    else:
        site = db.execute(
            text(
                "SELECT * FROM hosting_site WHERE tenant_id = :tenant AND container_name = :container"
            ),
            {"tenant": user.tenant_id, "container": f"spanel-{fqdn.split('.')[0]}-wp"},
        ).mappings().first()
    if site is None:
        raise HTTPException(
            status_code=404,
            detail="no hay site wordpress con ese nombre (spanel-<nombre>-wp) — usa site_id",
        )

    existing = db.execute(
        text("SELECT 1 FROM hosting_domain WHERE fqdn = :fqdn"),
        {"fqdn": fqdn},
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="dominio ya registrado")

    docker = _docker()
    try:
        _ensure_traefik(docker)
        net_name = f"spanel-{site.container_name.replace('spanel-', '', 1).rsplit('-wp', 1)[0]}"
        try:
            docker.network_connect(TRAEFIK_CONTAINER, net_name)
        except docker.DockerAdapterError as exc:
            if "already exists" not in str(exc):
                raise
        all_domains = json.loads(site.domains_json or "[]")
        if fqdn not in all_domains:
            all_domains.append(fqdn)
        site.domains_json = json.dumps(all_domains)
        _sync_site_route(docker, site)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"traefik: {exc}") from exc

    domain_id = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO hosting_domain (id, site_id, fqdn, ssl_status)
            VALUES (:id, :site, :fqdn, 'pending-letsencrypt')
            """
        ),
        {"id": domain_id, "site": site.id, "fqdn": fqdn},
    )
    current_domains = json.loads(site.domains_json or "[]")
    if fqdn not in current_domains:
        current_domains.insert(0, fqdn)
    db.execute(
        text(
            """
            UPDATE hosting_site
            SET domains_json = :domains
            WHERE id = :site
            """
        ),
        {"site": site.id, "domains": json.dumps(current_domains)},
    )
    db.commit()

    if site.stack == "wordpress" and site.db_container_name and site.db_password:
        try:
            docker.exec_container(
                site.db_container_name,
                ["sh", "-c",
                 f"mariadb -u wp -p{site.db_password} wordpress "
                 f"-e \"UPDATE wp_options SET option_value='http://{fqdn}' "
                 f"WHERE option_name IN ('siteurl','home')\""],
            )
        except docker.DockerAdapterError:
            pass

    return {
        "id": domain_id,
        "site_id": site.id,
        "fqdn": fqdn,
        "ssl_status": "pending-letsencrypt",
    }


@router.get("/domains")
def list_domains(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = db.execute(
        text(
            """
            SELECT d.* FROM hosting_domain d
            JOIN hosting_site s ON s.id = d.site_id
            WHERE s.tenant_id = :tenant
            ORDER BY d.created_at DESC
            """
        ),
        {"tenant": user.tenant_id},
    ).mappings()
    return [
        {
            "id": row.id,
            "site_id": row.site_id,
            "fqdn": row.fqdn,
            "ssl_status": row.ssl_status,
        }
        for row in rows
    ]


@router.patch("/domains/{domain_id}")
def update_domain(
    domain_id: str,
    payload: DomainUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    row = db.execute(
        text(
            """
            SELECT d.*, s.name AS site_name, s.container_name, s.domains_json
            FROM hosting_domain d
            JOIN hosting_site s ON s.id = d.site_id
            WHERE d.id = :id AND s.tenant_id = :tenant
            """
        ),
        {"id": domain_id, "tenant": user.tenant_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="dominio no encontrado")

    new_fqdn = payload.fqdn.strip().lower()
    if "." not in new_fqdn or "/" in new_fqdn:
        raise HTTPException(status_code=422, detail="fqdn invalido")

    if new_fqdn != row.fqdn:
        dup = db.execute(
            text("SELECT 1 FROM hosting_domain WHERE fqdn = :fqdn AND id != :id"),
            {"fqdn": new_fqdn, "id": domain_id},
        ).first()
        if dup is not None:
            raise HTTPException(status_code=409, detail="dominio ya registrado")

    db.execute(
        text("UPDATE hosting_domain SET fqdn = :fqdn WHERE id = :id"),
        {"fqdn": new_fqdn, "id": domain_id},
    )

    all_domains = json.loads(row.domains_json or "[]")
    if row.fqdn in all_domains:
        all_domains[all_domains.index(row.fqdn)] = new_fqdn
    db.execute(
        text("UPDATE hosting_site SET domains_json = :domains WHERE id = :site"),
        {"site": row.site_id, "domains": json.dumps(all_domains)},
    )
    db.commit()

    docker = _docker()
    try:
        class _Site:
            pass
        site_proxy = _Site()
        site_proxy.name = row.site_name
        site_proxy.container_name = row.container_name
        site_proxy.domains_json = json.dumps(all_domains)
        _sync_site_route(docker, site_proxy)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"traefik: {exc}") from exc

    return {"id": domain_id, "site_id": row.site_id, "fqdn": new_fqdn, "ssl_status": row.ssl_status}


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(
    domain_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    row = db.execute(
        text(
            """
            SELECT d.*, s.name AS site_name, s.container_name, s.domains_json
            FROM hosting_domain d
            JOIN hosting_site s ON s.id = d.site_id
            WHERE d.id = :id AND s.tenant_id = :tenant
            """
        ),
        {"id": domain_id, "tenant": user.tenant_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="dominio no encontrado")

    db.execute(text("DELETE FROM hosting_domain WHERE id = :id"), {"id": domain_id})

    all_domains = json.loads(row.domains_json or "[]")
    if row.fqdn in all_domains:
        all_domains.remove(row.fqdn)
    db.execute(
        text("UPDATE hosting_site SET domains_json = :domains WHERE id = :site"),
        {"site": row.site_id, "domains": json.dumps(all_domains)},
    )
    db.commit()

    docker = _docker()
    try:
        class _Site:
            pass
        site_proxy = _Site()
        site_proxy.name = row.site_name
        site_proxy.container_name = row.container_name
        site_proxy.domains_json = json.dumps(all_domains)
        _sync_site_route(docker, site_proxy)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"traefik: {exc}") from exc


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(["proxy.routes.read", "proxy.routes.manage"])
    context.register_events(["proxy.route.created", "proxy.route.removed"])
