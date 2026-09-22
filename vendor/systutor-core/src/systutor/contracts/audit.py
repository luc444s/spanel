from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class AuditContract(BaseModel):
    audit_id: str | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    tenant_id: str | None = None
    branch_id: str | None = None
    actor_user_id: str | None = None
    actor_type: str = "system"
    module: str
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    result: str
    correlation_id: str | None = None
    request_id: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
