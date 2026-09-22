from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.kernel.events.models import EventLog, EventOutbox
from systutor.kernel.tenants.service import resolve_tenant_scope


@dataclass(slots=True)
class EventEmissionResult:
    event_log: EventLog
    outbox: EventOutbox


def record_event(
    db: Session,
    *,
    event_name: str,
    module: str,
    tenant_id: str | None,
    branch_id: str | None,
    actor_user_id: str | None,
    actor_type: str,
    entity_type: str | None,
    entity_id: str | None,
    correlation_id: str | None,
    causation_id: str | None,
    payload: dict,
    metadata_json: dict,
    version: str = "1",
) -> EventLog:
    effective_tenant_id, effective_branch_id = resolve_tenant_scope(
        db,
        tenant_id=tenant_id,
        branch_id=branch_id,
        actor_user_id=actor_user_id,
    )
    event = EventLog(
        event_name=event_name,
        version=version,
        module=module,
        tenant_id=effective_tenant_id,
        branch_id=effective_branch_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id or str(uuid4()),
        causation_id=causation_id,
        payload=payload,
        metadata_json=metadata_json,
    )
    db.add(event)
    db.flush()
    return event


def create_outbox_event(db: Session, *, event: EventLog, status: str = "pending") -> EventOutbox:
    outbox = EventOutbox(
        event_log_id=event.id,
        event_name=event.event_name,
        tenant_id=event.tenant_id,
        correlation_id=event.correlation_id,
        status=status,
    )
    db.add(outbox)
    db.flush()
    return outbox


def emit_event(db: Session, *, event: EventContract) -> EventEmissionResult:
    event_data = event.model_copy(
        update={
            "correlation_id": event.correlation_id or str(uuid4()),
        }
    )
    event_log = record_event(
        db,
        event_name=event_data.event_name,
        module=event_data.module,
        tenant_id=event_data.tenant_id,
        branch_id=event_data.branch_id,
        actor_user_id=event_data.actor_user_id,
        actor_type=event_data.actor_type,
        entity_type=event_data.entity_type,
        entity_id=event_data.entity_id,
        correlation_id=event_data.correlation_id,
        causation_id=event_data.causation_id,
        payload=dict(event_data.payload),
        metadata_json=dict(event_data.metadata),
        version=event_data.version,
    )
    outbox = create_outbox_event(db, event=event_log)
    return EventEmissionResult(event_log=event_log, outbox=outbox)
