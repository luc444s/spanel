"""Serialized Apps worker; HTTP code never calls these external-I/O handlers."""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import audit as record_catalog_audit
from .catalog import mark_installation_suspended, require_source
from .crypto import EncryptedValue, TokenCipher
from .github import GithubClient, UrllibGithubClient, app_jwt_for
from .models import (
    Application,
    Deployment,
    GithubInstallation,
    GithubProvider,
    GithubRepository,
    Operation,
)
from .operations import claim_operation
from .runtime import (
    DeployOptions,
    LifecycleOptions,
    Runner,
    audit_project,
    clone_repo,
    deploy,
    lifecycle,
)
from .runtime.deployment import _publish_release_state
from .service import audit as record_apps_audit

logger = logging.getLogger(__name__)
_DEPLOY_LOCK = threading.Lock()


class AppsWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        cipher: TokenCipher,
        *,
        runner_factory: Callable[..., Runner] = Runner,
        publisher: Any = None,
        github: GithubClient | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cipher = cipher
        self.runner_factory = runner_factory
        self.publisher = publisher
        self.github = github or UrllibGithubClient()

    def run(self, operation_id: str) -> None:
        with self.session_factory() as db:
            operation = claim_operation(db, operation_id)
            if operation is None:
                return
            application_id = operation.application_id
            kind = operation.kind
            subject_type = operation.subject_type
            subject_id = operation.subject_id
        stopped = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat, args=(operation_id, stopped), daemon=True
        )
        heartbeat.start()
        try:
            if kind == "github_sync":
                result = self._sync_operation(operation_id, subject_type, subject_id)
            elif kind in {"inspect", "deploy"}:
                if application_id is None:
                    raise RuntimeError("Application not found")
                result = self._source_operation(operation_id, application_id, kind)
            else:
                if application_id is None:
                    raise RuntimeError("Application not found")
                result = self._lifecycle_operation(operation_id, application_id, kind)
            self._finish(operation_id, result)
        except Exception as exc:
            logger.exception(
                "apps_operation_failed operation_id=%s application_id=%s "
                "operation_kind=%s error_type=%s",
                operation_id,
                application_id,
                kind,
                type(exc).__name__,
                extra={
                    "operation_id": operation_id,
                    "application_id": application_id,
                    "operation_kind": kind,
                    "error_type": type(exc).__name__,
                },
            )
            self._fail(operation_id, exc)
        finally:
            stopped.set()
            heartbeat.join(timeout=1)
            self._pending_path(operation_id).unlink(missing_ok=True)

    def _heartbeat(self, operation_id: str, stopped: threading.Event) -> None:
        while not stopped.wait(30):
            with self.session_factory() as db:
                operation = db.get(Operation, operation_id)
                if operation is None or operation.status != "running":
                    return
                operation.heartbeat_at = datetime.now(UTC)
                db.commit()

    def _pending_path(self, operation_id: str) -> Path:
        root = Path(os.environ.get("SPANEL_APPS_STATE_ROOT", "/var/lib/spanel/apps"))
        return root / "pending" / f"{operation_id}.env"

    @staticmethod
    def _transport() -> str:
        value = os.environ.get("SPANEL_DOCKER_TRANSPORT", "local").strip()
        return value if value in {"local", "ssh"} else "local"

    def _source_operation(self, operation_id: str, application_id: str, kind: str) -> dict:
        from systutor.core.errors import AppError

        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if app is None:
                raise RuntimeError("Application not found")
            try:
                installation, repository, provider = require_source(db, app)
            except AppError:
                self._mark_reconnect(application_id)
                raise RuntimeError("GitHub reconnect required") from None
            try:
                pem = self.cipher.decrypt_scoped(
                    "github-app-private-key", provider.tenant_id, provider.id,
                    EncryptedValue(
                        provider.private_key_ciphertext, provider.private_key_nonce,
                        provider.key_version,
                    ),
                )
                jwt = app_jwt_for(pem, provider.github_app_id)
                token_value = self.github.installation_token(
                    jwt, installation.github_installation_id,
                    repository_id=repository.github_repository_id,
                    permissions={"contents": "read"},
                )
            except Exception:
                self._mark_reconnect(application_id)
                raise RuntimeError("GitHub reconnect required") from None
            provider.last_used_at = datetime.now(UTC)
            db.commit()
            token = token_value.token
            source = (
                repository.full_name, app.branch, app.compose_file, app.runtime_project,
                app.ingress_service, app.ingress_port, app.domain,
            )
        with tempfile.TemporaryDirectory(prefix="spanel-app-worker-") as temporary:
            checkout = Path(temporary) / "checkout"
            clone_env = dict(os.environ, GH_TOKEN=token, GH_PROMPT_DISABLED="1")
            clone_repo(source[0], source[1], checkout, self.runner_factory("local", clone_env))
            token = ""
            audit = audit_project(checkout, source[2], non_root=True)
            report = {
                "compose": audit.compose.relative_to(audit.root).as_posix(),
                "services": sorted(audit.data["services"]),
                "findings": [finding.__dict__ for finding in audit.findings],
                "blocking": bool(audit.errors),
            }
            if kind == "inspect" or audit.errors:
                return report
            env_file = self._pending_path(operation_id)
            runner = self.runner_factory(self._transport())
            with self.session_factory() as db:
                current_app = db.get(Application, application_id)
                previous = (
                    db.get(Deployment, current_app.active_deployment_id)
                    if current_app is not None and current_app.active_deployment_id else None
                )
                previous_release = Path(previous.release) if previous and previous.release else None
            if not _DEPLOY_LOCK.acquire(blocking=False):
                raise RuntimeError("Another build or deployment is active")
            try:
                try:
                    manifest = deploy(
                        DeployOptions(
                            repo=source[0], branch=source[1], project=source[3], non_root=True,
                            env_file=env_file if env_file.exists() else None,
                            release_id=operation_id,
                            publish_state=False,
                        ),
                        audit,
                        runner,
                    )
                except Exception:
                    if previous_release is not None:
                        try:
                            lifecycle(
                                LifecycleOptions(
                                    command="start", project=source[3], release=previous_release
                                ),
                                runner,
                            )
                        except Exception:
                            with self.session_factory() as db:
                                failed_app = db.get(Application, application_id)
                                if failed_app is not None:
                                    failed_app.status = "degraded"
                                    db.commit()
                    raise
            finally:
                _DEPLOY_LOCK.release()
            if manifest is None:
                raise RuntimeError("Deployment produced no manifest")
            if source[6]:
                try:
                    service = source[4]
                    port = source[5]
                    if not service or service not in manifest.get("containers", {}):
                        raise RuntimeError(
                            "Ingress service did not resolve to exactly one container"
                        )
                    if port is None or not 1 <= port <= 65535:
                        raise RuntimeError("Ingress port is invalid")
                    if self.publisher is None:
                        raise RuntimeError("Domain publisher is unavailable")
                    self.publisher.publish(
                        manifest["containers"][service], source[6], port=port
                    )
                except Exception:
                    rollback_ok = True
                    try:
                        lifecycle(
                            LifecycleOptions(
                                command="down", project=source[3],
                                release=Path(str(manifest["release"])),
                            ),
                            runner,
                        )
                        if previous_release is not None and previous is not None:
                            lifecycle(
                                LifecycleOptions(
                                    command="start", project=source[3], release=previous_release
                                ),
                                runner,
                            )
                            previous_containers = (previous.manifest or {}).get("containers", {})
                            if (
                                previous.ingress_service in previous_containers
                                and previous.ingress_port
                            ):
                                self.publisher.publish(
                                    previous_containers[previous.ingress_service], source[6],
                                    port=previous.ingress_port,
                                )
                    except Exception:
                        rollback_ok = False
                    if not rollback_ok:
                        with self.session_factory() as db:
                            failed_app = db.get(Application, application_id)
                            if failed_app is not None:
                                failed_app.status = "degraded"
                                db.commit()
                    raise
            _publish_release_state(runner, Path(str(manifest["release"])), manifest)
            with self.session_factory() as db:
                deployment = Deployment(
                    application_id=application_id,
                    operation_id=operation_id,
                    repository=source[0],
                    branch=source[1],
                    commit=manifest.get("commit"),
                    tree_hash=manifest.get("tree_hash"),
                    compose_file=manifest.get("compose"),
                    ingress_service=source[4],
                    ingress_port=source[5],
                    status="running",
                    release=manifest.get("release"),
                    manifest=manifest,
                    finished_at=datetime.now(UTC),
                )
                db.add(deployment)
                db.flush()
                app = db.get(Application, application_id)
                if app is not None:
                    app.active_deployment_id = deployment.id
                    app.status = "running"
                db.commit()
            return {"deployment_id": deployment.id, "status": "running"}

    def _mark_reconnect(self, application_id: str) -> None:
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if app is not None:
                app.source_status = "source_reconnect_required"
                db.commit()

    def _sync_operation(
        self, operation_id: str, subject_type: str | None, subject_id: str | None
    ) -> dict:
        if subject_type != "installation" or not subject_id:
            raise RuntimeError("GitHub sync subject is invalid")
        with self.session_factory() as db:
            operation = db.get(Operation, operation_id)
            installation = db.get(GithubInstallation, subject_id)
            if operation is None or installation is None:
                raise RuntimeError("GitHub sync installation not found")
            if installation.tenant_id != operation.tenant_id:
                raise RuntimeError("GitHub sync installation not found")
            provider = db.get(GithubProvider, installation.provider_id)
            if provider is None or provider.tenant_id != installation.tenant_id:
                raise RuntimeError("GitHub provider not found")
            if installation.status != "active" or provider.status != "active":
                mark_installation_suspended(db, installation, installation.status)
                db.commit()
                raise RuntimeError("GitHub installation is not active")
            try:
                pem = self.cipher.decrypt_scoped(
                    "github-app-private-key", provider.tenant_id, provider.id,
                    EncryptedValue(
                        provider.private_key_ciphertext, provider.private_key_nonce,
                        provider.key_version,
                    ),
                )
                jwt = app_jwt_for(pem, provider.github_app_id)
                token_value = self.github.installation_token(
                    jwt, installation.github_installation_id,
                    permissions={"metadata": "read"},
                )
            except Exception:
                raise RuntimeError("GitHub sync authentication failed") from None
            token = token_value.token
            provider.last_used_at = datetime.now(UTC)
            github_id = installation.github_installation_id
            tenant_id = installation.tenant_id
            installation_id = installation.id
        observed: dict[int, dict] = {}
        page = 1
        try:
            while page <= 100:
                data = self.github.installation_repositories(token, page)
                rows = data.get("repositories")
                if not isinstance(rows, list):
                    raise RuntimeError("GitHub repositories response is invalid")
                if not rows:
                    break
                for row in rows:
                    repository = _repository_row(row)
                    observed[repository["github_repository_id"]] = repository
                page += 1
        finally:
            token = ""
        if page > 100:
            raise RuntimeError("GitHub repositories pagination exceeded the limit")
        now = datetime.now(UTC)
        with self.session_factory() as db:
            installation = db.get(GithubInstallation, installation_id)
            if installation is None:
                raise RuntimeError("GitHub installation disappeared")
            provider = db.get(GithubProvider, installation.provider_id)
            existing = {
                row.github_repository_id: row
                for row in db.scalars(select(GithubRepository).where(
                    GithubRepository.installation_id == installation_id
                )).all()
            }
            seen: set[int] = set()
            for github_repository_id, values in observed.items():
                seen.add(github_repository_id)
                row = existing.get(github_repository_id)
                if row is None:
                    row = GithubRepository(
                        installation_id=installation_id, tenant_id=tenant_id,
                        github_repository_id=github_repository_id,
                        full_name=values["full_name"],
                    )
                    db.add(row)
                row.full_name = values["full_name"]
                row.private = values["private"]
                row.default_branch = values["default_branch"]
                row.archived = values["archived"]
                row.disabled = values["disabled"]
                row.observed_at = now
                row.removed_at = None
            for github_repository_id, row in existing.items():
                if github_repository_id not in seen and row.removed_at is None:
                    row.removed_at = now
                    apps = db.scalars(select(Application).where(
                        Application.tenant_id == tenant_id,
                        Application.source_repository_id == row.id,
                        Application.deleted_at.is_(None),
                    )).all()
                    for app in apps:
                        app.source_status = "source_reconnect_required"
            installation.last_synced_at = now
            installation.status = "active"
            if provider is not None:
                provider.last_used_at = now
            record_catalog_audit(
                db, "github.sync.completed", tenant_id=tenant_id,
                actor=None, entity_id=installation_id,
                details={"github_installation_id": github_id, "repositories": len(seen)},
            )
            db.commit()
        return {
            "installation_id": installation_id,
            "github_installation_id": github_id,
            "repositories": len(seen),
        }

    def _lifecycle_operation(self, operation_id: str, application_id: str, kind: str) -> dict:
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if app is None or not app.active_deployment_id:
                raise RuntimeError("Active deployment not found")
            deployment = db.get(Deployment, app.active_deployment_id)
            if deployment is None or not deployment.manifest:
                raise RuntimeError("Owned deployment manifest not found")
            project, domain = app.runtime_project, app.domain
            operation = db.get(Operation, operation_id)
            purge_data = bool((operation.report or {}).get("purge_data")) if operation else False
        command = "status" if kind == "refresh" else kind
        result = lifecycle(
            LifecycleOptions(
                command=command, project=project,
                release=Path(deployment.release) if deployment.release else None,
                purge_data=purge_data if kind == "delete" else False,
                confirm_purge=project if kind == "delete" and purge_data else None,
            ),
            self.runner_factory(self._transport()),
        )
        if domain and self.publisher is not None:
            if kind in {"stop", "down", "delete"}:
                self.publisher.unpublish(domain)
            elif kind in {"start", "restart"}:
                containers = deployment.manifest.get("containers", {})
                app_service = deployment.ingress_service
                if app_service and app_service in containers and deployment.ingress_port:
                    self.publisher.publish(
                        containers[app_service], domain, port=deployment.ingress_port
                    )
        if kind == "delete":
            release = Path(str(deployment.manifest.get("release", "")))
            project_root = (
                release.parent.parent if release.parent.name == "releases" else release
            )
            if project_root.name == project:
                runner = self.runner_factory(self._transport())
                if runner.transport == "ssh":
                    runner.run(["rm", "-rf", "--", str(project_root)])
                elif project_root.exists():
                    shutil.rmtree(project_root)
            with self.session_factory() as db:
                app = db.get(Application, application_id)
                if app is not None:
                    app.deleted_at = datetime.now(UTC)
                    app.status = "deleted"
                    db.commit()
        return {
            "application_id": application_id, "operation_id": operation_id,
            "action": kind, "output": result.output[-65536:],
        }

    def _finish(self, operation_id: str, result: dict) -> None:
        with self.session_factory() as db:
            operation = db.get(Operation, operation_id)
            if operation is None:
                return
            operation.status = "succeeded"
            operation.phase = "complete"
            operation.result = result
            operation.finished_at = datetime.now(UTC)
            if operation.application_id is not None:
                app = db.get(Application, operation.application_id)
                if app is not None:
                    if operation.kind == "inspect":
                        app.status = "blocked" if result.get("blocking") else "inspected"
                        if not app.compose_file and result.get("compose"):
                            app.compose_file = str(result["compose"])
                    if app.active_operation_id == operation.id:
                        app.active_operation_id = None
            record_apps_audit(
                db, f"operation.{operation.kind}.complete", tenant_id=operation.tenant_id,
                actor=operation.actor_user_id, entity_id=operation.application_id,
                details={"operation_id": operation.id},
            )
            db.commit()

    def _fail(self, operation_id: str, exc: Exception) -> None:
        with self.session_factory() as db:
            operation = db.get(Operation, operation_id)
            if operation is None:
                return
            operation.status = "failed"
            operation.phase = "failed"
            operation.error_code = "apps_operation_failed"
            operation.public_error = "Application operation failed"
            operation.finished_at = datetime.now(UTC)
            if operation.application_id is not None:
                app = db.get(Application, operation.application_id)
                if app is not None and app.active_operation_id == operation.id:
                    app.active_operation_id = None
            record_apps_audit(
                db, f"operation.{operation.kind}.failed", tenant_id=operation.tenant_id,
                actor=operation.actor_user_id, entity_id=operation.application_id,
                result="failure",
                details={"operation_id": operation.id, "code": operation.error_code},
            )
            db.commit()


def build_actor(handler: Callable[[str], None]):
    dramatiq = importlib.import_module("dramatiq")
    return dramatiq.actor(
        actor_name="apps_operation", queue_name="apps", max_retries=0
    )(handler)


def _repository_row(value: object) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError("GitHub repository metadata is invalid")
    repository_id = value.get("id")
    full_name = value.get("full_name")
    if (
        isinstance(repository_id, bool) or not isinstance(repository_id, int)
        or repository_id <= 0 or not isinstance(full_name, str)
        or not full_name or len(full_name) > 510
    ):
        raise RuntimeError("GitHub repository metadata is invalid")
    default_branch = value.get("default_branch")
    return {
        "github_repository_id": repository_id,
        "full_name": full_name,
        "private": bool(value.get("private")),
        "default_branch": default_branch if isinstance(default_branch, str) else None,
        "archived": bool(value.get("archived")),
        "disabled": bool(value.get("disabled")),
    }
