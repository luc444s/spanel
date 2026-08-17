import json
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_user
from systutor.kernel.auth.models import User

from spanel_hosting_shared import (
    PROTECTED_CONTAINERS,
    AdoptRequest,
    PatchSiteRequest,
    ProvisionWordpressRequest,
    docker_infra,
    get_own_site,
    infer_stack,
    site_status_map,
)

router = APIRouter(tags=["hosting"])


@router.get("/sites")
def list_sites(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = list(
        db.execute(
            text(
                "SELECT * FROM hosting_site WHERE tenant_id = :tenant ORDER BY name"
            ),
            {"tenant": user.tenant_id},
        ).mappings()
    )
    docker = docker_infra()
    status_map = site_status_map(docker, rows)
    return [
        site_row_to_dict(row, container_status=status_map.get(row.container_name, "missing"))
        for row in rows
    ]


@router.post("/sites/adopt", status_code=201)
def adopt_site(
    payload: AdoptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    if payload.container_name in PROTECTED_CONTAINERS:
        raise HTTPException(
            status_code=403,
            detail=f"container protegido (operativo): {payload.container_name}",
        )
    docker = docker_infra()
    try:
        info = docker.inspect_container(payload.container_name)
    except docker.ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc

    image = (info.get("Config") or {}).get("Image", "")
    stack = infer_stack(image)
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


def cleanup_provision(docker, created: list[tuple[str, str]]) -> None:
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

    docker = docker_infra()
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
        cleanup_provision(docker, created)
        raise HTTPException(
            status_code=502,
            detail=f"provision fallo, rollback ejecutado: {exc}",
        ) from exc

    site_id = str(uuid.uuid4())
    domain_list: list[str] = []
    if payload.domain:
        domain_list.append(payload.domain.strip().lower())
    db.execute(
        text(
            """
            INSERT INTO hosting_site
                (id, tenant_id, branch_id, stack, name, container_name,
                 db_container_name, db_password, admin_email, domains_json)
            VALUES
                (:id, :tenant, :branch, 'wordpress', :name, :container,
                 :db_container, :db_password, :admin_email, :domains)
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
            "admin_email": payload.admin_email,
            "domains": json.dumps(domain_list),
        },
    )
    db.commit()

    if payload.domain:
        import time as _time

        _time.sleep(5)
        fqdn = payload.domain.strip().lower()
        try:
            docker.exec_container(
                db_container,
                [
                    "sh",
                    "-c",
                    f"mariadb -u {db_user} -p{db_password} {db_name} "
                    f"-e \"UPDATE wp_options SET option_value='http://{fqdn}' "
                    f"WHERE option_name IN ('siteurl','home')\"",
                ],
            )
        except docker.DockerAdapterError:
            pass
        db.execute(
            text(
                """
                INSERT INTO hosting_domain (id, site_id, fqdn, ssl_status)
                VALUES (:id, :site, :fqdn, 'pending')
                """
            ),
            {"id": str(uuid.uuid4()), "site": site_id, "fqdn": fqdn},
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
        "domains": domain_list,
        "note": "credenciales admin entregadas una sola vez",
    }


@router.patch("/sites/{site_id}")
def patch_site(
    site_id: str,
    payload: PatchSiteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
    if payload.admin_email is not None:
        db.execute(
            text("UPDATE hosting_site SET admin_email = :email WHERE id = :id"),
            {"email": payload.admin_email, "id": site.id},
        )
        db.commit()
    return {"updated": True}


@router.delete("/sites/{site_id}")
def delete_site(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
    if json.loads(site.domains_json or "[]"):
        raise HTTPException(
            status_code=409,
            detail="site con dominios: primero eliminarlos en proxy",
        )

    linked_domains = db.execute(
        text("SELECT 1 FROM hosting_domain WHERE site_id = :site LIMIT 1"),
        {"site": site.id},
    ).first()
    if linked_domains is not None:
        raise HTTPException(
            status_code=409,
            detail="site con dominios: primero eliminarlos en proxy",
        )

    db.execute(text("DELETE FROM hosting_backup WHERE site_id = :site"), {"site": site.id})
    db.execute(
        text("DELETE FROM hosting_site WHERE id = :id AND tenant_id = :tenant"),
        {"id": site.id, "tenant": user.tenant_id},
    )
    db.commit()
    return {"deleted": True, "id": site.id}
