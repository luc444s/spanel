# ruff: noqa: E501
"""Durable operation creation, idempotency and queue dispatch."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.core.errors import AppError

from .models import Application, Operation

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "interrupted")


class Dispatcher(Protocol):
    def enqueue(self, operation_id: str) -> str: ...


def request_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_operation(
    db: Session,
    *,
    application: Application,
    actor_user_id: str,
    kind: str,
    idempotency_key: str,
    payload: object,
    correlation_id: str | None = None,
) -> tuple[Operation, bool]:
    if not idempotency_key or len(idempotency_key) > 255:
        raise AppError("Idempotency-Key is required", status_code=422, code="apps.idempotency_required")
    digest = request_hash({"application_id": application.id, "kind": kind, "body": payload})
    existing = db.scalar(
        select(Operation).where(
            Operation.actor_user_id == actor_user_id,
            Operation.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise AppError("Idempotency-Key body conflict", status_code=409, code="apps.idempotency_conflict")
        return existing, False
    db.refresh(application, with_for_update=True)
    if application.active_operation_id is not None:
        raise AppError("Application already has an active operation", status_code=409, code="apps.operation_active")
    operation = Operation(
        application_id=application.id,
        tenant_id=application.tenant_id,
        actor_user_id=actor_user_id,
        kind=kind,
        idempotency_key=idempotency_key,
        request_hash=digest,
        correlation_id=correlation_id,
    )
    db.add(operation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Operation).where(
            Operation.actor_user_id == actor_user_id,
            Operation.idempotency_key == idempotency_key,
        ))
        if existing is not None and existing.request_hash == digest:
            return existing, False
        raise AppError(
            "Idempotency-Key body conflict", status_code=409,
            code="apps.idempotency_conflict",
        ) from None
    acquired = db.execute(
        update(Application)
        .where(Application.id == application.id, Application.active_operation_id.is_(None))
        .values(active_operation_id=operation.id)
        .execution_options(synchronize_session=False)
    )
    if acquired.rowcount != 1:
        db.rollback()
        raise AppError(
            "Application already has an active operation", status_code=409,
            code="apps.operation_active",
        )
    db.commit()
    return operation, True


def dispatch_operation(db: Session, operation: Operation, dispatcher: Dispatcher) -> None:
    try:
        message_id = dispatcher.enqueue(operation.id)
    except Exception as exc:
        operation.status = "failed"
        operation.error_code = "apps_queue_unavailable"
        operation.public_error = "Apps queue unavailable"
        operation.finished_at = datetime.now(UTC)
        db.execute(
            update(Application)
            .where(Application.id == operation.application_id)
            .values(active_operation_id=None)
        )
        db.commit()
        raise AppError("Apps queue unavailable", status_code=503, code="apps_queue_unavailable") from exc
    operation.message_id = str(message_id)
    db.commit()


def claim_operation(db: Session, operation_id: str) -> Operation | None:
    operation = db.scalar(select(Operation).where(Operation.id == operation_id).with_for_update())
    if operation is None or operation.status != "queued":
        return None
    operation.status = "running"
    operation.phase = "claim"
    operation.attempt += 1
    operation.heartbeat_at = datetime.now(UTC)
    db.commit()
    return operation


def reconcile(db: Session, dispatcher: Dispatcher, stale_after: int = 300) -> tuple[int, int]:
    requeued = interrupted = 0
    for operation in db.scalars(select(Operation).where(Operation.status.in_(ACTIVE))).all():
        if operation.status == "queued" and not operation.message_id:
            operation.message_id = str(dispatcher.enqueue(operation.id))
            requeued += 1
        elif operation.status == "running" and (
            operation.heartbeat_at is None
            or operation.heartbeat_at < datetime.now(UTC) - timedelta(seconds=stale_after)
        ):
            operation.status = "interrupted"
            operation.error_code = "apps_worker_interrupted"
            operation.finished_at = datetime.now(UTC)
            app = db.get(Application, operation.application_id)
            if app is not None and app.active_operation_id == operation.id:
                app.active_operation_id = None
            interrupted += 1
    db.commit()
    return requeued, interrupted


def write_pending_env(root: Path, operation_id: str, values: list[tuple[str, str]]) -> tuple[Path, list[str], str]:
    names: set[str] = set()
    lines: list[str] = []
    for name, value in values:
        if not name or not name.replace("_", "A").isalnum() or name[0].isdigit():
            raise ValueError("Invalid environment variable name")
        if name in names or any(char in value for char in "\x00\r\n") or len(value) > 8192:
            raise ValueError("Invalid environment variable value")
        names.add(name)
        lines.append(f"{name}={value}")
    pending = root / "pending"
    pending.mkdir(parents=True, exist_ok=True, mode=0o700)
    pending.chmod(0o700)
    path = pending / f"{operation_id}.env"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    content = ("\n".join(lines) + "\n").encode()
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    return path, sorted(names), hashlib.sha256(content).hexdigest()
