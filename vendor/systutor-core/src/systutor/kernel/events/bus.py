from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.core.logging import get_logger
from systutor.kernel.audit.service import record_audit
from systutor.kernel.events.models import EventLog, EventOutbox, utc_now
from systutor.kernel.events.service import emit_event

logger = get_logger(__name__)
EventHandler = Callable[[EventContract], None]


@dataclass(slots=True)
class RegisteredListener:
    event_name: str
    handler: EventHandler
    name: str
    source: str


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[RegisteredListener]] = {}

    def register_listener(
        self,
        event_name: str,
        handler: EventHandler,
        *,
        source: str = "core",
        listener_name: str | None = None,
    ) -> None:
        name = listener_name or f"{source}.{getattr(handler, '__name__', 'listener')}"
        self._listeners.setdefault(event_name, []).append(
            RegisteredListener(event_name=event_name, handler=handler, name=name, source=source)
        )

    def register_handlers(
        self,
        handlers: dict[str, list[EventHandler]],
        *,
        source: str,
    ) -> None:
        for event_name, event_handlers in handlers.items():
            for handler in event_handlers:
                self.register_listener(event_name, handler, source=source)

    def listeners_for(self, event_name: str) -> list[RegisteredListener]:
        return list(self._listeners.get(event_name, []))

    def publish(self, db: Session, *, event: EventContract) -> EventLog:
        emitted = emit_event(db, event=event)
        return emitted.event_log

    def dispatch_pending(
        self,
        db: Session,
        *,
        limit: int = 100,
        max_retries: int = 3,
    ) -> dict[str, int]:
        return dispatch_pending_outbox_events(db, self, limit=limit, max_retries=max_retries)


def build_event_contract(event_log: EventLog) -> EventContract:
    return EventContract(
        event_id=event_log.id,
        event_name=event_log.event_name,
        version=event_log.version,
        occurred_at=event_log.occurred_at,
        module=event_log.module,
        tenant_id=event_log.tenant_id,
        branch_id=event_log.branch_id,
        actor_user_id=event_log.actor_user_id,
        actor_type=event_log.actor_type,
        entity_type=event_log.entity_type,
        entity_id=event_log.entity_id,
        correlation_id=event_log.correlation_id,
        causation_id=event_log.causation_id,
        payload=dict(event_log.payload),
        metadata=dict(event_log.metadata_json),
    )


def dispatch_pending_outbox_events(
    db: Session,
    event_bus: EventBus,
    *,
    limit: int = 100,
    max_retries: int = 3,
) -> dict[str, int]:
    stmt: Select[tuple[EventOutbox]] = (
        select(EventOutbox)
        .where(EventOutbox.status.in_(["pending", "failed"]), EventOutbox.retry_count < max_retries)
        .order_by(EventOutbox.created_at.asc(), EventOutbox.id.asc())
        .limit(limit)
    )
    items = list(db.scalars(stmt))
    processed = 0
    failed = 0

    for item in items:
        event_log = db.get(EventLog, item.event_log_id)
        if event_log is None:
            item.status = "failed"
            item.retry_count += 1
            item.error_message = "event log not found"
            db.add(item)
            failed += 1
            continue

        event = build_event_contract(event_log)
        listeners = event_bus.listeners_for(event.event_name)

        try:
            for listener in listeners:
                listener.handler(event)
            item.status = "processed"
            item.error_message = None
            item.processed_at = utc_now()
            db.add(item)
            processed += 1
        except Exception as exc:  # pragma: no cover - exercised via tests with explicit failure
            item.status = "failed"
            item.retry_count += 1
            item.error_message = str(exc)[:500]
            db.add(item)
            request_id = event.metadata.get("request_id")
            record_audit(
                db,
                tenant_id=event.tenant_id,
                branch_id=event.branch_id,
                actor_user_id=event.actor_user_id,
                actor_type=event.actor_type,
                module=event.module,
                action="event.listener",
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                result="failure",
                correlation_id=event.correlation_id,
                request_id=str(request_id) if request_id else None,
                details={
                    "event_name": event.event_name,
                    "error": str(exc),
                    "listeners": [listener.name for listener in listeners],
                },
            )
            logger.error(
                "event_listener_failed",
                extra={
                    "event_name": event.event_name,
                    "correlation_id": event.correlation_id,
                    "tenant_id": event.tenant_id,
                    "error": str(exc),
                },
            )
            failed += 1

    db.flush()
    return {"processed": processed, "failed": failed, "total": len(items)}
