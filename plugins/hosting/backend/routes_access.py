import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import get_current_user
from systutor.kernel.auth.models import User

from spanel_hosting_shared import PLUGINS_ROOT, docker_infra, get_own_site

router = APIRouter(tags=["hosting"])
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
    site = get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="sso solo para wordpress")
    if not site.admin_email:
        raise HTTPException(status_code=409, detail="site sin admin_email")
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominio (SP-0011)")
    if not SSO_SECRET:
        raise HTTPException(status_code=500, detail="SPANEL_SSO_SECRET sin configurar")

    docker = docker_infra()
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
    site = get_own_site(db, site_id, user)
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominios")
    fqdn = domains[0]
    docker = docker_infra()
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
    site = get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="filebrowser solo para wordpress")
    domains = json.loads(site.domains_json or "[]")
    if not domains:
        raise HTTPException(status_code=409, detail="site sin dominio (SP-0011)")

    slug = site.container_name.replace("spanel-", "", 1).rsplit("-wp", 1)[0]
    fb_container = f"spanel-{slug}-files"
    docker = docker_infra()
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
