"""Private backup storage and thin orchestration of the validated site scripts."""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from systutor.api.deps import get_db_session
from systutor.core.errors import AppError
from systutor.core.lifecycle import ensure_session_factory
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.dependencies import get_current_tenant_context, require_permission
from systutor.kernel.auth.models import User
from systutor.kernel.tenants.context import TenantContext, build_tenant_context
from systutor.kernel.tenants.service import validate_user_branch_scope

from . import backup_tickets
from .database_binding import resolve_database_binding
from .guard import get_owned_site
from .inspection import inspect_container
from .models import HostingSite
from .schemas import BackupExportResponse, BackupImportResponse
from .settings import HostingSettings, docker_env, get_hosting_settings

logger = logging.getLogger(__name__)
backup_router = APIRouter(prefix="/sites", tags=["hosting"])
_CHUNK_SIZE = 1024 * 1024
_PURGE_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class ExportResult:
    ticket: str
    filename: str


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
Inspector = Callable[..., dict | None]


def _default_runner(
    argv: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, env=env)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _remove_private(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


class BackupService:
    """Coordinate scripts without reproducing their Docker/ZIP/SQL engine."""

    def __init__(
        self,
        settings: HostingSettings | None = None,
        *,
        runner: Runner = _default_runner,
        inspector: Inspector = inspect_container,
    ) -> None:
        self.settings = settings or get_hosting_settings()
        self.runner = runner
        self.inspector = inspector
        self.root = Path(self.settings.backups_dir).expanduser().resolve()

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def imports(self) -> Path:
        return self.root / "imports"

    @property
    def safety(self) -> Path:
        return self.root / "safety"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    @property
    def failed(self) -> Path:
        return self.root / "failed"

    def initialize(self) -> None:
        for directory in (
            self.root,
            self.exports,
            self.imports,
            self.safety,
            self.locks,
            self.failed,
        ):
            _private_directory(directory)

    def purge_expired(self, now: float | None = None) -> None:
        """Purge temporary exports/imports only; safety and failed are retained."""
        self.initialize()
        cutoff = (now if now is not None else time.time()) - (
            self.settings.backup_link_ttl + _PURGE_MARGIN_SECONDS
        )
        for parent in (self.exports, self.imports):
            for path in parent.iterdir():
                try:
                    if path.lstat().st_mtime < cutoff:
                        _remove_private(path)
                except FileNotFoundError:
                    continue

    def _require_local(self) -> None:
        if self.settings.docker_transport != "local":
            raise AppError(
                "Backups require the local Docker transport",
                status_code=503,
                code="hosting_backup_transport_unsupported",
            )

    def _script(self, name: str) -> Path:
        scripts = Path(self.settings.backup_scripts_dir).expanduser().resolve()
        candidate = scripts / name
        if candidate.parent != scripts or not candidate.is_file():
            raise AppError(
                "The backup runtime is not available",
                status_code=503,
                code="hosting_backup_runtime_unavailable",
            )
        return candidate

    def _require_running(self, site: HostingSite) -> dict:
        try:
            info = self.inspector(
                docker_env(self.settings),
                site.container_name,
                timeout=self.settings.command_timeout,
            )
        except Exception as exc:
            raise AppError(
                "Could not check the site status",
                status_code=503,
                code="hosting_backup_runtime_unavailable",
            ) from exc
        state = info.get("State") if isinstance(info, dict) else None
        state = state if isinstance(state, dict) else {}
        if state.get("Status") != "running" and state.get("Running") is not True:
            raise AppError(
                "The site must be running",
                status_code=409,
                code="hosting_backup_site_not_running",
            )
        return info if isinstance(info, dict) else {}

    @contextmanager
    def _site_lock(self, site_id: str) -> Iterator[None]:
        safe_id = site_id if re.fullmatch(r"[A-Za-z0-9_.-]+", site_id) else hashlib.sha256(
            site_id.encode("utf-8")
        ).hexdigest()
        lock_path = self.locks / f"{safe_id}.lock"
        handle = lock_path.open("a+b")
        lock_path.chmod(0o600)
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AppError(
                    "A backup operation is already in progress for this site",
                    status_code=409,
                    code="hosting_backup_busy",
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _run(
        self, argv: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            if env is None:
                return self.runner(argv)
            return self.runner(argv, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AppError(
                "The backup runtime is not available",
                status_code=503,
                code="hosting_backup_runtime_unavailable",
            ) from exc

    def _database_args(
        self, site: HostingSite, info: dict
    ) -> tuple[list[str], dict[str, str] | None]:
        """Resolve the DB the same way the panel does and hand it to the script.

        The script is standalone, so it cannot import the plugin's resolver.
        The app resolves environment → `wp-config.php` → network alias and
        passes the result explicitly. The password travels in the child
        environment, never in argv.
        """
        if getattr(site, "kind", "") != "wordpress":
            return [], None
        env = docker_env(self.settings)
        try:
            raw_host, db_container, database, user, password = resolve_database_binding(
                env,
                site.container_name,
                info,
                inspector=lambda name: self.inspector(
                    env, name, timeout=self.settings.command_timeout
                ),
                timeout=self.settings.command_timeout,
            )
        except AppError as exc:
            logger.warning(
                "hosting_backup_db_unresolved container=%s error=%s",
                site.container_name,
                exc,
            )
            return [], None
        return (
            [
                "--kind",
                "wp",
                "--path",
                "/var/www/html",
                "--db-host",
                raw_host,
                "--db-container",
                db_container,
                "--db-user",
                user,
                "--db-name",
                database,
            ],
            {**os.environ, "SPANEL_BACKUP_DB_PASSWORD": password},
        )

    def export(self, site: HostingSite, *, user_id: str, tenant_id: str) -> ExportResult:
        self._require_local()
        backup_tickets.require_store()
        try:
            self.purge_expired()
            with self._site_lock(site.id):
                info = self._require_running(site)
                backup_id = secrets.token_hex(16)
                directory = self.exports / backup_id
                _private_directory(directory)
                try:
                    db_args, run_env = self._database_args(site, info)
                    argv = [
                        sys.executable,
                        str(self._script("site_backup.py")),
                        "--container",
                        site.container_name,
                        "--out",
                        str(directory),
                        *db_args,
                    ]
                    result = self._run(argv, env=run_env)
                    candidates = list(directory.iterdir())
                    valid = [
                        path
                        for path in candidates
                        if path.suffix.lower() == ".zip"
                        and not path.is_symlink()
                        and path.is_file()
                    ]
                    if result.returncode != 0 or len(candidates) != 1 or len(valid) != 1:
                        logger.warning(
                            "hosting_backup_export_failed container=%s returncode=%s "
                            "candidates=%s valid=%s stderr=%r stdout=%r",
                            site.container_name,
                            result.returncode,
                            [path.name for path in candidates],
                            [path.name for path in valid],
                            result.stderr[-4000:],
                            result.stdout[-4000:],
                        )
                        raise AppError(
                            "Could not generate the backup",
                            status_code=503,
                            code="hosting_backup_runtime_unavailable",
                        )
                    archive = valid[0]
                    archive.chmod(0o600)
                    payload = {
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "site_id": site.id,
                        "backup_id": backup_id,
                        "filename": archive.name,
                    }
                    ticket = backup_tickets.issue(payload, self.settings.backup_link_ttl)
                except Exception:
                    _remove_private(directory)
                    raise
                return ExportResult(ticket=ticket, filename=archive.name)
        except AppError:
            raise
        except OSError as exc:
            raise AppError(
                "Backup storage is not available",
                status_code=503,
                code="hosting_backup_runtime_unavailable",
            ) from exc

    def _write_upload(self, source: BinaryIO, destination: Path) -> None:
        part = destination.with_suffix(".part")
        written = 0
        try:
            with part.open("xb") as target:
                while chunk := source.read(_CHUNK_SIZE):
                    written += len(chunk)
                    if written > self.settings.backup_max_upload_bytes:
                        raise AppError(
                            "The backup exceeds the allowed size",
                            status_code=413,
                            code="hosting_backup_upload_too_large",
                        )
                    target.write(chunk)
            part.chmod(0o600)
            os.replace(part, destination)
            destination.chmod(0o600)
        finally:
            part.unlink(missing_ok=True)

    def _secure_new_safety(self, previous: set[str]) -> list[Path]:
        created = [path for path in self.safety.iterdir() if path.name not in previous]
        for root in created:
            if root.is_symlink():
                continue
            for directory, _names, files in os.walk(root):
                Path(directory).chmod(0o700)
                for name in files:
                    (Path(directory) / name).chmod(0o600)
        return created

    def _preserve_failure(self, incoming: Path, safety: list[Path]) -> str:
        reference = secrets.token_urlsafe(12)
        destination = self.failed / reference
        _private_directory(destination)
        os.replace(incoming, destination / "incoming.zip")
        (destination / "incoming.zip").chmod(0o600)
        if safety:
            safety_destination = destination / "safety"
            _private_directory(safety_destination)
            for path in safety:
                if path.exists() and not path.is_symlink():
                    shutil.move(str(path), safety_destination / path.name)
        return reference

    def restore(
        self,
        site: HostingSite,
        *,
        filename: str,
        confirmation: str,
        source: BinaryIO,
    ) -> None:
        self._require_local()
        if not filename.lower().endswith(".zip"):
            raise AppError(
                "Select a .zip file",
                status_code=422,
                code="hosting_backup_invalid",
            )
        if confirmation != site.container_name:
            raise AppError(
                "The confirmation does not match the container",
                status_code=422,
                code="hosting_backup_confirmation_mismatch",
            )
        incoming: Path | None = None
        preserved = False
        try:
            self.purge_expired()
            with self._site_lock(site.id):
                self._require_running(site)
                incoming = self.imports / f"{secrets.token_hex(16)}.zip"
                self._write_upload(source, incoming)
                previous_safety = {path.name for path in self.safety.iterdir()}
                argv = [
                    sys.executable,
                    str(self._script("site_restore.py")),
                    "--zip",
                    str(incoming),
                    "--container",
                    site.container_name,
                    "--yes",
                    "--safety-out",
                    str(self.safety),
                ]
                result = self._run(argv)
                new_safety = self._secure_new_safety(previous_safety)
                if result.returncode == 0:
                    return
                if result.returncode == 2:
                    raise AppError(
                        "The backup is not valid for this site",
                        status_code=422,
                        code="hosting_backup_invalid",
                    )
                if result.returncode == 1:
                    raise AppError(
                        "Could not restore the backup; the site remains in a safe state",
                        status_code=409,
                        code="hosting_backup_restore_failed",
                    )
                if result.returncode == 3:
                    reference = self._preserve_failure(incoming, new_safety)
                    preserved = True
                    raise AppError(
                        f"Rollback failed. Reference: {reference}",
                        status_code=500,
                        code="hosting_backup_rollback_failed",
                    )
                raise AppError(
                    "The backup runtime is not available",
                    status_code=503,
                    code="hosting_backup_runtime_unavailable",
                )
        except AppError:
            raise
        except OSError as exc:
            raise AppError(
                "The backup runtime is not available",
                status_code=503,
                code="hosting_backup_runtime_unavailable",
            ) from exc
        finally:
            if incoming is not None and not preserved:
                incoming.unlink(missing_ok=True)

    def download_path(self, backup_id: str, filename: str) -> tuple[Path, Path] | None:
        if not re.fullmatch(r"[a-f0-9]{32}", backup_id) or Path(filename).name != filename:
            return None
        directory = self.exports / backup_id
        archive = directory / filename
        try:
            resolved_exports = self.exports.resolve(strict=True)
            resolved_directory = directory.resolve(strict=True)
            resolved_archive = archive.resolve(strict=True)
            resolved_directory.relative_to(resolved_exports)
            resolved_archive.relative_to(resolved_directory)
        except (OSError, ValueError):
            return None
        if directory.is_symlink() or archive.is_symlink() or not resolved_archive.is_file():
            return None
        return resolved_archive, resolved_directory


def initialize_backup_storage(settings: HostingSettings | None = None) -> None:
    service = BackupService(settings)
    service.purge_expired()


def _service() -> BackupService:
    return BackupService()


def _audit(
    db: Session,
    request: Request,
    site_id: str,
    context: TenantContext,
    action: str,
    result: str,
    details: dict[str, str] | None = None,
) -> None:
    record_audit(
        db,
        tenant_id=context.current_tenant_id,
        branch_id=context.current_branch_id,
        actor_user_id=context.current_user_id,
        actor_type="user",
        module="hosting",
        action=action,
        entity_type="hosting_site",
        entity_id=site_id,
        result=result,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
        details=details or {},
    )
    db.commit()


def _audit_failed_import(
    db: Session, request: Request, site: HostingSite, context: TenantContext, code: str
) -> None:
    try:
        _audit(
            db,
            request,
            site.id,
            context,
            "hosting.site.backup_import_failed",
            "failure",
            {"code": code, "result": "failed"},
        )
    except Exception:
        db.rollback()
        logger.exception("hosting_backup_import_failure_audit_failed", extra={"code": code})


@backup_router.post("/{site_id}/backup/export", response_model=BackupExportResponse)
def export_backup(
    request: Request,
    response: Response,
    site: HostingSite = Depends(get_owned_site),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("hosting.sites.manage")),
    db: Session = Depends(get_db_session),
    service: BackupService = Depends(_service),
) -> BackupExportResponse:
    db.commit()
    result = service.export(
        site, user_id=context.current_user_id, tenant_id=context.current_tenant_id
    )
    _audit(db, request, site.id, context, "hosting.site.backup_exported", "success")
    response.headers["Cache-Control"] = "no-store"
    return BackupExportResponse(
        url=f"/api/v1/plugins/hosting/sites/{site.id}/backup/download/{result.ticket}",
        expires_in=service.settings.backup_link_ttl,
        filename=result.filename,
    )


@backup_router.post("/{site_id}/backup/import", response_model=BackupImportResponse)
def import_backup(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    confirmation: Annotated[str, Form()],
    site: HostingSite = Depends(get_owned_site),
    context: TenantContext = Depends(get_current_tenant_context),
    _user: User = Depends(require_permission("hosting.sites.manage")),
    db: Session = Depends(get_db_session),
    service: BackupService = Depends(_service),
) -> BackupImportResponse:
    db.commit()
    try:
        service.restore(
            site,
            filename=file.filename or "",
            confirmation=confirmation,
            source=file.file,
        )
    except AppError as exc:
        _audit_failed_import(db, request, site, context, exc.code)
        raise
    _audit(db, request, site.id, context, "hosting.site.backup_imported", "success")
    response.headers["Cache-Control"] = "no-store"
    return BackupImportResponse(
        site_id=site.id, container_name=site.container_name, restored=True
    )


def _expired() -> HTTPException:
    return HTTPException(status_code=401, detail="Descarga expirada")


@backup_router.get("/{site_id}/backup/download/{ticket}", response_class=FileResponse)
def download_backup(request: Request, site_id: str, ticket: str) -> FileResponse:
    payload = backup_tickets.consume(ticket)
    required = {"user_id", "tenant_id", "site_id", "backup_id", "filename"}
    if payload is None or set(payload) != required or payload["site_id"] != site_id:
        raise _expired()
    with ensure_session_factory(request.app)() as db:
        user = db.get(User, payload["user_id"])
        if (
            user is None
            or not user.is_active
            or user.tenant_id != payload["tenant_id"]
            or not validate_user_branch_scope(db, user)
        ):
            raise _expired()
        context = build_tenant_context(db, user)
        if (
            context.current_tenant_id != payload["tenant_id"]
            or not context.has_permission("hosting.sites.manage")
            or db.scalar(
                select(HostingSite.id).where(
                    HostingSite.id == site_id,
                    HostingSite.tenant_id == payload["tenant_id"],
                )
            )
            is None
        ):
            raise _expired()
        service = BackupService()
        resolved = service.download_path(payload["backup_id"], payload["filename"])
        if resolved is None:
            raise _expired()
        archive, directory = resolved
        _audit(
            db,
            request,
            site_id,
            context,
            "hosting.site.backup_downloaded",
            "success",
            {"result": "ticket_consumed"},
        )
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=payload["filename"],
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(_remove_private, directory),
    )
