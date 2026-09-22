from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from systutor.contracts.events import EventContract
from systutor.kernel.audit.service import record_audit
from systutor.kernel.auth.models import User
from systutor.kernel.events.service import emit_event
from systutor.kernel.tenants.context import TenantContext


class PluginRuntimeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plugin_id: str
    name: str
    version: str
    api_version: str
    state: str
    is_enabled: bool
    backend_entrypoint: str | None
    frontend_entrypoint: str | None
    requires_json: list[str]
    permissions_json: list[str]
    events_json: list[str]
    description: str | None
    migration_version: str | None
    installed_at: datetime | None
    enabled_at: datetime | None
    disabled_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CoreActionContext:
    tenant_id: str
    branch_id: str | None
    actor_user_id: str
    correlation_id: str | None
    request_id: str | None


def build_action_context(request: Request, tenant_context: TenantContext) -> CoreActionContext:
    return CoreActionContext(
        tenant_id=tenant_context.current_tenant_id,
        branch_id=tenant_context.current_branch_id,
        actor_user_id=tenant_context.current_user_id,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
    )


def audit_core_action(
    db: Session,
    *,
    context: CoreActionContext,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, object],
    result: str = "success",
) -> None:
    record_audit(
        db,
        tenant_id=context.tenant_id,
        branch_id=context.branch_id,
        actor_user_id=context.actor_user_id,
        actor_type="user",
        module="core",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        result=result,
        correlation_id=context.correlation_id,
        request_id=context.request_id,
        details=details,
    )


def emit_core_event(
    db: Session,
    *,
    context: CoreActionContext,
    event_name: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, object],
) -> None:
    emit_event(
        db,
        event=EventContract(
            event_name=event_name,
            module="core",
            tenant_id=context.tenant_id,
            branch_id=context.branch_id,
            actor_user_id=context.actor_user_id,
            actor_type="user",
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=context.correlation_id,
            payload=payload,
            metadata={"request_id": context.request_id} if context.request_id else {},
        ),
    )


def tenant_not_found(entity_name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity_name} not found")


def handle_integrity_error(exc: IntegrityError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=str(exc.orig) if exc.orig else str(exc),
    )


def plugin_admin_context(request: Request, current_user: User) -> CoreActionContext:
    return CoreActionContext(
        tenant_id=current_user.tenant_id,
        branch_id=current_user.branch_id,
        actor_user_id=current_user.id,
        correlation_id=getattr(request.state, "correlation_id", None),
        request_id=getattr(request.state, "request_id", None),
    )
