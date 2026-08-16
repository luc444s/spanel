import importlib.util
import secrets
import string
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
DOCKER_INFRA_SYNTH = "_spanel_docker_infra_mail"


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


MAIL_CONTAINER = "spanel-mail"
MAIL_IMAGE = "ghcr.io/docker-mailserver/docker-mailserver:latest"

router = APIRouter(tags=["mail"])


class MailboxRequest(BaseModel):
    domain: str
    user: str
    password: str | None = None


def _ensure_mailserver(docker) -> None:
    docker.volume_ensure("spanel-mail-data")
    docker.volume_ensure("spanel-mail-state")
    docker.volume_ensure("spanel-mail-config")
    try:
        docker.inspect_container(MAIL_CONTAINER)
        return
    except docker.ContainerNotFoundError:
        pass
    docker.run_container(
        MAIL_CONTAINER,
        MAIL_IMAGE,
        hostname="mail.spanel.local",
        env={
            "ENABLE_FAIL2BAN": "0",
            "ENABLE_POP3": "1",
            "ONE_DIR": "1",
            "POSTMASTER_ADDRESS": "postmaster@spanel.local",
        },
        volumes=[
            "spanel-mail-data:/var/mail",
            "spanel-mail-state:/var/mail-state",
            "spanel-mail-config:/tmp/docker-mailserver",
        ],
        ports=["25:25", "465:465", "587:587", "143:143", "993:993"],
    )


def _gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(16))


@router.get("/server/status")
def server_status(_=Depends(get_current_user)):
    docker = _docker()
    try:
        info = docker.inspect_container(MAIL_CONTAINER)
        return {
            "provisioned": True,
            "status": (info.get("State") or {}).get("Status", "unknown"),
        }
    except docker.ContainerNotFoundError:
        return {"provisioned": False, "status": "missing"}
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"docker remoto: {exc}") from exc


@router.post("/server/ensure", status_code=201)
def ensure_mailserver(_=Depends(get_current_user)):
    docker = _docker()
    try:
        _ensure_mailserver(docker)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"mail server: {exc}") from exc
    return {"provisioned": True, "container": MAIL_CONTAINER}


@router.post("/domains", status_code=201)
def create_domain(
    payload: MailboxRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    domain = payload.domain.strip().lower()
    if "." not in domain or "/" in domain:
        raise HTTPException(status_code=422, detail="dominio invalido")
    docker = _docker()
    try:
        _ensure_mailserver(docker)
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"mail: {exc}") from exc
    row = db.execute(
        text("SELECT id FROM mail_domain WHERE domain = :d AND tenant_id = :t"),
        {"d": domain, "t": user.tenant_id},
    ).first()
    if row is None:
        db.execute(
            text(
                "INSERT INTO mail_domain (id, tenant_id, domain) "
                "VALUES (:id, :tenant, :domain)"
            ),
            {"id": str(uuid.uuid4()), "tenant": user.tenant_id, "domain": domain},
        )
        db.commit()
    return {"domain": domain, "status": "active"}


@router.post("/mailboxes", status_code=201)
def create_mailbox(
    payload: MailboxRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    domain = payload.domain.strip().lower()
    local = payload.user.strip().lower()
    if not local or "@" in local or "." not in domain:
        raise HTTPException(status_code=422, detail="user/dominio invalidos")
    email = f"{local}@{domain}"
    password = payload.password or _gen_password()

    docker = _docker()
    try:
        _ensure_mailserver(docker)
        last_error: Exception | None = None
        for _ in range(4):
            try:
                docker.exec_container(
                    MAIL_CONTAINER, ["setup", "email", "add", email, password]
                )
                last_error = None
                break
            except docker.DockerAdapterError as exc:
                last_error = exc
                if "already exists" in str(exc).lower():
                    last_error = None
                    break
                if "restarting" in str(exc).lower() or "starting" in str(exc).lower():
                    import time as _time

                    _time.sleep(6)
                    continue
                raise
        if last_error is not None:
            raise last_error
    except docker.DockerAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"mail: {exc}") from exc

    domain_row = db.execute(
        text("SELECT id FROM mail_domain WHERE domain = :d AND tenant_id = :t"),
        {"d": domain, "t": user.tenant_id},
    ).first()
    if domain_row is None:
        raise HTTPException(status_code=409, detail="dominio no registrado en Spanel")

    db.execute(
        text(
            "INSERT INTO mailbox (id, tenant_id, domain_id, email) "
            "VALUES (:id, :tenant, :domain, :email)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tenant": user.tenant_id,
            "domain": domain_row.id,
            "email": email,
        },
    )
    db.commit()
    return {
        "email": email,
        "password": password,
        "note": "credencial entregada una sola vez; SMTP host = docker remoto (100.67.5.50)",
    }


@router.get("/mailboxes")
def list_mailboxes(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    rows = db.execute(
        text("SELECT * FROM mailbox WHERE tenant_id = :t ORDER BY email"),
        {"t": user.tenant_id},
    ).mappings()
    return [{"email": row.email} for row in rows]


def register(context: PluginContext) -> None:
    context.register_router(router)
    context.register_permissions(["mail.domains.read", "mail.domains.manage"])
    context.register_events(["mail.smtp.provisioned"])
