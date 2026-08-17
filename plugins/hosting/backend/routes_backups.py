import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from systutor.api.deps import get_db_session
from systutor.kernel.auth.dependencies import require_permission
from systutor.kernel.auth.models import User

from spanel_hosting_shared import docker_infra, get_own_site

router = APIRouter(tags=["hosting"])
REQUIRE_BACKUPS_CREATE = Depends(require_permission("hosting.backups.create"))
REQUIRE_BACKUPS_READ = Depends(require_permission("hosting.backups.read"))


@router.post("/sites/{site_id}/backups", status_code=201)
def create_backup(
    site_id: str,
    user: User = REQUIRE_BACKUPS_CREATE,
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
    if site.stack != "wordpress":
        raise HTTPException(status_code=422, detail="backups solo para wordpress")
    if not site.db_container_name or not site.db_password:
        raise HTTPException(
            status_code=409,
            detail="site adoptado sin credenciales de db: configurar antes",
        )

    docker = docker_infra()
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
    user: User = REQUIRE_BACKUPS_READ,
    db: Session = Depends(get_db_session),
):
    site = get_own_site(db, site_id, user)
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
