"""Serialized Apps worker; HTTP code never calls these external-I/O handlers."""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from systutor.kernel.auth.models import User

from .crypto import EncryptedValue, TokenCipher
from .models import Application, Deployment, GithubConnection, Operation
from .oauth import OAuthFlow
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

_DEPLOY_LOCK = threading.Lock()


class AppsWorker:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        cipher: TokenCipher,
        *,
        runner_factory: Callable[..., Runner] = Runner,
        publisher: Any = None,
        oauth_flow: OAuthFlow | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cipher = cipher
        self.runner_factory = runner_factory
        self.publisher = publisher
        self.oauth_flow = oauth_flow

    def run(self, operation_id: str) -> None:
        with self.session_factory() as db:
            operation = claim_operation(db, operation_id)
            if operation is None:
                return
            application_id = operation.application_id
            kind = operation.kind
        stopped = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat, args=(operation_id, stopped), daemon=True
        )
        heartbeat.start()
        try:
            if kind in {"inspect", "deploy"}:
                result = self._source_operation(operation_id, application_id, kind)
            else:
                result = self._lifecycle_operation(operation_id, application_id, kind)
            self._finish(operation_id, result)
        except Exception as exc:
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
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if app is None:
                raise RuntimeError("Application not found")
            connection = db.get(GithubConnection, app.source_connection_user_id)
            owner = db.get(User, app.source_connection_user_id)
            if (
                connection is None or connection.reconnect_required or owner is None
                or not owner.is_active or owner.tenant_id != app.tenant_id
            ):
                raise RuntimeError("GitHub reconnect required")
            encrypted = EncryptedValue(
                connection.access_ciphertext, connection.access_nonce, connection.key_version
            )
            refresh_value = (
                EncryptedValue(
                    connection.refresh_ciphertext, connection.refresh_nonce,
                    connection.key_version,
                )
                if connection.refresh_ciphertext is not None
                and connection.refresh_nonce is not None else None
            )
            expires_at = connection.access_expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            expired = expires_at is not None and expires_at <= datetime.now(UTC)
            try:
                token = self.cipher.decrypt(connection.user_id, encrypted) if not expired else ""
            except Exception:
                connection.reconnect_required = True
                db.commit()
                raise RuntimeError("GitHub reconnect required") from None
            source = (
                app.repository, app.branch, app.compose_file, app.runtime_project,
                app.ingress_service, app.ingress_port, app.domain,
            )
            connection_user_id = connection.user_id
            if not expired:
                connection.last_used_at = datetime.now(UTC)
                db.commit()
        if expired:
            if refresh_value is None or self.oauth_flow is None:
                self._require_reconnect(connection_user_id)
                raise RuntimeError("GitHub reconnect required")
            try:
                refresh_token = self.cipher.decrypt(connection_user_id, refresh_value)
                refreshed = self.oauth_flow.refresh(refresh_token)
                token = refreshed.access_token
                access_encrypted = self.cipher.encrypt(connection_user_id, token)
                refresh_encrypted = self.cipher.encrypt(
                    connection_user_id, refreshed.refresh_token or refresh_token
                )
                with self.session_factory() as db:
                    locked = db.get(GithubConnection, connection_user_id, with_for_update=True)
                    if locked is None:
                        raise RuntimeError("GitHub reconnect required")
                    locked.access_ciphertext = access_encrypted.ciphertext
                    locked.access_nonce = access_encrypted.nonce
                    locked.refresh_ciphertext = refresh_encrypted.ciphertext
                    locked.refresh_nonce = refresh_encrypted.nonce
                    locked.key_version = access_encrypted.key_version
                    locked.scopes = list(refreshed.scope)
                    now = datetime.now(UTC)
                    locked.access_expires_at = (
                        now + timedelta(seconds=refreshed.expires_in)
                        if refreshed.expires_in else None
                    )
                    locked.refresh_expires_at = (
                        now + timedelta(seconds=refreshed.refresh_token_expires_in)
                        if refreshed.refresh_token_expires_in else None
                    )
                    locked.last_used_at = now
                    locked.reconnect_required = False
                    db.commit()
            except Exception:
                self._require_reconnect(connection_user_id)
                raise RuntimeError("GitHub reconnect required") from None
        with tempfile.TemporaryDirectory(prefix="spanel-app-worker-") as temporary:
            checkout = Path(temporary) / "checkout"
            clone_env = dict(os.environ, GH_TOKEN=token, GH_PROMPT_DISABLED="1")
            clone_repo(source[0], source[1], checkout, self.runner_factory("local", clone_env))
            token = ""
            audit = audit_project(checkout, source[2], non_root=True)
            report = {
                "compose": audit.compose.relative_to(audit.root).as_posix(),
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

    def _require_reconnect(self, user_id: str) -> None:
        with self.session_factory() as db:
            connection = db.get(GithubConnection, user_id)
            if connection is not None:
                connection.reconnect_required = True
                db.commit()

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
            app = db.get(Application, operation.application_id)
            if app is not None and app.active_operation_id == operation.id:
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
