import base64
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import sys
import time
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


class PatchSiteRequest(BaseModel):
    admin_email: str | None = None


class ProvisionWordpressRequest(BaseModel):
    name: str
    admin_email: str
    admin_user: str | None = None
    domain: str | None = None


router = APIRouter(tags=["hosting"])


def _site_row_to_dict(row, *, container_status: str | None = None) -> dict:
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


def _site_status_map(docker, rows: list) -> dict[str, str]:
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
    rows = list(
        db.execute(
        text(
            "SELECT * FROM hosting_site WHERE tenant_id = :tenant ORDER BY name"
        ),
        {"tenant": user.tenant_id},
        ).mappings()
    )
    docker = _docker_infra()
    status_map = _site_status_map(docker, rows)
    return [
        _site_row_to_dict(row, container_status=status_map.get(row.container_name, "missing"))
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
                ["sh", "-c",
                 f"mariadb -u {db_user} -p{db_password} {db_name} "
                 f"-e \"UPDATE wp_options SET option_value='http://{fqdn}' "
                 f"WHERE option_name IN ('siteurl','home')\""],
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


SSO_SECRET = os.getenv("SPANEL_SSO_SECRET", "")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_sso_token(email: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps({"sub": email, "iat": now, "exp": now + 60}, separators=(",", ":")).encode()
    )
    signature = hmac.new(
        SSO_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


def _install_sso_plugin(docker, site) -> None:
    plugin_source = PLUGINS_ROOT / "spanel-sso-wp" / "spanel-sso.php"
    plugin_php = plugin_source.read_text(encoding="utf-8")
    plugin_b64 = base64.b64encode(plugin_php.encode()).decode()
    secret_b64 = base64.b64encode(SSO_SECRET.encode()).decode()
    wp_vol = f"{site.container_name.replace('-wp', '')}-wp"
    net = f"{site.container_name.replace('-wp', '').replace('spanel-', 'spanel-', 1)}"
    script = (
        "mkdir -p /wp/wp-content/plugins/spanel-sso\n"
        f"printf '%s' '{plugin_b64}' | base64 -d > "
        "/wp/wp-content/plugins/spanel-sso/spanel-sso.php\n"
        f"printf '%s' '{secret_b64}' | base64 -d > /wp/spanel-sso-secret.txt\n"
        "ls /wp/wp-content/plugins/spanel-sso/"
    )
    docker.run_once_container(
        "alpine:3.20",
        script,
        volumes=[f"{wp_vol}:/wp"],
    )
    db_container = site.db_container_name
    db_password = site.db_password
    if not db_container or not db_password:
        try:
            wp_info = docker.inspect_container(site.container_name)
            env_vars = (wp_info.get("Config") or {}).get("Env") or []
            env_map = dict(e.split("=", 1) for e in env_vars if "=" in e)
            db_container = db_container or env_map.get("WORDPRESS_DB_HOST", "")
            db_password = db_password or env_map.get("WORDPRESS_DB_PASSWORD", "")
        except docker.DockerAdapterError:
            pass
    docker.run_once_container(
        "wordpress:cli",
        (
            "wp plugin activate spanel-sso --allow-root && "
            "wp rewrite structure '/%postname%/' --allow-root"
        ),
        env={
            "WORDPRESS_DB_HOST": db_container or "localhost",
            "WORDPRESS_DB_USER": "wp",
            "WORDPRESS_DB_PASSWORD": db_password or "",
            "WORDPRESS_DB_NAME": "wordpress",
        },
        volumes=[f"{wp_vol}:/var/www/html"],
        network=net,
    )


@router.post("/sites/{site_id}/sso")
def site_sso(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="sso solo para wordpress")
    if not site.admin_email:
        raise HTTPException(status_code=409, detail="site sin admin_email")
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominio (SP-0011)")
    if not SSO_SECRET:
        raise HTTPException(status_code=500, detail="SPANEL_SSO_SECRET sin configurar")

    docker = _docker_infra()
    try:
        _install_sso_plugin(docker, site)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"instalacion sso fallo: {exc}") from exc

    token = _sign_sso_token(site.admin_email)
    fqdn = domains[0]
    return {
        "url": f"http://{fqdn}/wp-json/spanel/v1/sso?token={token}",
        "expires_in": 60,
    }


@router.get("/sites/{site_id}/access-logs")
def site_access_logs(
    site_id: str,
    since: str | None = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominios")
    fqdn = domains[0]
    docker = _docker_infra()
    try:
        out = docker.run_once_container(
            "alpine:3.20",
            f"grep -F '{fqdn}' /l/access.log | tail -n {min(limit, 500)}",
            volumes=["spanel-traefik-logs:/l"],
        )
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"traefik logs: {exc}") from exc
    lines = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        lines.append(
            {
                "ip": row.get("ClientHost", ""),
                "method": row.get("RequestMethod", ""),
                "path": row.get("RequestPath", ""),
                "status": row.get("DownstreamStatus", ""),
                "ts": row.get("StartLocal", ""),
            }
        )
    return lines


@router.post("/sites/{site_id}/files/ensure", status_code=201)
def ensure_filebrowser(
    site_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="filebrowser solo para wordpress")
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominio (SP-0011)")

    slug = site.container_name.replace("spanel-", "", 1).rsplit("-wp", 1)[0]
    fb_container = f"spanel-{slug}-files"
    docker = _docker_infra()
    try:
        try:
            docker.inspect_container(fb_container)
        except docker.ContainerNotFoundError:
            docker.run_container(
                fb_container,
                "filebrowser/filebrowser:v2",
                env={"FB_NOAUTH": "1"},
                volumes=[f"spanel-{slug}-wp:/srv"],
                network=f"spanel-{slug}",
            )
        try:
            docker.network_connect("spanel-traefik", f"spanel-{slug}")
        except docker.DockerAdapterError as exc:
            if "already exists" not in str(exc):
                raise

        fqdn = domains[0]
        spanel_api_url = os.getenv("SPANEL_API_URL", "http://host.docker.internal:8001")
        yaml = (
            "http:\n"
            "  middlewares:\n"
            f"    {slug}-files-auth:\n"
            "      forwardAuth:\n"
            f"        address: {spanel_api_url}/api/v1/auth/me\n"
            "        trustForwardHeader: true\n"
            "  routers:\n"
            f"    {slug}-files:\n"
            f'      rule: Host("files.{fqdn}")\n'
            f"      service: {slug}-files\n"
            f"      middlewares: [{slug}-files-auth]\n"
            "      entryPoints: [web]\n"
            "  services:\n"
            f"    {slug}-files:\n"
            "      loadBalancer:\n"
            "        servers:\n"
            f"          - url: http://{fb_container}:80\n"
        )
        yaml_b64 = base64.b64encode(yaml.encode()).decode()
        docker.run_once_container(
            "alpine:3.20",
            (
                f"printf '%s' '{yaml_b64}' | base64 -d > "
                f"/c/{slug}-files.yml && ls /c/"
            ),
            volumes=["spanel-traefik-conf:/c"],
        )
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"filebrowser: {exc}") from exc

    return {
        "container": fb_container,
        "url": f"http://files.{fqdn}/",
        "auth": "forwardAuth: JWT de Spanel (header Authorization)",
    }


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(
        ["hosting.containers.read", "hosting.containers.manage"]
    )
    context.register_events(["hosting.site.adopted"])


@router.patch("/sites/{site_id}")
def patch_site(
    site_id: str,
    payload: PatchSiteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    site = _get_own_site(db, site_id, user)
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
    site = _get_own_site(db, site_id, user)
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

    return {
        **_site_row_to_dict(site, container_status=origin["container_status"]),
        "origin": origin,
    }
