"""Tenant-scoped file operations; DB sessions end before container I/O."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from systutor.contracts.file_targets import FileTarget, resolve_file_target
from systutor.core.lifecycle import ensure_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.dependencies import bearer_scheme, get_current_user
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import build_tenant_context
from systutor.kernel.tenants.service import validate_user_branch_scope

from .driver import EDIT_LIMIT, TRANSFER_LIMIT, close_process, download, operation, upload
from .tickets import consume, issue

router = APIRouter(prefix="/targets/{provider}/{resource_id}", tags=["filemanager"])


class Rename(BaseModel):
    path: str
    name: str


class NewDirectory(BaseModel):
    path: str


class TextUpdate(BaseModel):
    content: str
    version: str


class Archive(BaseModel):
    path: str
    name: str
    entries: list[str]


class Extract(BaseModel):
    path: str
    destination: str


@dataclass(frozen=True)
class Access:
    target: FileTarget
    tenant_id: str
    branch_id: str | None
    user_id: str


def authorize(
    request: Request,
    provider: str,
    resource_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Access:
    return _authorize_target(
        request,
        provider,
        resource_id,
        credentials,
        write=request.method in ("POST", "PUT", "PATCH", "DELETE"),
    )


def authorize_read(
    request: Request,
    provider: str,
    resource_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Access:
    return _authorize_target(request, provider, resource_id, credentials, write=False)


def _authorize_target(
    request: Request,
    provider: str,
    resource_id: str,
    credentials: HTTPAuthorizationCredentials | None,
    *,
    write: bool,
) -> Access:
    with ensure_session_factory(request.app)() as db:
        user = get_current_user(request, credentials, db, request.app.state.settings)
        context = build_tenant_context(db, user)
        permission = "filemanager.files.manage" if write else "filemanager.files.read"
        if not context.has_permission(permission):
            raise HTTPException(403, "Permission denied")
        target = resolve_file_target(
            request.app,
            provider,
            db,
            context.current_tenant_id,
            resource_id,
            write,
            context.current_permissions,
        )
        if target is None:
            raise HTTPException(404, "File target not found")
        return Access(target, context.current_tenant_id, context.current_branch_id, user.id)


def docker_environment() -> dict[str, str]:
    return {
        key: os.environ.get(key, "")
        for key in (
            "SPANEL_DOCKER_TRANSPORT",
            "SPANEL_DOCKER_SSH_USER",
            "SPANEL_DOCKER_SSH_HOST",
            "SPANEL_DOCKER_SSH_PORT",
            "SPANEL_DOCKER_SSH_STRICT_HOST_KEY",
            "SPANEL_DOCKER_SSH_PASSWORD",
            "MAIL_SERVER_PASSWORD",
        )
    }


def ticket_access(
    request: Request, provider: str, resource_id: str, ticket: str
) -> tuple[Access, str]:
    record = consume(ticket)
    if not record or record.get("provider") != provider or record.get("resource_id") != resource_id:
        raise HTTPException(401, "Descarga expirada")
    with ensure_session_factory(request.app)() as db:
        user = db.get(User, record.get("user_id"))
        if (
            user is None
            or not user.is_active
            or user.tenant_id != record.get("tenant_id")
            or not validate_user_branch_scope(db, user)
        ):
            raise HTTPException(401, "Descarga expirada")
        context = build_tenant_context(db, user)
        if not context.has_permission("filemanager.files.read"):
            raise HTTPException(403, "Permission denied")
        target = resolve_file_target(
            request.app,
            provider,
            db,
            context.current_tenant_id,
            resource_id,
            False,
            context.current_permissions,
        )
        if target is None:
            raise HTTPException(404, "File target not found")
        return Access(
            target, context.current_tenant_id, context.current_branch_id, user.id
        ), record["path"]


def audited(
    request: Request, access: Access, action: str, path: str, result: str = "success"
) -> None:
    with ensure_session_factory(request.app)() as db:
        record_audit(
            db,
            tenant_id=access.tenant_id,
            branch_id=access.branch_id,
            actor_user_id=access.user_id,
            actor_type="user",
            module="filemanager",
            action=action,
            entity_type="file_target",
            entity_id=access.target.resource_id,
            result=result,
            correlation_id=getattr(request.state, "correlation_id", None),
            request_id=getattr(request.state, "request_id", None),
            details={"path": path},
        )
        db.commit()


@router.get("/entries")
def entries(path: str = "", access: Access = Depends(authorize)) -> dict:
    return operation(access.target, "list", docker_environment(), path=path)


@router.get("/text")
def read_text(path: str, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(access.target, "read", docker_environment(), path=path, limit=EDIT_LIMIT)
    audited(request, access, "filemanager.text.read", path)
    return result


@router.put("/text")
def save_text(
    body: TextUpdate,
    request: Request,
    path: str,
    access: Access = Depends(authorize),
) -> dict:
    encoded = body.content.encode("utf-8")
    if len(encoded) > EDIT_LIMIT:
        raise HTTPException(413, "Archivo demasiado grande para editar")
    result = upload(
        access.target,
        "write",
        docker_environment(),
        io.BytesIO(encoded),
        EDIT_LIMIT,
        path=path,
        version=body.version,
    )
    audited(request, access, "filemanager.text.save", path)
    return result


@router.post("/uploads")
def add_file(
    request: Request,
    file: Annotated[UploadFile, File()],
    path: str = "",
    replace: bool = False,
    access: Access = Depends(authorize),
) -> dict:
    name = file.filename or ""
    if not name or "/" in name or "\\" in name or name in (".", "..") or path.startswith("/"):
        raise HTTPException(400, "Nombre de archivo inválido")
    relative = "/".join(part for part in (path.strip("/"), name) if part)
    result = upload(
        access.target,
        "upload",
        docker_environment(),
        file.file,
        TRANSFER_LIMIT,
        path=relative,
        replace=replace,
    )
    audited(request, access, "filemanager.upload", relative)
    return result


class ManagedDownload(StreamingResponse):
    def __init__(self, process, chunks, **kwargs):
        super().__init__(chunks, **kwargs)
        self.process = process

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close_process(self.process)


@router.get("/download")
def get_file(path: str, request: Request, access: Access = Depends(authorize)) -> Response:
    info = operation(access.target, "stat", docker_environment(), path=path)
    if info.get("directory"):
        raise HTTPException(400, "Seleccioná un archivo")
    audited(request, access, "filemanager.download", path)
    process, chunks = download(access.target, docker_environment(), path)
    name = quote(str(info["name"]), safe="")
    return ManagedDownload(
        process,
        chunks,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{name}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/download-tickets")
def create_download_ticket(
    path: str,
    request: Request,
    provider: str,
    resource_id: str,
    access: Access = Depends(authorize_read),
) -> dict[str, str]:
    # Issuing a ticket only needs read. Redemption rechecks permissions/site.
    ticket = issue(
        {
            "user_id": access.user_id,
            "tenant_id": access.tenant_id,
            "provider": provider,
            "resource_id": resource_id,
            "path": path,
        }
    )
    return {
        "url": (
            f"{request.app.state.settings.api_prefix}/plugins/filemanager/targets/"
            f"{provider}/{resource_id}/download-tickets/{ticket}"
        )
    }


@router.get("/download-tickets/{ticket}")
def redeem_download_ticket(
    request: Request, provider: str, resource_id: str, ticket: str
) -> Response:
    access, path = ticket_access(request, provider, resource_id, ticket)
    result = get_file(path, request, access)
    result.headers["Referrer-Policy"] = "no-referrer"
    return result


@router.post("/directories")
def mkdir(body: NewDirectory, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(access.target, "mkdir", docker_environment(), path=body.path)
    audited(request, access, "filemanager.mkdir", body.path)
    return result


@router.patch("/entries")
def rename(body: Rename, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(
        access.target,
        "rename",
        docker_environment(),
        path=body.path,
        name=body.name,
    )
    audited(request, access, "filemanager.rename", body.path)
    return result


@router.delete("/entries")
def remove(path: str, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(access.target, "delete", docker_environment(), path=path)
    audited(request, access, "filemanager.delete", path)
    return result


@router.post("/archives")
def compress(body: Archive, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(
        access.target,
        "zip",
        docker_environment(),
        path=body.path,
        name=body.name,
        entries=body.entries,
    )
    audited(request, access, "filemanager.compress", body.path)
    return result


@router.post("/archives/extract")
def extract(body: Extract, request: Request, access: Access = Depends(authorize)) -> dict:
    result = operation(
        access.target,
        "extract",
        docker_environment(),
        path=body.path,
        destination=body.destination,
    )
    audited(request, access, "filemanager.extract", body.destination)
    return result
