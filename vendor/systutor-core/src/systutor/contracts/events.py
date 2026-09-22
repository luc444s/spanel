from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class EventContract(BaseModel):
    event_id: str | None = None
    event_name: str
    version: str = "1"
    occurred_at: datetime = Field(default_factory=utc_now)
    module: str
    tenant_id: str | None = None
    branch_id: str | None = None
    actor_user_id: str | None = None
    actor_type: str = "system"
    entity_type: str | None = None
    entity_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
